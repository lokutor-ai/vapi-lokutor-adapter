from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    lokutor_api_key: str
    # REST base, e.g. https://api.lokutor.com ; the WebSocket base is derived from it.
    lokutor_base_url: str = "https://api.lokutor.com"
    # Shared secret Vapi sends (x-vapi-secret header, or "Authorization: Bearer <secret>"
    # when you use a Custom Credential of type Bearer). Empty = no check (local use only).
    adapter_secret: str = ""
    default_voice: str = "F1"
    default_language: str = "en"
    tts_timeout_s: float = 30.0

    @property
    def ws_base_url(self) -> str:
        base = self.lokutor_base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://"):]
        if base.startswith("http://"):
            return "ws://" + base[len("http://"):]
        return base

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.environ.get("LOKUTOR_API_KEY", "")
        if not key:
            raise RuntimeError("LOKUTOR_API_KEY is not set")
        return cls(
            lokutor_api_key=key,
            lokutor_base_url=os.environ.get("LOKUTOR_BASE_URL", "https://api.lokutor.com"),
            adapter_secret=os.environ.get("ADAPTER_SECRET", ""),
            default_voice=os.environ.get("LOKUTOR_DEFAULT_VOICE", "F1"),
            default_language=os.environ.get("LOKUTOR_DEFAULT_LANGUAGE", "en"),
            tts_timeout_s=float(os.environ.get("TTS_TIMEOUT_S", "30")),
        )
