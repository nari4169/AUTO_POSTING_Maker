"""대가성(제휴 마케팅) 안내 문구 — 채널 공통.

.env 설정
  AD_DISCLOSURE_MODE   auto(기본) = 제휴 링크가 있는 글에만 문구를 붙임 / off = 문구를 붙이지 않음
  AD_DISCLOSURE        붙일 문구(비워 두면 아래 기본 문구)

※ 문구를 끄면(off) 표시 의무는 사용자 본인에게 있습니다. 수수료를 받는 링크가 들어가는 글이라면 켜 두세요.
"""
from __future__ import annotations

from .config import ConfigError, env

DEFAULT_DISCLOSURE = "이 포스팅은 제휴 마케팅 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받을 수 있습니다."
MODES = ("auto", "off")


def disclosure_mode() -> str:
    mode = (env("AD_DISCLOSURE_MODE", "auto") or "auto").lower()
    if mode not in MODES:
        raise ConfigError(f"AD_DISCLOSURE_MODE 는 {', '.join(MODES)} 중 하나여야 합니다: {mode!r}")
    return mode


def disclosure_text(has_link: bool) -> str:
    """이 글에 붙일 대가성 문구. 붙이지 않아야 하면 빈 문자열."""
    if not has_link or disclosure_mode() == "off":
        return ""
    return env("AD_DISCLOSURE", DEFAULT_DISCLOSURE) or DEFAULT_DISCLOSURE
