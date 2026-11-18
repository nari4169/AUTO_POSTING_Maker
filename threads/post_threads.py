"""Threads 자동 게시 — 실행 진입점.

사용 예)
    python -m threads.post_threads --dry-run            # 문구만 만들어 화면에 출력(키 없이도 가능, 게시 안 함)
    python -m threads.post_threads --count 1            # 새 글 1건 게시
    python -m threads.post_threads --count 3 --daily-limit 10

안전장치:
  * 게시 전에 store.reserve() 로 'pending' 을 선점 → 같은 글이 두 번 올라가지 않음
  * 결과를 알 수 없는 pending 이 남으면 자동 재시도하지 않음(사람이 확인)
  * 24시간 게시 한도 / 실패 3회 초과 항목 제외
  * 본문은 게시됐는데 댓글만 실패하면, 다음 실행 때 댓글만 다시 시도
"""
from __future__ import annotations

import argparse
import sys
import time

from common.config import ConfigError, env, env_int, require
from common.notify import send_mail
from common.sources import get_items, pick_items
from common.store import AlreadyHandled, DailyLimitReached, PendingConflict, PostStore

from .client import ThreadsClient, ThreadsError
from .content import PostContent, build_post

CHANNEL = "threads"


def build_client() -> ThreadsClient:
    cfg = require("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
    return ThreadsClient(
        cfg["THREADS_USER_ID"], cfg["THREADS_ACCESS_TOKEN"], version=env("THREADS_API_VERSION", "v1.0") or "v1.0"
    )


def post_reply_safely(client: ThreadsClient, store: PostStore, item_id: str, media_id: str, text: str) -> None:
    """댓글 실패는 예외로 올리지 않고 기록만 한다(본문을 다시 올리면 중복 게시가 되므로)."""
    try:
        client.reply(text, media_id)
        store.mark_reply_done(item_id)
        print(f"  ↳ 댓글 게시 완료")
    except Exception as e:  # noqa: BLE001
        store.mark_reply_failed(item_id)
        print(f"  ↳ 댓글 실패(다음 실행에서 다시 시도): {e}")


def retry_pending_replies(client: ThreadsClient, store: PostStore) -> None:
    for row in store.pending_replies():
        print(f"[reply-retry] {row['item_id']}")
        post_reply_safely(client, store, row["item_id"], row["remote_id"], row["reply_text"])


def publish_once(client: ThreadsClient, store: PostStore, item, content: PostContent, daily_limit: int | None):
    """reserve → 컨테이너 생성 → 발행 → 기록. 이 순서를 지키는 유일한 곳."""
    store.reserve(item.id, daily_limit)

    # 1) 컨테이너 생성 — 여기서 실패하면 아직 아무것도 게시되지 않았으므로 failed 로 확정해도 안전
    try:
        container = client.create_container(content.body, image_url=item.image_url or None)
        if item.image_url:
            client.wait_until_ready(container)
    except Exception as e:  # noqa: BLE001
        store.mark_failed(item.id, str(e))
        raise

    # 2) 발행 — API 가 에러를 돌려줬다면 게시되지 않은 것. 네트워크 오류는 결과를 모르므로 pending 유지
    try:
        media_id = client.publish(container)
    except ThreadsError as e:
        store.mark_failed(item.id, str(e))
        raise
    # (그 외 requests 예외는 그대로 올라가며 pending 으로 남는다 → 사람이 확인)

    store.mark_published(item.id, media_id)
    print(f"  ✓ 게시 완료 media_id={media_id}")
    if content.reply:
        store.set_reply(item.id, content.reply)
        post_reply_safely(client, store, item.id, media_id, content.reply)
    return media_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Threads 자동 게시")
    ap.add_argument("--count", type=int, default=1, help="이번 실행에서 새로 게시할 글 수 (기본 1)")
    ap.add_argument("--daily-limit", type=int, default=None, help="24시간 최대 게시 수 (기본: .env THREADS_DAILY_LIMIT)")
    ap.add_argument("--dry-run", action="store_true", help="문구만 만들어 출력하고 게시하지 않음")
    ap.add_argument("--no-ai", action="store_true", help="AI 없이 템플릿 문구만 사용")
    ap.add_argument("--delay", type=float, default=5.0, help="글 사이 대기(초)")
    args = ap.parse_args(argv)

    daily_limit = args.daily_limit or env_int("THREADS_DAILY_LIMIT", 10)
    items = get_items()

    if args.dry_run:
        for item in items[: args.count]:
            content = build_post(item, use_ai=not args.no_ai)
            print(f"--- [{item.id}] 본문 ({len(content.body)}자) ---\n{content.body}")
            if content.reply:
                print(f"--- 댓글 ---\n{content.reply}")
            print()
        return 0

    store = PostStore(CHANNEL)
    posted = 0
    try:
        client = build_client()
        retry_pending_replies(client, store)
        for item in pick_items(items, store, args.count):
            print(f"[{item.id}] {item.title or item.text[:30]}")
            content = build_post(item, use_ai=not args.no_ai)
            try:
                publish_once(client, store, item, content, daily_limit)
                posted += 1
            except DailyLimitReached as e:
                print(f"[중단] {e}")
                break
            except (AlreadyHandled, PendingConflict) as e:
                print(f"[건너뜀] {e}")
                continue
            if posted < args.count:
                time.sleep(args.delay)
    except (ConfigError, ThreadsError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[Threads 자동 게시 실패]", str(e))
        return 1
    finally:
        store.close()

    print(f"완료: 새로 게시 {posted}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
