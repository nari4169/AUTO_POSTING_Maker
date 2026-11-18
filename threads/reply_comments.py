"""내 최근 Threads 글에 달린 댓글에 자동으로 답글 달기.

    python -m threads.reply_comments --dry-run
    python -m threads.reply_comments --posts 5 --max-replies 10

  * 필요 권한: threads_read_replies, threads_manage_replies
  * 같은 댓글에 두 번 답하지 않도록 store(channel='threads_reply') 에 기록
  * 답글 문구: AI 가 켜져 있으면 AI, 아니면 .env 의 고정 문구(REPLY_TEXT)
"""
from __future__ import annotations

import argparse
import sys
import time

from common import ai
from common.config import ConfigError, env
from common.store import AlreadyHandled, PendingConflict, PostStore

from .client import ThreadsError
from .post_threads import build_client

CHANNEL = "threads_reply"
DEFAULT_REPLY = "댓글 감사합니다! 🙌"


def make_reply(comment_text: str) -> str:
    prompt = (
        "다음은 내 Threads 글에 달린 댓글이다. 친근하고 짧게(80자 이내) 한국어로 답글을 써라. "
        f"광고/링크는 넣지 말고 답글만 출력한다.\n\n댓글: {comment_text}"
    )
    return ai.generate_text(prompt, max_chars=120) or env("REPLY_TEXT", DEFAULT_REPLY) or DEFAULT_REPLY


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Threads 댓글 자동 답글")
    ap.add_argument("--posts", type=int, default=5, help="확인할 내 최근 글 수")
    ap.add_argument("--max-replies", type=int, default=10, help="이번 실행에서 달 최대 답글 수")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = build_client()
        my_username = client.me().get("username")
        store = PostStore(CHANNEL)
        done = 0
        for post in client.my_threads(limit=args.posts):
            for c in client.replies(post["id"]):
                if done >= args.max_replies:
                    break
                if c.get("username") == my_username or not (c.get("text") or "").strip():
                    continue  # 내 댓글/빈 댓글 제외
                if store.is_blocked(c["id"]):
                    continue
                text = make_reply(c["text"])
                print(f"[{c['id']}] @{c.get('username')}: {c['text'][:40]!r} → {text!r}")
                if args.dry_run:
                    continue
                try:
                    store.reserve(c["id"])
                    new_id = client.reply(text, c["id"])
                    store.mark_published(c["id"], new_id)
                    done += 1
                    time.sleep(3)
                except (AlreadyHandled, PendingConflict):
                    continue
                except ThreadsError as e:
                    store.mark_failed(c["id"], str(e))
                    print(f"  ✗ {e}")
        store.close()
        print(f"완료: 답글 {done}건")
        return 0
    except (ConfigError, ThreadsError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
