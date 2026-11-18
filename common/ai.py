"""AI 문구 생성 — Gemini → Groq → (둘 다 안 되면) None.

None 이 돌아오면 호출한 쪽에서 템플릿 문구로 대체합니다. 즉 AI 가 실패해도 자동화는 멈추지 않습니다.

주의할 점(실제 운영에서 겪은 문제):
  * 토큰 한도에 걸려 문장이 중간에서 끊긴 응답을 그대로 게시하면 "고춧가" 같은 글이 올라갑니다.
    → finish_reason 이 MAX_TOKENS / length 면 "실패"로 취급하고 다음 서비스로 넘어갑니다.
  * 생각(thinking) 기능이 있는 모델은 답변 전에 토큰을 쓰므로 max_tokens 를 넉넉히(2048) 잡습니다.
"""
from __future__ import annotations

import itertools
import os
import re

from .config import env, env_bool

MAX_TOKENS = 2048
_groq_rotation = itertools.count()


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    return text


def _gemini(prompt: str, system: str | None) -> str | None:
    key = env("GEMINI_API_KEY")
    if not key or key.startswith("your-"):
        return None
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=env("GEMINI_MODEL", "gemini-flash-latest"),
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=MAX_TOKENS, temperature=0.8, system_instruction=system
        ),
    )
    cands = getattr(resp, "candidates", None) or []
    if cands and "MAX_TOKENS" in str(getattr(cands[0], "finish_reason", "")):
        raise RuntimeError("Gemini 응답이 토큰 한도로 잘림")
    return _clean(resp.text or "")


def _groq_keys() -> list[str]:
    keys = []
    for name in sorted(k for k in os.environ if k == "GROQ_API_KEY" or re.fullmatch(r"GROQ_API_KEY_\d+", k)):
        v = env(name)
        if v and not v.startswith("your-"):
            keys.append(v)
    return keys


def _groq(prompt: str, system: str | None) -> str | None:
    keys = _groq_keys()
    if not keys:
        return None
    from openai import OpenAI

    start = next(_groq_rotation)
    last_error: Exception | None = None
    for i in range(len(keys)):  # 한 키가 실패하면 나머지 키를 한 번씩 시도
        key = keys[(start + i) % len(keys)]
        try:
            client = OpenAI(api_key=key, base_url=env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
            messages = ([{"role": "system", "content": system}] if system else []) + [
                {"role": "user", "content": prompt}
            ]
            resp = client.chat.completions.create(
                model=env("GROQ_MODEL", "openai/gpt-oss-120b"),
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=0.8,
            )
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise RuntimeError("Groq 응답이 토큰 한도로 잘림")
            text = _clean(choice.message.content or "")
            if text:
                return text
            raise RuntimeError("Groq 빈 응답")
        except Exception as e:  # noqa: BLE001 - 다음 키로 넘어가기 위해 폭넓게 처리
            last_error = e
    if last_error:
        raise last_error
    return None


def generate_text(prompt: str, *, max_chars: int | None = None, system: str | None = None) -> str | None:
    """프롬프트로 글을 생성. 실패하면 None(호출한 쪽이 템플릿으로 대체)."""
    if not env_bool("AI_ENABLED", True):
        return None
    for name, fn in (("gemini", _gemini), ("groq", _groq)):
        try:
            text = fn(prompt, system)
        except Exception as e:  # noqa: BLE001
            print(f"[ai] {name} 실패 → 다음 단계: {type(e).__name__}: {str(e)[:120]}")
            continue
        if not text:
            continue
        if max_chars and len(text) > max_chars:
            print(f"[ai] {name} 결과가 너무 김({len(text)}>{max_chars}) → 다음 단계")
            continue
        return text
    return None
