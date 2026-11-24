import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from common import ai
from common.sources import Item, load_json_items, pick_items
from common.store import (AlreadyHandled, DailyLimitReached, PendingConflict, PostStore, MAX_FAILS)


@pytest.fixture
def store(tmp_path):
    s = PostStore("test", tmp_path / "state.db")
    yield s
    s.close()


def test_reserve_publish_blocks_duplicates(store):
    store.reserve("a")
    with pytest.raises(PendingConflict):          # 결과 불명 pending 은 자동 재시도 금지
        store.reserve("a")
    store.mark_published("a", "m1")
    with pytest.raises(AlreadyHandled):
        store.reserve("a")
    assert store.is_blocked("a")


def test_failed_retries_until_limit(store):
    for _ in range(MAX_FAILS):
        store.reserve("b")
        store.mark_failed("b", "boom")
    assert store.is_blocked("b")
    with pytest.raises(AlreadyHandled):
        store.reserve("b")


def test_failed_once_can_retry(store):
    store.reserve("c")
    store.mark_failed("c", "boom")
    assert not store.is_blocked("c")
    store.reserve("c")  # 다시 시도 가능


def test_daily_limit(store):
    store.reserve("x", daily_limit=1)
    store.mark_published("x", "1")
    with pytest.raises(DailyLimitReached):
        store.reserve("y", daily_limit=1)


def test_resolve_pending(store):
    store.reserve("p")
    assert [r["item_id"] for r in store.list_pending()] == ["p"]
    store.resolve_pending("p", published=False)
    assert not store.is_blocked("p")


def test_reply_tracking(store):
    store.reserve("r")
    store.mark_published("r", "m9")
    store.set_reply("r", "링크")
    assert len(store.pending_replies()) == 1
    store.mark_reply_done("r")
    assert store.pending_replies() == []


def test_pick_items_skips_published(store):
    items = [Item(id="1", title="t1"), Item(id="2", title="t2"), Item(id="3", title="t3")]
    store.reserve("1")
    store.mark_published("1", "m")
    assert [i.id for i in pick_items(items, store, 2)] == ["2", "3"]


def test_load_example_json():
    items = load_json_items(Path(__file__).resolve().parent.parent / "content" / "posts.example.json")
    assert len(items) >= 3 and all(i.id for i in items)


def test_item_requires_title_or_text():
    with pytest.raises(ValueError):
        Item.from_dict({"id": "z"})


def test_ai_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setattr(ai, "_gemini", lambda p, s: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(ai, "_groq", lambda p, s: None)
    assert ai.generate_text("hi") is None


def test_ai_uses_second_service_and_rejects_too_long(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setattr(ai, "_gemini", lambda p, s: "x" * 100)  # 너무 긺 → 거절
    monkeypatch.setattr(ai, "_groq", lambda p, s: "짧은 글")
    assert ai.generate_text("hi", max_chars=50) == "짧은 글"


def test_ai_disabled(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "0")
    assert ai.generate_text("hi") is None


# ── 운영 도구 ────────────────────────────────────────────────
def test_store_stats(store):
    store.reserve("a"); store.mark_published("a", "m1")
    store.reserve("b"); store.mark_failed("b", "boom")
    store.reserve("c")                      # pending
    st = store.stats()
    assert st["counts"] == {"published": 1, "pending": 1, "failed": 1}
    assert st["published_24h"] == 1 and st["last_published"]
    assert {p["item_id"] for p in st["problems"]} == {"b", "c"}


def test_backup_creates_and_prunes(tmp_path):
    import sqlite3
    from tools.backup import backup
    db = tmp_path / "state.db"
    s = PostStore("t", db)
    s.reserve("x"); s.close()
    out = tmp_path / "bk"
    import time
    paths = []
    for _ in range(4):
        paths.append(backup(keep=2, db_path=db, out_dir=out))
        time.sleep(1.1)                     # 파일 이름이 초 단위라 간격을 둠
    assert len(list(out.glob("state_*.db"))) == 2
    assert sqlite3.connect(str(paths[-1])).execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_status_report_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from tools.status import build_report
    s = PostStore("threads")
    s.reserve("p")                           # pending → 문제 있음으로 표시
    s.close()
    text, problem = build_report()
    assert problem and "threads" in text and "p" in text and "디스크" in text


def test_heartbeat_skips_without_url(monkeypatch, capsys):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    from tools.heartbeat import main
    assert main() == 0


def test_pick_items_explains_why_empty(store, capsys):
    items = [Item(id="a", title="t"), Item(id="b", title="t")]
    store.reserve("a")
    store.mark_published("a", "m")
    for _ in range(3):
        store.reserve("b")
        store.mark_failed("b", "토큰 오류")
    assert pick_items(items, store, 1) == []
    out = capsys.readouterr().out
    assert "a: 이미 게시함" in out and "b: 실패 3회" in out and "토큰 오류" in out
    assert pick_items([], store, 1) == []
    assert "글감이 없습니다" in capsys.readouterr().out
