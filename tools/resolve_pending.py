"""결과를 알 수 없는 'pending' 항목을 사람이 확인한 뒤 확정하는 도구.

    python -m tools.resolve_pending list
    python -m tools.resolve_pending published threads sample-001 --remote-id 1789...   # 실제로 올라가 있음
    python -m tools.resolve_pending failed    threads sample-001                        # 올라가지 않았음 → 재시도 허용

pending 은 "발행 요청은 보냈는데 응답을 못 받은" 상태입니다. 앱/사이트에서 실제로 올라갔는지 먼저 확인하세요.
(올라간 글을 failed 로 바꾸면 같은 글이 또 올라갑니다!)
"""
from __future__ import annotations

import argparse
import sys

from common.store import PostStore

CHANNELS = ("threads", "threads_reply", "reels", "naver", "telegram")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pending 항목 확정")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="모든 채널의 pending 목록")
    for name in ("published", "failed"):
        p = sub.add_parser(name)
        p.add_argument("channel", choices=CHANNELS)
        p.add_argument("item_id")
        if name == "published":
            p.add_argument("--remote-id", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        found = False
        for ch in CHANNELS:
            store = PostStore(ch)
            for row in store.list_pending():
                found = True
                print(f"[{ch}] {row['item_id']}  (마지막 갱신 {row['updated_at']})")
            store.close()
        if not found:
            print("pending 항목이 없습니다.")
        return 0

    store = PostStore(args.channel)
    if store.get(args.item_id) is None:
        print("해당 항목이 없습니다.", file=sys.stderr)
        return 1
    store.resolve_pending(args.item_id, published=(args.cmd == "published"),
                          remote_id=getattr(args, "remote_id", None))
    store.close()
    print("확정했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
