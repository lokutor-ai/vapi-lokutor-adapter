from __future__ import annotations

import asyncio
import hmac
import json
import logging

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .audio import VAPI_SAMPLE_RATES, stereo_to_customer_mono, wav_to_raw_pcm
from .config import Settings

log = logging.getLogger("vapi_lokutor_adapter")


def _authorized(settings: Settings, headers) -> bool:
    if not settings.adapter_secret:
        return True
    expected = settings.adapter_secret
    candidates = [headers.get("x-vapi-secret", "")]
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        candidates.append(auth[7:])
    return any(hmac.compare_digest(c, expected) for c in candidates if c)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    app = FastAPI(title="vapi-lokutor-adapter")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # ---- Custom voice: Vapi POSTs {"message": {"type": "voice-request", ...}} -------------
    @app.post("/api/synthesize")
    async def synthesize(request: Request):
        if not _authorized(cfg, request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        msg = body.get("message") if isinstance(body, dict) else None
        if not isinstance(msg, dict) or msg.get("type") != "voice-request":
            return JSONResponse({"error": "Invalid request"}, status_code=400)
        text = (msg.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "Missing text"}, status_code=400)
        rate = msg.get("sampleRate")
        if rate not in VAPI_SAMPLE_RATES:
            return JSONResponse(
                {"error": "Unsupported sample rate", "supportedRates": list(VAPI_SAMPLE_RATES)},
                status_code=400,
            )
        # The voice-request carries no voice id, so the voice and language come from the
        # server URL's query string (?voice=F2&language=es), then from the environment.
        voice = request.query_params.get("voice", cfg.default_voice)
        language = request.query_params.get("language", cfg.default_language)

        try:
            async with httpx.AsyncClient(timeout=cfg.tts_timeout_s) as client:
                r = await client.post(
                    f"{cfg.lokutor_base_url.rstrip('/')}/v1/tts/synthesize",
                    headers={"Authorization": f"Bearer {cfg.lokutor_api_key}"},
                    json={"text": text, "voice": voice, "language": language},
                )
        except httpx.TimeoutException:
            return JSONResponse({"error": "Synthesis timed out"}, status_code=408)
        except httpx.HTTPError:
            return JSONResponse({"error": "Lokutor unreachable"}, status_code=502)

        if r.status_code != 200:
            log.warning("lokutor tts status=%s", r.status_code)
            headers = {}
            if r.status_code == 503 and "retry-after" in r.headers:
                headers["Retry-After"] = r.headers["retry-after"]
            # 4xx from Lokutor (bad voice, no balance) is surfaced as 502 so Vapi's
            # fallbackPlan can take over; the detail is logged, never the key.
            return JSONResponse(
                {"error": "Lokutor synthesis failed", "upstream_status": r.status_code},
                status_code=502 if r.status_code != 503 else 503,
                headers=headers,
            )
        try:
            pcm = wav_to_raw_pcm(r.content, rate)
        except Exception:
            return JSONResponse({"error": "Unreadable audio from Lokutor"}, status_code=502)
        return Response(
            content=pcm,
            media_type="application/octet-stream",
            headers={"Content-Length": str(len(pcm))},
        )

    # ---- Custom transcriber: Vapi opens a WebSocket, sends start + stereo PCM ------------
    @app.websocket("/api/custom-transcriber")
    async def transcriber(ws: WebSocket):
        if not _authorized(cfg, ws.headers):
            await ws.close(code=1008)
            return
        await ws.accept()
        language = ws.query_params.get("language", "")  # "" = let Lokutor detect
        upstream_url = f"{cfg.ws_base_url}/ws/stt"
        try:
            async with websockets.connect(
                upstream_url,
                additional_headers={"Authorization": f"Bearer {cfg.lokutor_api_key}"},
                max_size=None,
            ) as up:
                await up.send(json.dumps({"language": language}))
                await _pump(ws, up)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # upstream refused (402/429) or dropped
            log.warning("transcriber upstream error: %s", type(exc).__name__)
            try:
                await ws.send_text(json.dumps({"type": "error", "error": "Transcription service error"}))
                await ws.close(code=1011)
            except Exception:
                pass

    async def _pump(ws: WebSocket, up) -> None:
        async def vapi_to_lokutor():
            started = False
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    break
                if m.get("text") is not None:
                    try:
                        data = json.loads(m["text"])
                    except ValueError:
                        continue
                    if data.get("type") == "start":
                        started = True
                    continue
                chunk = m.get("bytes")
                if chunk and started:
                    mono = stereo_to_customer_mono(chunk)
                    if mono:
                        await up.send(mono)
            # Vapi closed: ask Lokutor to finalize what is pending.
            try:
                await up.send(json.dumps({"type": "end"}))
            except Exception:
                pass

        async def lokutor_to_vapi():
            async for frame in up:
                if isinstance(frame, bytes):
                    continue
                try:
                    data = json.loads(frame)
                except ValueError:
                    continue
                kind = data.get("type")
                if kind == "transcript" and data.get("data"):
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "transcriber-response",
                                "transcription": data["data"],
                                "channel": "customer",
                                "transcriptType": "final" if data.get("isFinal") else "partial",
                            }
                        )
                    )
                elif kind == "error":
                    await ws.send_text(
                        json.dumps({"type": "error", "error": "Transcription service error"})
                    )
                elif kind == "status" and data.get("data") == "done":
                    break

        t1 = asyncio.create_task(vapi_to_lokutor())
        t2 = asyncio.create_task(lokutor_to_vapi())
        done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        # Vapi side finished: give Lokutor a moment to flush the last final transcript.
        if t1 in done and t2 in pending:
            try:
                await asyncio.wait_for(t2, timeout=3)
            except Exception:
                pass
        for t in (t1, t2):
            if not t.done():
                t.cancel()
        for t in (t1, t2):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    return app


def main() -> None:  # pragma: no cover
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))


if __name__ == "__main__":  # pragma: no cover
    main()
