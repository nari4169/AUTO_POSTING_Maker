"""릴스 자동 제작 + 게시 — 실행 진입점.

사용 예)
    python -m reels.post_reels --dry-run            # 영상만 만들어 data/reels_preview 에 저장(게시 안 함, IG 키 불필요)
    python -m reels.post_reels --count 1            # 새 릴스 1건 제작·게시

글감은 common.sources 에서 가져오며, 항목에 image_url / image_path 가 있으면 배경으로 쓰고 없으면 단색 배경을 씁니다.

주제 모드(.env 의 REEL_SOURCE=topics):
    REEL_TOPICS 에 적은 주제(| 로 구분)를 돌려 가며 AI 가 대본을 쓰고, ID 는 자동으로 만듭니다.
    같은 주제와 같은 문구는 REEL_REPOST_DAYS(기본 15일) 동안 다시 올리지 않습니다(data/state.db 의 topic_history).
"""
from __future__ import annotations

import argparse
import random
import re
import shutil
import sys
import time
from pathlib import Path

from common import ai
from common.config import ConfigError, data_dir, env, env_int, require, resolve_path
from common.disclosure import disclosure_text
from common.notify import send_mail
from common.sources import Item, get_items, pick_items
from common.store import AlreadyHandled, DailyLimitReached, PendingConflict, PostStore
from common.topics import DEFAULT_REPOST_DAYS, TopicStore, new_item_id, parse_topics

from .hosting import host_video, upload_mode
from .instagram_client import InstagramClient, InstagramError
from .make_video import VideoError, build_video, download_image

CHANNEL = "reels"


def script_for(item: Item, use_ai: bool) -> str:
    """나레이션 대본: item.text → (없으면) AI 가 title 로 작성 → (실패하면) title."""
    if item.text:
        return item.text
    if use_ai and item.title:
        prompt = ("30초 안에 읽을 수 있는 짧은 한국어 릴스 대본을 써라(120자 이내). 첫 문장은 시선을 끄는 질문으로 시작. "
                  f"해시태그/링크/이모지 없이 대본만 출력.\n\n주제: {item.title}")
        text = ai.generate_text(prompt, max_chars=200)
        if text:
            return text
    return item.title


def caption_for(item: Item, script: str) -> str:
    parts = [item.title or script]
    if item.link:
        parts.append(item.link)
        if disclosure_text(True):
            parts.append(disclosure_text(True))
    if item.hashtags:
        parts.append(" ".join(f"#{t}" for t in item.hashtags[:5]))
    return "\n\n".join(parts)[:2200]  # 인스타 캡션 한도


def make_for_item(item: Item, out_path: Path, use_ai: bool) -> Path:
    work = data_dir() / "reel_work"
    images: list[Path] = []
    if item.image_path:
        p = resolve_path(item.image_path)
        if p.exists():
            images.append(p)
        else:
            print(f"  (이미지 파일 없음, 단색 배경 사용: {p})")
    elif item.image_url:
        try:
            dl = data_dir() / "reel_dl"  # build_video 가 work 폴더를 비우므로 별도 폴더에 저장
            dl.mkdir(parents=True, exist_ok=True)
            images.append(download_image(item.image_url, dl / "dl_image.jpg"))
        except Exception as e:  # noqa: BLE001
            print(f"  (이미지 다운로드 실패, 단색 배경 사용: {e})")
    return build_video(script_for(item, use_ai), out_path, images=images or None, work_dir=work)


# ── 주제 모드(REEL_SOURCE=topics): .env 의 주제로 AI 가 대본을 쓰고, ID 는 자동으로 만든다 ──
def reel_source() -> str:
    source = (env("REEL_SOURCE", "content") or "content").lower()
    if source not in ("content", "topics"):
        raise ConfigError(f"REEL_SOURCE 는 content 또는 topics 여야 합니다: {source!r}")
    return source


def _hashtags() -> list[str]:
    raw = env("REEL_HASHTAGS", "") or ""
    return [t.lstrip("#") for t in re.split(r"[,\s]+", raw) if t.strip("# ")]


