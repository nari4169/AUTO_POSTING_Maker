"""게시 이력 저장소(SQLite) — 중복 게시 방지의 핵심.

상태 흐름:  (없음) → pending → published | failed
  - pending 인 채로 남은 항목은 "발행은 됐는데 응답만 못 받았을" 수 있으므로
    자동 재시도를 막습니다(PendingConflict). 사람이 확인한 뒤 resolve_pending() 으로 확정하세요.
  - failed 는 MAX_FAILS 번까지만 자동 재시도합니다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import data_dir

MAX_FAILS = 3


class AlreadyHandled(Exception):
    """이미 게시했거나 실패 한도를 넘긴 항목."""


class PendingConflict(Exception):
    """결과를 알 수 없는 pending 상태가 남아 있음 — 사람이 확인해야 함."""


class DailyLimitReached(Exception):
    """24시간 게시 한도 도달."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    channel TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_id TEXT,
    error TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0,
    reply_text TEXT,
    reply_status TEXT,
    reply_fail_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (channel, item_id)
)
"""


class PostStore:
    def __init__(self, channel: str, db_path: str | Path | None = None):
        self.channel = channel
        self.db_path = Path(db_path) if db_path else data_dir() / "state.db"
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ── 조회 ────────────────────────────────────────────────
    def get(self, item_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM posts WHERE channel=? AND item_id=?", (self.channel, item_id)
        ).fetchone()

    def is_blocked(self, item_id: str) -> bool:
        """자동 게시 대상에서 제외해야 하는가(게시 완료 / pending / 실패 한도)."""
        row = self.get(item_id)
        if row is None:
            return False
        if row["status"] in ("published", "pending"):
            return True
        return row["status"] == "failed" and row["fail_count"] >= MAX_FAILS

    def stats(self) -> dict:
        """채널의 현황 요약: 상태별 개수, 24시간 게시 수, 마지막 게시 시각, 실패/pending 목록."""
        counts = {"published": 0, "pending": 0, "failed": 0}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM posts WHERE channel=? GROUP BY status", (self.channel,)
        ):
            counts[row["status"]] = int(row["n"])
        last = self._conn.execute(
            "SELECT MAX(updated_at) AS t FROM posts WHERE channel=? AND status='published'", (self.channel,)
        ).fetchone()["t"]
        problems = [
            dict(item_id=r["item_id"], status=r["status"], fail_count=r["fail_count"], error=r["error"])
            for r in self._conn.execute(
                "SELECT item_id, status, fail_count, error FROM posts "
                "WHERE channel=? AND status IN ('pending','failed') ORDER BY updated_at DESC LIMIT 10",
                (self.channel,),
            )
        ]
        return {
            "counts": counts,
            "published_24h": self.count_published_since(24),
            "last_published": last,
            "problems": problems,
        }

    def count_published_since(self, hours: float = 24) -> int:
        since = _iso(_now() - timedelta(hours=hours))
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE channel=? AND status='published' AND updated_at>=?",
            (self.channel, since),
        ).fetchone()
        return int(row["n"])

    # ── 상태 전이 ───────────────────────────────────────────
    def reserve(self, item_id: str, daily_limit: int | None = None) -> None:
        """게시를 시작하기 전에 반드시 호출. 조건에 안 맞으면 예외."""
        row = self.get(item_id)
        if row is not None:
            if row["status"] == "published":
                raise AlreadyHandled(f"이미 게시됨: {item_id}")
            if row["status"] == "pending":
                raise PendingConflict(
                    f"이전 시도의 결과를 알 수 없습니다({item_id}). 실제로 게시됐는지 확인한 뒤 "
                    "`python -m tools.resolve_pending` 으로 확정하세요."
                )
            if row["fail_count"] >= MAX_FAILS:
                raise AlreadyHandled(f"실패 {MAX_FAILS}회 초과로 제외됨: {item_id}")
        if daily_limit is not None and self.count_published_since(24) >= daily_limit:
            raise DailyLimitReached(f"24시간 게시 한도({daily_limit}건)에 도달했습니다.")

        now = _iso(_now())
        with self._conn:
            if row is None:
                self._conn.execute(
                    "INSERT INTO posts(channel,item_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (self.channel, item_id, "pending", now, now),
                )
            else:
                self._conn.execute(
                    "UPDATE posts SET status='pending', updated_at=? WHERE channel=? AND item_id=?",
                    (now, self.channel, item_id),
                )

    def mark_published(self, item_id: str, remote_id: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE posts SET status='published', remote_id=?, error=NULL, updated_at=? "
                "WHERE channel=? AND item_id=?",
                (remote_id, _iso(_now()), self.channel, item_id),
            )

    def mark_failed(self, item_id: str, error: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE posts SET status='failed', error=?, fail_count=fail_count+1, updated_at=? "
                "WHERE channel=? AND item_id=?",
                (error[:500], _iso(_now()), self.channel, item_id),
            )

    def resolve_pending(self, item_id: str, published: bool, remote_id: str | None = None) -> None:
        """pending 으로 남은 항목을 사람이 확인한 결과로 확정."""
        if published:
            self.mark_published(item_id, remote_id)
        else:
            self.mark_failed(item_id, "사람이 '게시 안 됨'으로 확정")

    def list_pending(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM posts WHERE channel=? AND status='pending' ORDER BY updated_at",
                (self.channel,),
            )
        )

    # ── 댓글(자기 댓글) 추적 ────────────────────────────────
    def set_reply(self, item_id: str, text: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE posts SET reply_text=?, reply_status='pending' WHERE channel=? AND item_id=?",
                (text, self.channel, item_id),
            )

    def mark_reply_done(self, item_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE posts SET reply_status='done' WHERE channel=? AND item_id=?",
                (self.channel, item_id),
            )

    def mark_reply_failed(self, item_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE posts SET reply_status='failed', reply_fail_count=reply_fail_count+1 "
                "WHERE channel=? AND item_id=?",
                (self.channel, item_id),
            )

    def pending_replies(self, max_fail: int = 3) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM posts WHERE channel=? AND status='published' AND remote_id IS NOT NULL "
                "AND reply_text IS NOT NULL AND reply_status IN ('pending','failed') "
                "AND reply_fail_count<?",
                (self.channel, max_fail),
            )
        )
