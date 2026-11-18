"""Instagram 토큰 점검/갱신.

    python -m reels.token_tool check [--save]   # 토큰이 유효한지, 어떤 계정인지 확인(--save: IG_USER_ID 자동 저장)
    python -m reels.token_tool refresh --save   # 장기 토큰 갱신(발급 24시간 후 ~ 만료 전) 후 .env 저장

cron 예) 매주 월요일 03:00 →  0 3 * * 1  ./cron/run.sh reels.token_tool refresh --save
"""
from __future__ import annotations

import argparse
import sys

import requests

from common.config import ConfigError, env, require, update_env_file
from common.notify import send_mail
from .instagram_client import InstagramClient, InstagramError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Instagram 토큰 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("--save", action="store_true", help="확인된 user_id 를 .env 의 IG_USER_ID 에 저장")
    rf = sub.add_parser("refresh")
    rf.add_argument("--save", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "check":
            # IG_USER_ID 를 아직 모르는 상태에서도 쓸 수 있도록 토큰만 필수로 한다(/me 로 user_id 를 알아냄)
            cfg = require("IG_ACCESS_TOKEN")
            me = InstagramClient(env("IG_USER_ID", "me") or "me", cfg["IG_ACCESS_TOKEN"],
                                 version=env("IG_API_VERSION", "v25.0") or "v25.0").me()
            print(f"토큰 정상: @{me.get('username')} (user_id={me.get('user_id')})")
            if args.save and me.get("user_id"):
                update_env_file("IG_USER_ID", str(me["user_id"]))
                print(".env 의 IG_USER_ID 에 저장했습니다.")
            return 0
        cfg = require("IG_ACCESS_TOKEN")
        r = requests.get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": cfg["IG_ACCESS_TOKEN"]},
            timeout=30,
        )
        body = r.json()
        if r.status_code >= 400 or "access_token" not in body:
            raise RuntimeError(f"토큰 갱신 실패: {body}")
        print(f"갱신 성공: 유효기간 약 {int(body.get('expires_in', 0)) // 86400}일")
        if args.save:
            update_env_file("IG_ACCESS_TOKEN", body["access_token"])
            print(".env 에 저장했습니다.")
        return 0
    except (ConfigError, RuntimeError, InstagramError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[Instagram 토큰 점검/갱신 실패]", str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
