from __future__ import annotations

import io
import wave
from math import gcd

import numpy as np
from scipy.signal import resample_poly

# Sample rates Vapi's custom voice documentation lists.
VAPI_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100)


def wav_to_pcm16(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode a 16-bit WAV into mono int16 samples and its sample rate."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM WAV")
        rate = w.getframerate()
        channels = w.getnchannels()
        raw = w.readframes(w.getnframes())
    samples = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples, rate


def resample_pcm16(samples: np.ndarray, src_rate: int, dst_rate: int) -> bytes:
    """Return little-endian mono 16-bit PCM at dst_rate."""
    if src_rate != dst_rate and len(samples):
        g = gcd(src_rate, dst_rate)
        out = resample_poly(samples.astype(np.float32), dst_rate // g, src_rate // g)
        samples = np.clip(np.rint(out), -32768, 32767).astype("<i2")
    return samples.astype("<i2").tobytes()


def wav_to_raw_pcm(wav_bytes: bytes, dst_rate: int) -> bytes:
    samples, rate = wav_to_pcm16(wav_bytes)
    return resample_pcm16(samples, rate, dst_rate)


def stereo_to_customer_mono(chunk: bytes) -> bytes:
    """Vapi streams 16-bit stereo; channel 0 is the customer, channel 1 the assistant.

    Returns the customer channel as mono PCM16. A trailing partial frame (not a
    multiple of 4 bytes) is dropped.
    """
    usable = len(chunk) - (len(chunk) % 4)
    if usable == 0:
        return b""
    arr = np.frombuffer(chunk[:usable], dtype="<i2").reshape(-1, 2)
    return arr[:, 0].astype("<i2").tobytes()
