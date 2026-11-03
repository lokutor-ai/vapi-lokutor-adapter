"""A local stand-in for the two Lokutor endpoints the adapter uses.

POST /v1/tts/synthesize  -> 44.1 kHz mono 16-bit WAV (a 440 Hz tone, 0.5 s)
WS   /ws/stt             -> {"status": ready}; for every 16000-byte (0.5 s) of PCM received
                            a partial, and a final on {"type": "end"}
"""
from __future__ import annotations

import io
import json
import math
import struct
import wave

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response


def tone_wav(rate=44100, seconds=0.5, freq=440.0) -> bytes:
    n = int(rate * seconds)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def create_mock(expected_key="test-key") -> FastAPI:
    app = FastAPI()
    app.state.tts_requests = []
    app.state.stt_pcm = []
    app.state.stt_first = []

    @app.post("/v1/tts/synthesize")
    async def tts(request: Request):
        if request.headers.get("authorization") != f"Bearer {expected_key}":
            return JSONResponse({"code": "auth.invalid_key"}, status_code=401)
        body = await request.json()
        app.state.tts_requests.append(body)
        if body.get("voice") == "BAD":
            return JSONResponse({"code": "validation.invalid_voice"}, status_code=400)
        if body.get("voice") == "BUSY":
            return JSONResponse({"error": "capacity"}, status_code=503, headers={"Retry-After": "2"})
        return Response(tone_wav(), media_type="audio/wav")

    @app.websocket("/ws/stt")
    async def stt(ws: WebSocket):
        if ws.headers.get("authorization") != f"Bearer {expected_key}":
            await ws.close(code=1008)
            return
        await ws.accept()
        first = json.loads(await ws.receive_text())
        app.state.stt_first.append(first)
        await ws.send_text(json.dumps({"type": "status", "data": "ready", "lang": first.get("language") or "en"}))
        received = 0
        try:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    return
                if m.get("bytes") is not None:
                    app.state.stt_pcm.append(m["bytes"])
                    received += len(m["bytes"])
                    if received >= 16000:
                        received = 0
                        await ws.send_text(json.dumps(
                            {"type": "transcript", "data": "hello wor", "isFinal": False, "lang": "en"}))
                elif m.get("text"):
                    if json.loads(m["text"]).get("type") == "end":
                        await ws.send_text(json.dumps(
                            {"type": "transcript", "data": "Hello world.", "isFinal": True, "lang": "en"}))
                        await ws.send_text(json.dumps({"type": "status", "data": "done"}))
                        await ws.close()
                        return
        except WebSocketDisconnect:
            return

    return app
