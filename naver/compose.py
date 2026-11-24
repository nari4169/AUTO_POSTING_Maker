"""블로그 글(제목/본문) 만들기. 브라우저 없이 테스트할 수 있도록 분리."""
from __future__ import annotations

from dataclasses import dataclass

from common import ai
from common.disclosure import disclosure_text
from common.sources import Item



@dataclass
class BlogPost:
    title: str
    body: str  # 줄바꿈(\n)으로 문단 구분하는 일반 텍스트


def _ai_body(item: Item) -> str | None:
    prompt = (
        "너는 한국어 블로그 작성자다. 아래 주제로 네이버 블로그 글 본문을 600~900자로 써라. "
        "인사말 1~2줄, 핵심 내용 3~4문단, 마무리 1줄 구성. 문단 사이는 빈 줄로 구분한다. "
        "마크다운 기호(#, *, `)는 쓰지 말고 본문만 출력한다.\n\n"
        f"주제: {item.title}\n참고 내용: {item.text}"
    )
    return ai.generate_text(prompt, max_chars=3000)


def build_post(item: Item, *, use_ai: bool = True) -> BlogPost:
    title = item.title or (item.text[:40] if item.text else "제목 없음")

    body = item.text
    if use_ai and item.title and len(item.text) < 200:
        # 짧은 메모만 있으면 AI 가 글로 확장. 실패하면 원래 메모를 그대로 사용.
        body = _ai_body(item) or item.text or item.title
    if not body:
        body = item.title

    parts = [body]
    if item.link:
        parts.append(f"자세히 보기: {item.link}")
        if disclosure_text(True):
            parts.append(disclosure_text(True))
    if item.hashtags:
        parts.append(" ".join(f"#{t}" for t in item.hashtags))
    return BlogPost(title=title, body="\n\n".join(parts))
