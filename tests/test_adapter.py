import json
import struct

import httpx
import numpy as np
import pytest
import websockets

from vapi_lokutor_adapter.audio import resample_pcm16, stereo_to_customer_mono

SECRET = {"x-vapi-secret": "s3cret"}


def voice_request(rate=16000, text="Hello there."):
    return {"message": {"type": "voice-request", "text": text, "sampleRate": rate, "timestamp": 1700000000000,
                        "call": {"id": "c", "orgId": "o"}}}


# ---------------- custom voice ----------------
@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100])
def test_voice_returns_raw_pcm_at_requested_rate(adapter, rate):
    srv, mock = adapter
    r = httpx.post(f"http://127.0.0.1:{srv.port}/api/synthesize", json=voice_request(rate), headers=SECRET)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert not r.content.startswith(b"RIFF")  # no container
    expected_samples = round(0.5 * rate)
    assert abs(len(r.content) // 2 - expected_samples) <= 2
    pcm = np.frombuffer(r.content, dtype="<i2")
    assert 8000 < np.abs(pcm).max() < 14000  # the 440 Hz tone survived resampling
    assert mock.state.tts_requests[-1] == {"text": "Hello there.", "voice": "F1", "language": "en"}


def test_voice_and_language_from_query(adapter):
    srv, mock = adapter
    r = httpx.post(f"http://127.0.0.1:{srv.port}/api/synthesize?voice=F3&language=es",
                   json=voice_request(), headers=SECRET)
    assert r.status_code == 200
    assert mock.state.tts_requests[-1]["voice"] == "F3"
    assert mock.state.tts_requests[-1]["language"] == "es"


def test_voice_rejects_missing_secret(adapter):
    srv, _ = adapter
    assert httpx.post(f"http://127.0.0.1:{srv.port}/api/synthesize", json=voice_request()).status_code == 401


def test_voice_accepts_bearer_secret(adapter):
    srv, _ = adapter
    r = httpx.post(f"http://127.0.0.1:{srv.port}/api/synthesize", json=voice_request(),
                   headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_voice_validation(adapter):
    srv, _ = adapter
    url = f"http://127.0.0.1:{srv.port}/api/synthesize"
    assert httpx.post(url, json={"message": {"type": "other"}}, headers=SECRET).status_code == 400
    assert httpx.post(url, json=voice_request(text="  "), headers=SECRET).status_code == 400
    r = httpx.post(url, json=voice_request(rate=12345), headers=SECRET)
    assert r.status_code == 400 and 16000 in r.json()["supportedRates"]
    assert httpx.post(url, content=b"nope", headers=SECRET).status_code == 400


def test_voice_upstream_errors(adapter):
    srv, _ = adapter
    url = f"http://127.0.0.1:{srv.port}/api/synthesize"
    assert httpx.post(url + "?voice=BAD", json=voice_request(), headers=SECRET).status_code == 502
    busy = httpx.post(url + "?voice=BUSY", json=voice_request(), headers=SECRET)
    assert busy.status_code == 503 and busy.headers["retry-after"] == "2"


# ---------------- custom transcriber ----------------
def stereo_chunk(customer_val=1000, assistant_val=-1000, frames=1600):
    return b"".join(struct.pack("<hh", customer_val, assistant_val) for _ in range(frames))


async def test_transcriber_end_to_end(adapter):
    srv, mock = adapter
    uri = f"ws://127.0.0.1:{srv.port}/api/custom-transcriber?language=es"
    async with websockets.connect(uri, additional_headers=SECRET) as ws:
        await ws.send(json.dumps({"type": "start", "encoding": "linear16", "container": "raw",
                                  "sampleRate": 16000, "channels": 2}))
        for _ in range(5):  # 5 x 1600 stereo frames = 0.5 s mono = 16000 bytes
            await ws.send(stereo_chunk())
        first = json.loads(await ws.recv())
        assert first == {"type": "transcriber-response", "transcription": "hello wor",
                         "channel": "customer", "transcriptType": "partial"}
        await ws.close()
    # the adapter forwarded only the customer channel (value 1000), as mono
    pcm = np.frombuffer(b"".join(mock.state.stt_pcm), dtype="<i2")
    assert len(pcm) == 8000 and set(pcm.tolist()) == {1000}
    assert mock.state.stt_first[-1] == {"language": "es"}


async def test_transcriber_final_on_end(adapter):
    srv, _ = adapter
    uri = f"ws://127.0.0.1:{srv.port}/api/custom-transcriber"
    async with websockets.connect(uri, additional_headers=SECRET) as ws:
        await ws.send(json.dumps({"type": "start", "sampleRate": 16000, "channels": 2}))
        await ws.send(stereo_chunk(frames=800))
        await ws.close()
    # nothing to assert on the closed socket: the call must not hang or raise


async def test_transcriber_rejects_bad_secret(adapter):
    srv, _ = adapter
    uri = f"ws://127.0.0.1:{srv.port}/api/custom-transcriber"
    with pytest.raises(Exception):
        async with websockets.connect(uri, additional_headers={"x-vapi-secret": "wrong"}) as ws:
            await ws.recv()


# ---------------- audio helpers ----------------
def test_stereo_split_drops_partial_frame():
    chunk = struct.pack("<hhhh", 1, 2, 3, 4) + b"\x09"
    assert np.frombuffer(stereo_to_customer_mono(chunk), dtype="<i2").tolist() == [1, 3]


def test_resample_identity():
    s = np.arange(100, dtype=np.int16)
    assert np.frombuffer(resample_pcm16(s, 16000, 16000), dtype="<i2").tolist() == s.tolist()
