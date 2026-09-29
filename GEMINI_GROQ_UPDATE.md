# Gemini + Groq update

This build keeps both LLM paths available:

- `ChatGPT` logical slot -> Groq -> `openai/gpt-oss-120b`
- `Gemini` logical slot -> Google Gemini REST API -> `gemini-3.5-flash-lite` by default

## Changes in this update

1. Google Gemini provider is wired into `app.py` and provider construction.
2. Gemini keys are accepted through `GEMINI_API_KEYS` and the sidebar.
3. Agent 3 now has provider-specific pacing: `GROQ_MIN_INTERVAL_S` and `GEMINI_MIN_INTERVAL_S`.
4. Gemini defaults to a fast/cost-efficient Flash-Lite model and remains configurable with `GEMINI_MODEL`.
5. Gemini 429/5xx retry handling now prefers Google `Retry-After` / structured `retryDelay` hints and falls back to exponential backoff.
6. Gemini is intentionally not added to Agents 1, 7, or 8 in this build, so enabling Gemini only spends Gemini calls in the explicit Agent 3 LLM comparison stage.
7. Existing key rotation/resume behavior remains in place.

## Recommended `.env`

```env
GROQ_API_KEYS=gsk_key1,gsk_key2
GROQ_MODEL=openai/gpt-oss-120b
GROQ_MIN_INTERVAL_S=0.5

GEMINI_API_KEYS=AIza_key1,AIza_key2
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_MIN_INTERVAL_S=6.5
GEMINI_MAX_RETRIES=3
GEMINI_RETRY_BASE_DELAY_S=2.0
GEMINI_RETRY_MAX_DELAY_S=90.0
```

6.5 seconds is only a conservative starting point for a roughly 10-RPM limit. Use the actual limit shown for the Google project/tier. Google's documentation says 429 can represent RPM, TPM, RPD, spend, or other rate limits; retry/backoff is appropriate for retryable cases.