def _background_image() -> str:
    """REEL_IMAGE_DIR 폴더의 이미지 중 하나를 고른다(없으면 단색 배경)."""
    folder = env("REEL_IMAGE_DIR")
    if not folder:
        return ""
    path = resolve_path(folder)
    files = sorted(p for p in path.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")) if path.is_dir() else []
    if not files:
        print(f"  (REEL_IMAGE_DIR 에 이미지가 없어 단색 배경 사용: {path})")
        return ""
    return str(random.choice(files))


def topic_script(topic: str, tstore: TopicStore, days: int) -> str | None:
    """주제로 대본을 만든다. 최근 days 일 안에 올린 것과 같은 문구면 한 번 더 시도한다."""
    last = tstore.last_script(topic)
    for attempt in range(2):
        prompt = ("30초 안에 읽을 수 있는 짧은 한국어 릴스 대본을 써라(120자 이내). 첫 문장은 시선을 끄는 질문으로 시작. "
                  f"해시태그/링크/이모지 없이 대본만 출력.\n\n주제: {topic}")
        if last:
            prompt += f"\n\n지난번 대본(이것과 겹치지 않게 다른 관점·다른 표현으로 쓸 것):\n{last}"
        if attempt:
            prompt += "\n\n방금 만든 문장은 이전에 쓴 것과 같았다. 완전히 다른 문장으로 다시 써라."
        text = ai.generate_text(prompt, max_chars=200)
        if not text:
            return None
        text = text.strip()
        if not tstore.is_recent_script(text, days):
            return text
        print(f"  (주제 '{topic}': 최근 {days}일 안에 올린 문구와 같아 다시 만듭니다)")
    return None


def plan_topic_items(count: int, tstore: TopicStore, days: int) -> list[tuple[Item, str]]:
    """다시 써도 되는 주제를 골라 (글감, 주제) 목록을 만든다. 게시 기록은 남기지 않는다."""
    topics = parse_topics(env("REEL_TOPICS"))
    if not topics:
        raise ConfigError("REEL_SOURCE=topics 인데 REEL_TOPICS 가 비어 있습니다. 예) REEL_TOPICS=주제1|주제2|주제3")
    candidates = tstore.eligible(topics, days)
    if not candidates:
        print(f"[알림] 모든 주제가 최근 {days}일 안에 쓰였습니다. 다시 쓸 수 있는 시각(UTC):")
        for t, when in tstore.next_available(topics, days):
            print(f"  - {t}: {when} 이후")
        print("  → .env 의 REEL_TOPICS 에 주제를 더 추가하면 바로 올릴 수 있습니다.")
        return []
    out: list[tuple[Item, str]] = []
    for topic in candidates:
        if len(out) >= count:
            break
        script = topic_script(topic, tstore, days)
        if not script:
            print(f"  (주제 '{topic}': AI 대본을 만들지 못해 건너뜀 — GEMINI_API_KEY/GROQ_API_KEY 확인)")
            continue
        item = Item(id=new_item_id(topic), title="", text=script,
                    image_path=_background_image(), hashtags=_hashtags())
        out.append((item, topic))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Instagram 릴스 자동 제작·게시")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--daily-limit", type=int, default=None, help="기본: .env REEL_DAILY_LIMIT")
    ap.add_argument("--dry-run", action="store_true", help="영상만 만들고 게시하지 않음")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--keep", action="store_true", help="게시 후에도 영상 파일 보관")
    args = ap.parse_args(argv)

    daily_limit = args.daily_limit or env_int("REEL_DAILY_LIMIT", 5)
    repost_days = env_int("REEL_REPOST_DAYS", DEFAULT_REPOST_DAYS)
    try:
        source = reel_source()
        if source == "topics" and args.no_ai:
            raise ConfigError("주제 모드(REEL_SOURCE=topics)는 AI 가 대본을 씁니다. --no-ai 를 빼고 실행하세요.")
    except ConfigError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        out_dir = data_dir() / "reels_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            if source == "topics":   # 연습: 주제를 고르고 대본·영상만 만든다(기록은 남기지 않음)
                tstore = TopicStore(CHANNEL)
                try:
                    preview = [i for i, _ in plan_topic_items(args.count, tstore, repost_days)]
                finally:
                    tstore.close()
            else:
                preview = get_items()[: args.count]
            for item in preview:
                out = make_for_item(item, out_dir / f"{item.id}.mp4", not args.no_ai)
                print(f"[{item.id}] 영상 생성: {out}\n  캡션:\n{caption_for(item, script_for(item, False))}\n")
        except (VideoError, ConfigError) as e:
            print(f"[오류] {e}", file=sys.stderr)
            return 1
        return 0

    store = PostStore(CHANNEL)
    tstore = TopicStore(CHANNEL) if source == "topics" else None
    topic_of: dict[str, str] = {}
    posted = 0
    try:
        cfg = require("IG_ACCESS_TOKEN", "IG_USER_ID")
        client = InstagramClient(cfg["IG_USER_ID"], cfg["IG_ACCESS_TOKEN"],
                                 version=env("IG_API_VERSION", "v25.0") or "v25.0")
        mode = upload_mode()

        if tstore:
            pairs = plan_topic_items(args.count, tstore, repost_days)
            topic_of = {i.id: t for i, t in pairs}
            targets = [i for i, _ in pairs]
        else:
            targets = pick_items(get_items(), store, args.count)

        for item in targets:
            print(f"[{item.id}] {item.title or item.text[:30]}")
            out = data_dir() / "reels_out" / f"{item.id}.mp4"
            try:
                store.reserve(item.id, daily_limit)
            except DailyLimitReached as e:
                print(f"[중단] {e}")
                break
            except (AlreadyHandled, PendingConflict) as e:
                print(f"[건너뜀] {e}")
                continue

            # 1) 영상 제작 + 업로드 — 실패해도 아직 게시 전이므로 failed 로 확정 가능
            hosted = None
            try:
                make_for_item(item, out, not args.no_ai)
                caption = caption_for(item, script_for(item, False))
                if mode == "resumable":
                    container = client.create_reels_container(caption)
                    client.upload_video(container, str(out))
                else:  # s3 / command: 공개 URL 로 올린 뒤 그 주소를 인스타그램에 알려 줌
                    hosted = host_video(out, mode)
                    container = client.create_reels_container(caption, video_url=hosted.url)
                client.wait_until_ready(container["id"])
            except Exception as e:  # noqa: BLE001
                store.mark_failed(item.id, str(e))
                print(f"  ✗ 실패: {e}")
                if hosted:
                    hosted.cleanup()
                continue

            # 2) 발행 — API 오류면 failed, 네트워크 오류면 결과 불명(pending 유지)
            if tstore:   # 발행 직전에 주제 사용 기록(결과를 모르는 경우에도 같은 주제가 곧바로 다시 올라가지 않도록)
                tstore.record(topic_of[item.id], item.id, item.text)
            try:
                media_id = client.publish(container["id"])
            except InstagramError as e:
                store.mark_failed(item.id, str(e))
                if tstore:   # 확실히 실패 → 기록을 지워 그 주제를 다시 쓸 수 있게
                    tstore.forget(item.id)
                print(f"  ✗ 발행 실패: {e}")
                continue
            finally:
                if hosted:
                    hosted.cleanup()   # 발행 여부와 관계없이 임시 업로드 파일은 지움
            store.mark_published(item.id, media_id)
            posted += 1
            print(f"  ✓ 게시 완료 media_id={media_id}")
            if not args.keep:
                shutil.rmtree(out.parent, ignore_errors=True)
            time.sleep(5)
    except (ConfigError, InstagramError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[릴스 자동 게시 실패]", str(e))
        return 1
    finally:
        store.close()
        if tstore:
            tstore.close()

    print(f"완료: 새로 게시 {posted}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
