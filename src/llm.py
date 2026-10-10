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

GEMINI_MODEL = "gemini-2.0-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"


def _gemini(prompt: str, system: str | None, temperature: float) -> str:
    import google.generativeai as genai

    genai.configure(api_key=env("GEMINI_API_KEY", required=True))
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    resp = model.generate_content(
        prompt, generation_config={"temperature": temperature, "max_output_tokens": 8192}
    )
    return resp.text


def _groq(prompt: str, system: str | None, temperature: float) -> str:
    from groq import Groq

    client = Groq(api_key=env("GROQ_API_KEY", required=True))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=GROQ_MODEL, messages=messages, temperature=temperature, max_tokens=8000
    )
    return resp.choices[0].message.content


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call(prompt: str, system: str | None = None, temperature: float = 0.7) -> str:
    """Plain text completion. Falls back Gemini -> Groq."""
    try:
        return _gemini(prompt, system, temperature)
    except Exception as exc:  # noqa: BLE001 - any provider error should fail over
        log.warning("Gemini failed (%s); falling back to Groq", exc)
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
