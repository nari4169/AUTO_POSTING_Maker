import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from common import topics as T
from common.topics import TopicStore, parse_topics, script_hash


@pytest.fixture
def ts(tmp_path):
    s = TopicStore("reels", tmp_path / "state.db")
    yield s
    s.close()


def _age(ts, item_id, days):
    """기록 시각을 days 일 전으로 옮긴다."""
    when = (T._now() - timedelta(days=days)).isoformat()
    with ts._conn:
        ts._conn.execute("UPDATE topic_history SET used_at=? WHERE item_id=?", (when, item_id))


def test_parse_topics():
    assert parse_topics(" 주제 A | 주제B ||주제 A| ") == ["주제 A", "주제B"]
    assert parse_topics(None) == []


def test_script_hash_ignores_spacing_and_punctuation():
    assert script_hash("안녕하세요. 반가워요!") == script_hash("안녕하세요 반가워요")
    assert script_hash("안녕") != script_hash("잘가")


def test_new_item_id_is_unique_per_topic():
    a, b = T.new_item_id("주제1"), T.new_item_id("주제2")
    assert a.startswith("topic-") and a != b


def test_eligible_waits_15_days(ts):
    topics = ["A", "B", "C"]
    assert ts.eligible(topics) == ["A", "B", "C"]            # 처음엔 모두 가능(목록 순서)
    ts.record("A", "id1", "대본1")
    assert ts.eligible(topics) == ["B", "C"]                 # 방금 쓴 A 는 제외
    _age(ts, "id1", 14)
    assert "A" not in ts.eligible(topics)                    # 14일: 아직
    _age(ts, "id1", 16)
    assert ts.eligible(topics) == ["B", "C", "A"]            # 16일: 다시 가능(안 쓴 주제가 먼저)
    assert ts.next_available(["A"]) == []


def test_recent_script_and_forget(ts):
    ts.record("A", "id1", "같은 문구입니다.")
    assert ts.is_recent_script("같은 문구입니다")
    _age(ts, "id1", 16)
    assert not ts.is_recent_script("같은 문구입니다")
    ts.record("B", "id2", "다른 문구")
    ts.forget("id2")
    assert ts.eligible(["B"]) == ["B"] and ts.last_script("A") == "같은 문구입니다."


def test_plan_topic_items_uses_ai_and_avoids_duplicate_text(monkeypatch, ts):
    from reels import post_reels as pr
    monkeypatch.setenv("REEL_TOPICS", "주제A|주제B")
    monkeypatch.setenv("REEL_HASHTAGS", "#라즈베리파이, 자동화")
    monkeypatch.delenv("REEL_IMAGE_DIR", raising=False)
    ts.record("주제Z", "old", "이미 올린 문구")
    replies = iter(["이미 올린 문구", "새 문구 A", "새 문구 B"])
    prompts = []

    def fake_ai(prompt, **kw):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(pr.ai, "generate_text", fake_ai)
    pairs = pr.plan_topic_items(2, ts, 15)
    assert [(i.text, t) for i, t in pairs] == [("새 문구 A", "주제A"), ("새 문구 B", "주제B")]
    assert all(i.id.startswith("topic-") and i.hashtags == ["라즈베리파이", "자동화"] for i, _ in pairs)
    assert "다시 써라" in prompts[1]                          # 같은 문구라 한 번 더 요청


def test_plan_topic_items_requires_topics_and_reports_waiting(monkeypatch, ts, capsys):
    from common.config import ConfigError
    from reels import post_reels as pr
    monkeypatch.delenv("REEL_TOPICS", raising=False)
    with pytest.raises(ConfigError):
        pr.plan_topic_items(1, ts, 15)
    monkeypatch.setenv("REEL_TOPICS", "A")
    ts.record("A", "id1", "x")
    assert pr.plan_topic_items(1, ts, 15) == []
    assert "A:" in capsys.readouterr().out


def test_topic_mode_publish_flow(monkeypatch, tmp_path):
    """주제 모드 게시: 새 ID 로 게시하고 주제를 기록, 바로 다시 실행하면 같은 주제는 고르지 않는다."""
    from reels import post_reels as pr
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_SOURCE", "topics")
    monkeypatch.setenv("REEL_TOPICS", "주제A")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("IG_USER_ID", "u")
    monkeypatch.setenv("REELS_UPLOAD_MODE", "command")
    monkeypatch.setattr(pr.ai, "generate_text", lambda prompt, **kw: "AI 가 쓴 대본")
    monkeypatch.setattr(pr, "make_for_item", lambda item, out, use_ai: out)
    from reels.hosting import Hosted
    monkeypatch.setattr(pr, "host_video", lambda out, mode: Hosted(url="https://x/v.mp4", cleanup=lambda: None))
    monkeypatch.setattr(pr.time, "sleep", lambda s: None)
    captions = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        def create_reels_container(self, caption, video_url=None):
            captions.append(caption)
            return {"id": "c1"}
        def wait_until_ready(self, cid): pass
        def publish(self, cid): return "m1"

    monkeypatch.setattr(pr, "InstagramClient", FakeClient)
    assert pr.main(["--count", "1"]) == 0
    assert captions and captions[0].startswith("AI 가 쓴 대본")
    ts = TopicStore("reels")
    assert ts.last_script("주제A") == "AI 가 쓴 대본"
    ts.close()
    captions.clear()
    assert pr.main(["--count", "1"]) == 0                   # 15일 안: 같은 주제는 다시 안 올림
    assert captions == []


def test_topic_mode_rejects_no_ai(monkeypatch):
    from reels import post_reels as pr
    monkeypatch.setenv("REEL_SOURCE", "topics")
    assert pr.main(["--count", "1", "--no-ai"]) == 1
