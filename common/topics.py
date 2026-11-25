"""주제 기반 글감 — .env 에 적은 주제 목록을 돌려 가며 쓰고, 같은 주제·같은 문구를 일정 기간 다시 쓰지 않는다.

  * 주제마다 "마지막으로 쓴 때"와 "그때의 문구"를 SQLite(data/state.db 의 topic_history 표)에 기록합니다.
  * 마지막으로 쓴 지 REPOST_DAYS(기본 15일)가 지나지 않은 주제는 고르지 않습니다.
  * 그 기간 안에 이미 올린 것과 똑같은 문구도 쓰지 않습니다(공백을 무시하고 비교).
  * 게시할 때마다 새 ID(topic-날짜시각-주제코드)를 만들어 쓰므로 ID 가 겹치지 않습니다.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import data_dir

DEFAULT_REPOST_DAYS = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_history (
    channel TEXT NOT NULL,
    item_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    script TEXT NOT NULL,
    script_hash TEXT NOT NULL,
    used_at TEXT NOT NULL,
    PRIMARY KEY (channel, item_id)
)
"""


def parse_topics(raw: str | None) -> list[str]:
    """'주제1|주제2|주제3' → ['주제1', '주제2', '주제3'] (빈 칸·중복 제거, 순서 유지)."""
    seen: list[str] = []
    for part in (raw or "").split("|"):
        t = re.sub(r"\s+", " ", part).strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def script_hash(text: str) -> str:
    """공백·문장부호 차이는 무시하고 같은 문구인지 비교하기 위한 지문."""
    norm = re.sub(r"[\s\W_]+", "", text or "").lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def new_item_id(topic: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    code = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:6]
    return f"topic-{now:%Y%m%d-%H%M%S}-{code}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TopicStore:
    def __init__(self, channel: str, db_path: str | Path | None = None):
        self.channel = channel
        self.db_path = Path(db_path) if db_path else data_dir() / "state.db"
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _since(self, days: int) -> str:
        return (_now() - timedelta(days=days)).isoformat()

    def last_used(self, topic: str) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(used_at) AS t FROM topic_history WHERE channel=? AND topic=?", (self.channel, topic)
        ).fetchone()
        return row["t"] if row else None

    def last_script(self, topic: str) -> str:
        row = self._conn.execute(
            "SELECT script FROM topic_history WHERE channel=? AND topic=? ORDER BY used_at DESC LIMIT 1",
            (self.channel, topic),
        ).fetchone()
        return row["script"] if row else ""

    def eligible(self, topics: list[str], days: int = DEFAULT_REPOST_DAYS) -> list[str]:
        """다시 써도 되는 주제를 "한 번도 안 쓴 것(목록 순서) → 오래전에 쓴 것" 순으로."""
        since = self._since(days)
        fresh, old = [], []
        for t in topics:
            last = self.last_used(t)
            if last is None:
                fresh.append(t)
            elif last < since:
                old.append((last, t))
        return fresh + [t for _, t in sorted(old)]

    def next_available(self, topics: list[str], days: int = DEFAULT_REPOST_DAYS) -> list[tuple[str, str]]:
        """아직 기다려야 하는 주제와 다시 쓸 수 있는 시각(UTC) 목록 — 안내용."""
        out = []
        for t in topics:
            last = self.last_used(t)
            if last and last >= self._since(days):
                out.append((t, (datetime.fromisoformat(last) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")))
        return out

    def is_recent_script(self, script: str, days: int = DEFAULT_REPOST_DAYS) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM topic_history WHERE channel=? AND script_hash=? AND used_at>=? LIMIT 1",
            (self.channel, script_hash(script), self._since(days)),
        ).fetchone()
        return row is not None

    def record(self, topic: str, item_id: str, script: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO topic_history(channel,item_id,topic,script,script_hash,used_at) "
                "VALUES(?,?,?,?,?,?)",
                (self.channel, item_id, topic, script, script_hash(script), _now().isoformat()),
            )

    def forget(self, item_id: str) -> None:
        """게시가 확실히 실패했을 때 기록을 지워, 그 주제를 다시 고를 수 있게 한다."""
        with self._conn:
            self._conn.execute("DELETE FROM topic_history WHERE channel=? AND item_id=?", (self.channel, item_id))
