# vapi-lokutor-adapter

A small open service that lets [Vapi](https://vapi.ai) use [Lokutor](https://lokutor.com) as its
**speech-to-text** (Vapi "custom transcriber") and **text-to-speech** (Vapi "custom voice").
It implements the two protocols Vapi documents for bring-your-own providers and forwards to the
public Lokutor API. You run it; Vapi calls it.

```
Vapi ──wss /api/custom-transcriber──▶ adapter ──wss /ws/stt──────────▶ api.lokutor.com
Vapi ──POST /api/synthesize─────────▶ adapter ──POST /v1/tts/synthesize▶ api.lokutor.com
```

* **Transcriber.** Vapi sends a `start` message and then binary 16 kHz 16-bit **stereo** PCM
  (channel 0 = customer, channel 1 = assistant). The adapter forwards the customer channel, as mono,
  to Lokutor's streaming `/ws/stt` and returns Vapi's `transcriber-response` messages
  (`partial` while the caller speaks, `final` when Lokutor ends the utterance).
* **Voice.** Vapi POSTs a `voice-request`. The adapter synthesizes with Lokutor (44.1 kHz WAV),
  resamples to the `sampleRate` Vapi asked for (8000, 16000, 22050, 24000 or 44100 Hz) and returns
  raw mono 16-bit little-endian PCM as `application/octet-stream`.

Lokutor speech recognition covers English, Spanish, Catalan, Galician, Basque, Portuguese, French,
Italian and German. Voices are the slots `F1`-`F5` and `M1`-`M5` (see
[docs.lokutor.com](https://docs.lokutor.com/voices-languages-models)).

## Run it

```bash
docker build -t vapi-lokutor-adapter .
docker run -p 3000:3000 \
  -e LOKUTOR_API_KEY=... \          # your Lokutor key
  -e ADAPTER_SECRET=choose-a-long-random-string \
  vapi-lokutor-adapter
```

Vapi needs a public `https://` / `wss://` address, so put it behind your own TLS (a reverse proxy,
a tunnel such as ngrok for testing, or any container host).

| Variable | Default | Meaning |
|---|---|---|
| `LOKUTOR_API_KEY` | required | Lokutor API key. |
| `ADAPTER_SECRET` | empty | Shared secret. Checked against the `x-vapi-secret` header or `Authorization: Bearer <secret>`. **Set it**: without it anyone who finds the URL spends your Lokutor balance. |
| `LOKUTOR_BASE_URL` | `https://api.lokutor.com` | Lokutor API base. |
| `LOKUTOR_DEFAULT_VOICE` | `F1` | Voice when the URL has no `?voice=`. |
| `LOKUTOR_DEFAULT_LANGUAGE` | `en` | Language when the URL has no `?language=`. |
| `TTS_TIMEOUT_S` | `30` | Upstream synthesis timeout. |
| `PORT` | `3000` | Listen port. |

Without Docker: `pip install .` then `python -m vapi_lokutor_adapter.app`.

## Vapi assistant configuration

Vapi sends no voice id to a custom voice, so the **voice and language go in the server URL's query
string**. Likewise the transcriber language goes in the transcriber URL (`?language=es`; leave it
out to let Lokutor detect the language).

Replace `adapter.example.com` and the credential id with yours.

```json
{
  "name": "Lokutor assistant",
  "transcriber": {
    "provider": "custom-transcriber",
    "server": {
      "url": "wss://adapter.example.com/api/custom-transcriber?language=en",
      "credentialId": "YOUR_VAPI_CUSTOM_CREDENTIAL_ID"
    }
  },
  "voice": {
    "provider": "custom-voice",
    "server": {
      "url": "https://adapter.example.com/api/synthesize?voice=F1&language=en",
      "credentialId": "YOUR_VAPI_CUSTOM_CREDENTIAL_ID",
      "timeoutSeconds": 30
    }
  },
  "model": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "messages": [{ "role": "system", "content": "You are a helpful assistant." }]
  }
}
```

Create the credential in the Vapi dashboard as a **Bearer Token** whose token is your
`ADAPTER_SECRET`. The adapter also accepts the legacy `secret` field, which Vapi sends as
`x-vapi-secret`. Vapi's `voice.fallbackPlan` is supported by the adapter's error behaviour: any
failure on the Lokutor side answers `502` (or `503` with `Retry-After` when Lokutor is at
capacity), so a fallback voice can take over.

## What this does not do (yet)

* **Voice is not streamed.** The adapter waits for the whole sentence from Lokutor's one-shot
  endpoint, then answers; it does not use the streaming `/ws/tts`. First audio is therefore the
  synthesis time of the full sentence. Vapi sends a sentence at a time, so this is usually short, but
  it is not Lokutor's streaming first-audio figure.
* **Only the customer channel is transcribed.** Channel 1 (assistant) is ignored, since Vapi
  already knows what the assistant said.
* **Expression tags** (`[laugh]`, `[sigh]`...) in Vapi's text are passed to Lokutor as is.
* Silence and end-of-utterance detection are Lokutor's (about 0.7 s of silence); Vapi's own
  endpointing settings do not apply to a custom transcriber.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/python -m pytest
```

The suite runs the real adapter against a local mock of the two Lokutor endpoints, and a client
that speaks Vapi's protocol (start message, stereo PCM, `voice-request` JSON), for every sample rate
Vapi lists. No Vapi or Lokutor account is needed.

## Verification status

Checked against Vapi's published protocol documentation
([custom transcriber](https://docs.vapi.ai/customization/custom-transcriber),
[custom voice](https://docs.vapi.ai/customization/custom-voices/custom-tts)) with a local mock, and
against the live Lokutor API (a spoken sentence synthesized through the voice endpoint came back
through the transcriber endpoint as the same sentence). It has **not** been run with a real Vapi
account. In particular, whether Vapi keeps the query string in the server URL is taken from its
documentation, not tested; if it does not, set `LOKUTOR_DEFAULT_VOICE` / `LOKUTOR_DEFAULT_LANGUAGE`.

## Licence

MIT. Not affiliated with Vapi.
