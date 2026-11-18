"""새 채널 추가 연습: 텔레그램 봇으로 메시지 보내기 (7장).

이 파일은 "새 채널을 붙일 때 어떤 틀을 따르는가"를 보여 주려는 가장 작은 예제입니다.
  글감(common.sources) → 글 만들기 → 게시 → 기록(common.store)  ← 3·4·5장과 같은 뼈대

준비
  1) 텔레그램에서 @BotFather 로 봇을 만들고 토큰을 받는다
  2) 그 봇에게 아무 메시지나 보낸 뒤, 메시지를 받을 채팅 ID 를 확인한다
  3) .env 에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력

실행
    python -m examples.telegram_post --dry-run
    python -m examples.telegram_post --count 1
"""
from __future__ import annotations

import argparse
import sys

import requests

from common.config import ConfigError, env, require
from common.notify import send_mail
from common.sources import Item, get_items, pick_items
from common.store import AlreadyHandled, PendingConflict, PostStore

CHANNEL = "telegram"
DEFAULT_DISCLOSURE = "이 포스팅은 제휴 마케팅 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받을 수 있습니다."


class TelegramError(Exception):
    """텔레그램 API 가 '실패'라고 답한 경우(= 메시지가 가지 않았음이 확실)."""


def compose(item: Item) -> str:
    """글 만들기: 제목 + 본문 (+ 링크가 있으면 링크와 대가성 문구)."""
    parts = [p for p in (item.title, item.text) if p]
    if item.link:
        parts += [item.link, env("AD_DISCLOSURE", DEFAULT_DISCLOSURE) or DEFAULT_DISCLOSURE]
    return "\n\n".join(parts)[:4096]  # 텔레그램 메시지 글자 수 한도


def send_message(token: str, chat_id: str, text: str) -> str:
    """게시: API 를 부르고 메시지 번호를 돌려준다."""
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    body = r.json()
    if not body.get("ok"):
        raise TelegramError(f"텔레그램 오류: {body.get('description', r.text[:200])}")
    return str(body["result"]["message_id"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="텔레그램 메시지 보내기(새 채널 예제)")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    items = get_items()
    if args.dry_run:
        for item in items[: args.count]:
            print(f"--- [{item.id}] ---\n{compose(item)}\n")
        return 0

    store = PostStore(CHANNEL)
    posted = 0
    try:
        cfg = require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        for item in pick_items(items, store, args.count):
            try:
                store.reserve(item.id)                       # ① 먼저 pending 으로 선점
            except (AlreadyHandled, PendingConflict) as e:
                print(f"[건너뜀] {e}")
                continue
            try:
                message_id = send_message(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"], compose(item))  # ② 게시
            except TelegramError as e:                       # API 가 "안 갔다"고 답함 → failed 로 확정해도 안전
                store.mark_failed(item.id, str(e))
                print(f"  ✗ {e}")
                continue
            # (네트워크 오류는 잡지 않는다 → pending 으로 남음 = 결과를 모르므로 사람이 확인)
            store.mark_published(item.id, message_id)        # ③ 성공 기록
            posted += 1
            print(f"  ✓ 전송 완료 message_id={message_id}")
    except ConfigError as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[텔레그램 전송 실패]", str(e))
        return 1
    finally:
        store.close()
    print(f"완료: 새로 전송 {posted}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
