"""LLM access with automatic failover.

Gemini (free tier) is primary; Groq is the fallback when Gemini rate-limits.
Everything structured goes through `json_call`, which retries on malformed output
rather than letting a bad parse kill a pipeline run.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from .config import env

log = logging.getLogger(__name__)

# Rolling aliases, not pinned versions: providers retire dated model names
# every few months and a retired name is a 404 that stops the whole pipeline.
# The free tier counts requests per model per day (20 on the main one), so the
# lite model is a second, separate allowance.
GEMINI_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")
GEMINI_MODEL = GEMINI_MODELS[0]
# Groq has no alias, so rank whatever chat models are available at call time.
GROQ_PREFERRED = ("gpt-oss-120b", "llama-3.3-70b", "llama-4", "llama", "gpt-oss", "qwen")
GROQ_EXCLUDE = ("guard", "whisper", "tts", "vision", "orpheus", "allam")
# Groq's free tier counts prompt + max_tokens against a budget of 8,000 tokens
# a minute; a request over it is refused outright (and the run drops to a
# weaker model). So the answer allowance shrinks as the prompt grows.
GROQ_MAX_TOKENS = 4000
GROQ_TPM = 8000
_groq_models: list[str] | None = None
_exhausted: set[str] = set()     # models out of quota for the rest of this run


def groq_models(client) -> list[str]:
    """Available chat models, best first; cached."""
    global _groq_models
    if _groq_models is None:
        ids = [m.id for m in client.models.list().data
               if not any(x in m.id.lower() for x in GROQ_EXCLUDE)]

        def rank(i: str) -> int:
            return next((n for n, want in enumerate(GROQ_PREFERRED) if want in i.lower()),
                        len(GROQ_PREFERRED))
        _groq_models = sorted(ids, key=lambda i: (rank(i), i))
        if not _groq_models:
            raise RuntimeError("No usable Groq chat model available")
        log.info("Groq models: %s", ", ".join(_groq_models))
    return _groq_models


def _gemini(prompt: str, system: str | None, temperature: float) -> str:
    import google.generativeai as genai

    genai.configure(api_key=env("GEMINI_API_KEY", required=True))
    last: Exception | None = None
    for name in GEMINI_MODELS:
        if name in _exhausted:
            continue
        try:
            model = genai.GenerativeModel(name, system_instruction=system)
            resp = model.generate_content(
                prompt, generation_config={"temperature": temperature,
                                           "max_output_tokens": 8192})
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc)
            # A spent allowance or a retired name will not recover mid-run;
            # stop paying a failed request for it on every call.
            if "429" in msg or "404" in msg:
                _exhausted.add(name)
            log.warning("Gemini %s failed (%s)", name, msg.splitlines()[0][:160])
    raise last or RuntimeError("Gemini quota used up for this run")


def _groq(prompt: str, system: str | None, temperature: float) -> str:
    from groq import Groq

    # The SDK waits out a 429 using the server's retry-after, which on the
    # per-minute budget is seconds: far better than a weaker model.
    client = Groq(api_key=env("GROQ_API_KEY", required=True), max_retries=4)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    prompt_tokens = int(len((system or "") + prompt) / 3.2) + 50   # conservative
    max_tokens = max(1200, min(GROQ_MAX_TOKENS, GROQ_TPM - 300 - prompt_tokens))
    last: Exception | None = None
    for name in groq_models(client):
        try:
            extra = {"extra_body": {"reasoning_effort": "low"}} if "gpt-oss" in name else {}
            resp = client.chat.completions.create(
                model=name, messages=messages, temperature=temperature,
                max_tokens=max_tokens, **extra)
            if resp.choices[0].message.content:
                return resp.choices[0].message.content
            last = RuntimeError(f"{name} returned no text")
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Groq %s failed (%s)", name, str(exc)[:160])
    raise last or RuntimeError("Groq returned nothing")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call(prompt: str, system: str | None = None, temperature: float = 0.7) -> str:
    """Plain text completion. Falls back Gemini -> Groq."""
    try:
        return _gemini(prompt, system, temperature)
    except Exception as exc:  # noqa: BLE001 - any provider error should fail over
        log.warning("Gemini unavailable (%s); falling back to Groq",
                    str(exc).splitlines()[0][:120])
        return _groq(prompt, system, temperature)


def _extract_json(raw: str) -> Any:
    """Models wrap JSON in prose and fences more often than they should."""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # last resort: grab the outermost object or array
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Could not parse JSON from model output:\n{raw[:800]}")


def json_call(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    attempts: int = 3,
) -> Any:
    """Structured completion. Re-prompts on malformed JSON."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            suffix = "\n\nRespond with valid JSON only. No prose, no code fences."
            if i:
                suffix += " Your previous response was not valid JSON."
            return _extract_json(call(prompt + suffix, system, temperature))
        except ValueError as exc:
            last = exc
            log.warning("JSON parse attempt %d/%d failed", i + 1, attempts)
    raise RuntimeError(f"LLM never returned valid JSON: {last}")
