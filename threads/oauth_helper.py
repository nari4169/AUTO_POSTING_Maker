"""Threads 토큰 발급 도우미 (서버 없이 브라우저 + 터미널만으로).

순서:
  1) python -m threads.oauth_helper auth-url
        → 출력된 주소를 브라우저에서 열고, Threads 계정으로 로그인해 권한을 허용합니다.
  2) 허용하면 THREADS_REDIRECT_URI 로 이동하는데(페이지가 열리지 않아도 괜찮습니다),
     주소창의 전체 URL 을 복사합니다.  예) https://example.com/threads/callback?code=AQB...#_
  3) python -m threads.oauth_helper exchange "<복사한 URL>" --save
        → 단기 토큰 → 60일 장기 토큰으로 바꿔서 .env 의 THREADS_ACCESS_TOKEN / THREADS_USER_ID 에 저장합니다.

장기 토큰은 60일 뒤 만료되므로 `python -m threads.token_refresh --save` 를 주 1회 cron 으로 실행하세요.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from common.config import ConfigError, require, update_env_file

SCOPES = "threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies"
AUTH_URL = "https://threads.com/oauth/authorize"
TOKEN_URL = "https://graph.threads.com/oauth/access_token"
LONG_URL = "https://graph.threads.com/access_token"


def build_auth_url() -> str:
    cfg = require("THREADS_APP_ID", "THREADS_REDIRECT_URI")
    query = urlencode(
        {
            "client_id": cfg["THREADS_APP_ID"],
            "redirect_uri": cfg["THREADS_REDIRECT_URI"],
            "scope": SCOPES,
            "response_type": "code",
        }
    )
    return f"{AUTH_URL}?{query}"


def extract_code(redirect_url_or_code: str) -> str:
    value = redirect_url_or_code.strip()
    if "code=" in value:
        qs = parse_qs(urlparse(value).query)
        code = (qs.get("code") or [""])[0]
    else:
        code = value
    return code.split("#")[0]  # 주소 끝의 '#_' 제거


def exchange(code: str) -> tuple[str, str, int]:
    """code → 단기 토큰 → 장기 토큰. (long_token, user_id, expires_in_seconds)"""
    cfg = require("THREADS_APP_ID", "THREADS_APP_SECRET", "THREADS_REDIRECT_URI")
    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": cfg["THREADS_APP_ID"],
            "client_secret": cfg["THREADS_APP_SECRET"],
            "grant_type": "authorization_code",
            "redirect_uri": cfg["THREADS_REDIRECT_URI"],
            "code": code,
        },
        timeout=30,
    )
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise RuntimeError(f"단기 토큰 발급 실패: {body}")
    short_token, user_id = body["access_token"], str(body["user_id"])

    r = requests.get(
        LONG_URL,
        params={
            "grant_type": "th_exchange_token",
            "client_secret": cfg["THREADS_APP_SECRET"],
            "access_token": short_token,
        },
        timeout=30,
    )
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise RuntimeError(f"장기 토큰 교환 실패: {body}")
    return body["access_token"], user_id, int(body.get("expires_in", 0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Threads 토큰 발급 도우미")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth-url", help="로그인/권한 동의용 주소 출력")
    ex = sub.add_parser("exchange", help="리디렉션된 URL(또는 code)을 장기 토큰으로 교환")
    ex.add_argument("redirect_url", help="주소창에서 복사한 전체 URL 또는 code 값")
    ex.add_argument("--save", action="store_true", help=".env 에 THREADS_ACCESS_TOKEN / THREADS_USER_ID 저장")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "auth-url":
            print("아래 주소를 브라우저에서 열어 로그인/허용하세요:\n")
            print(build_auth_url())
            return 0
        token, user_id, expires = exchange(extract_code(args.redirect_url))
        print(f"발급 성공: user_id={user_id}, 유효기간 약 {expires // 86400}일")
        if args.save:
            update_env_file("THREADS_ACCESS_TOKEN", token)
            update_env_file("THREADS_USER_ID", user_id)
            print(".env 에 저장했습니다.")
        else:
            print("--save 옵션을 주면 .env 에 자동 저장됩니다. (토큰은 화면에 출력하지 않았습니다)")
        return 0
    except (ConfigError, RuntimeError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
