"""Threads 장기 토큰 갱신 (발급 후 24시간 이상, 만료 전이어야 함).

    python -m threads.token_refresh --save      # 갱신 후 .env 에 저장
    python -m threads.token_refresh --check     # 토큰이 아직 유효한지만 확인

cron 예) 매주 일요일 03:00 →  0 3 * * 0  ./cron/run.sh threads.token_refresh --save
"""
from __future__ import annotations

import argparse
import sys

import requests

from common.config import ConfigError, require, update_env_file
from common.notify import send_mail


def refresh(token: str) -> tuple[str, int]:
    r = requests.get(
        "https://graph.threads.com/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=30,
    )
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise RuntimeError(f"토큰 갱신 실패: {body}")
    return body["access_token"], int(body.get("expires_in", 0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Threads 토큰 갱신")
    ap.add_argument("--save", action="store_true", help="갱신된 토큰을 .env 에 저장")
    ap.add_argument("--check", action="store_true", help="갱신하지 않고 토큰 유효성만 확인")
    args = ap.parse_args(argv)

    try:
        cfg = require("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
        if args.check:
            r = requests.get(
                "https://graph.threads.com/v1.0/me",
                params={"fields": "id,username", "access_token": cfg["THREADS_ACCESS_TOKEN"]},
                timeout=30,
            )
            body = r.json()
            if r.status_code >= 400:
                raise RuntimeError(f"토큰이 유효하지 않습니다: {body}")
            print(f"토큰 정상: @{body.get('username')}")
            return 0
        token, expires = refresh(cfg["THREADS_ACCESS_TOKEN"])
        print(f"갱신 성공: 유효기간 약 {expires // 86400}일")
        if args.save:
            update_env_file("THREADS_ACCESS_TOKEN", token)
            print(".env 에 저장했습니다.")
        return 0
    except (ConfigError, RuntimeError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[Threads 토큰 갱신 실패]", str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
