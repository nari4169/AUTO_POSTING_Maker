"""게시 문구 만들기.

구조: 본문(링크 없음) + 자기 댓글(링크/광고 문구)
  * Threads 글자 수 제한은 500자. 넘으면 해시태그/고지는 남기고 본문만 줄입니다.
  * Threads 는 글 하나에 주제 태그(#)를 1개만 지원하므로 첫 번째 해시태그만 사용합니다.
  * 링크를 댓글에 두면 본문이 깔끔하지만, 댓글 작성에는 threads_manage_replies 권한이 필요합니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from common import ai
from common.disclosure import disclosure_text
from common.sources import Item

MAX_POST_LENGTH = 500


@dataclass
class PostContent:
    body: str
    reply: str = ""


def disclosure() -> str:
    return disclosure_text(True)


def _tail(item: Item) -> str:
    """본문 뒤에 붙는 고정 부분(글자 수를 먼저 계산해야 하는 부분)."""
    parts = []
    if item.link:
        parts.append("👇 링크는 댓글에 남겨 두었어요")
    if item.hashtags:
        parts.append(f"#{item.hashtags[0]}")
    return "\n\n".join(parts)


def make_hook(item: Item, max_chars: int) -> str | None:
    """AI 로 스크롤을 멈추게 하는 본문을 만든다. 실패하면 None(→ 템플릿 사용)."""
    prompt = (
        "너는 한국어 SNS 카피라이터다. 아래 정보를 바탕으로 Threads 게시글 본문을 "
        f"{max_chars}자 이내로 써라. 첫 문장은 스크롤을 멈추게 하는 질문이나 의외의 사실로 시작한다. "
        "링크, 해시태그, 광고 문구는 쓰지 말고 설명 없이 본문만 출력한다.\n\n"
        f"제목: {item.title}\n내용: {item.text}"
    )
    return ai.generate_text(prompt, max_chars=max_chars)


def weighted_len(text: str) -> int:
    """Threads 글자 수 계산. 공식 문서: 이모지는 UTF-8 바이트 길이만큼 센다(보통 4).

    그래서 이모지 한 개를 4, 나머지 글자는 1로 계산해 500자 한도를 넘지 않게 한다.
    (BMP 안의 기호형 이모지(☀ ❤ 등)도 안전하게 3으로 센다.)
    """
    total = 0
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            total += 4
        elif 0x2600 <= cp <= 0x27BF:
            total += 3
        else:
            total += 1
    return total


def _fit(text: str, budget: int) -> str:
    """weighted_len 이 budget 이하가 되도록 뒤에서부터 줄이고 말줄임표를 붙인다."""
    if weighted_len(text) <= budget:
        return text
    while text and weighted_len(text) + 1 > budget:
        text = text[:-1]
    return text.rstrip() + "…"


def build_post(item: Item, *, use_ai: bool = True) -> PostContent:
    tail = _tail(item)
    sep = 2 if tail else 0  # "\n\n"
    budget = MAX_POST_LENGTH - weighted_len(tail) - sep

    head = None
    if use_ai and (item.title or item.text):
        head = make_hook(item, budget)
    if not head or weighted_len(head) > budget:
        head = item.text or item.title
    head = _fit(head, budget)

    body = head if not tail else f"{head}\n\n{tail}"

    reply = ""
    if item.link:
        lines = []
        if item.title:
            lines.append(item.title)
        lines.append(item.link)
        notice = disclosure()
        if notice:
            lines.append("")
            lines.append(notice)
        reply = "\n".join(lines)
        reply = _fit(reply, MAX_POST_LENGTH)
    return PostContent(body=body, reply=reply)
