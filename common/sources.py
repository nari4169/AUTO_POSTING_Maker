"""글감 소스 — "무엇을 올릴까"를 담당하는 교체 가능한 부분.

기본은 내가 직접 쓴 content/posts.json 이고, SOURCE_TYPE=rss 로 바꾸면 RSS 글을 글감으로 씁니다.
나만의 소스(쇼핑 API, 뉴스 크롤링 등)를 붙이고 싶다면 Item 리스트만 돌려주는 함수를 만들면 됩니다.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from .config import ROOT, ConfigError, env, resolve_path


@dataclass
class Item:
    id: str
    title: str = ""
    text: str = ""            # 본문(비워 두면 AI 가 title 로 만들거나, title 을 그대로 씀)
    image_url: str = ""       # 공개 접근 가능한 이미지 주소(Threads 이미지 게시)
    image_path: str = ""      # 내 컴퓨터의 이미지 파일(Reels 영상 배경)
    link: str = ""            # 제휴/소개 링크(있으면 대가성 문구가 자동으로 붙음)
    hashtags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        title = str(d.get("title", "")).strip()
        text = str(d.get("text", "")).strip()
        if not title and not text:
            raise ValueError(f"title 또는 text 가 필요합니다: {d!r}")
        item_id = str(d.get("id") or "").strip() or hash_id(title or text)
        tags = d.get("hashtags") or []
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,\s]+", tags) if t]
        return cls(
            id=item_id,
            title=title,
            text=text,
            image_url=str(d.get("image_url", "")).strip(),
            image_path=str(d.get("image_path", "")).strip(),
            link=str(d.get("link", "")).strip(),
            hashtags=[t.lstrip("#") for t in tags],
        )


def hash_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def load_json_items(path: str | Path | None = None) -> list[Item]:
    p = resolve_path(path or env("CONTENT_FILE", "content/posts.json") or "content/posts.json")
    if not p.exists():
        fallback = ROOT / "content" / "posts.example.json"
        if not fallback.exists():
            raise ConfigError(f"글감 파일이 없습니다: {p}")
        print(f"[sources] {p.name} 이(가) 없어 예제 파일을 대신 사용합니다: {fallback.name}")
        p = fallback
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ConfigError(f"{p.name} 은 [ {{...}}, {{...}} ] 형태의 리스트여야 합니다.")
    return [Item.from_dict(d) for d in data]


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def load_rss_items(url: str | None = None, limit: int = 20) -> list[Item]:
    url = url or env("RSS_URL")
    if not url:
        raise ConfigError(".env 에 RSS_URL 을 채워 주세요.")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (autopost-example)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())

    items: list[Item] = []
    # RSS 2.0
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        desc = _strip_html(node.findtext("description") or "")
        guid = (node.findtext("guid") or link or title).strip()
        if title or desc:
            items.append(Item(id=hash_id(guid), title=title, text=desc[:400], link=link))
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for node in root.iter(f"{ns}entry"):
        title = (node.findtext(f"{ns}title") or "").strip()
        link_el = node.find(f"{ns}link")
        link = link_el.get("href", "") if link_el is not None else ""
        desc = _strip_html(node.findtext(f"{ns}summary") or node.findtext(f"{ns}content") or "")
        if title or desc:
            items.append(Item(id=hash_id(link or title), title=title, text=desc[:400], link=link))
    return items[:limit]


def get_items() -> list[Item]:
    source = (env("SOURCE_TYPE", "json") or "json").lower()
    if source == "rss":
        return load_rss_items()
    if source == "json":
        return load_json_items()
    raise ConfigError(f"SOURCE_TYPE 은 json 또는 rss 여야 합니다: {source!r}")


def pick_items(items: list[Item], store, count: int) -> list[Item]:
    """아직 게시하지 않았고 제외되지 않은 항목을 앞에서부터 count 개 고른다."""
    picked: list[Item] = []
    for item in items:
        if store.is_blocked(item.id):
            continue
        picked.append(item)
        if len(picked) >= count:
            break
    return picked
