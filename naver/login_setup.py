"""네이버 로그인 세션 저장 — "사람이 직접" 한 번만 로그인합니다.

네이버는 자동 로그인(아이디/비밀번호 입력)을 봇으로 감지하고, 캡차/2단계 인증도 나오므로
이 프로그램은 비밀번호를 받지 않습니다. 브라우저 창이 뜨면 직접 로그인하세요.

    python -m naver.login_setup            # 화면이 있는 환경(PC, 또는 파이의 VNC 데스크톱)에서 실행
    python -m naver.login_setup --check    # 저장된 세션이 아직 유효한지 확인(화면 없이 가능)

저장 위치: .env 의 NAVER_STATE_PATH (기본 data/naver_state.json) — 비밀번호급 파일이니 공유하지 마세요.
세션이 풀리면(종료 코드 2) 이 절차를 다시 하면 됩니다.
"""
from __future__ import annotations

import argparse
import sys
import time

from common.config import env, resolve_path

LOGIN_URL = "https://nid.naver.com/nidlogin.login"
CHECK_URL = "https://nid.naver.com/user2/help/myInfoV2?lang=ko_KR"


def state_path():
    return resolve_path(env("NAVER_STATE_PATH", "data/naver_state.json") or "data/naver_state.json")


def has_login_cookies(context) -> bool:
    names = {c["name"] for c in context.cookies()}
    return "NID_AUT" in names and "NID_SES" in names


def is_logged_in(page) -> bool:
    """내 정보 페이지가 로그인 페이지로 튕기지 않으면 로그인 상태."""
    page.goto(CHECK_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    return "nidlogin" not in page.url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="네이버 로그인 세션 저장")
    ap.add_argument("--check", action="store_true", help="저장된 세션이 유효한지만 확인")
    ap.add_argument("--timeout", type=int, default=300, help="로그인 대기 시간(초)")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if args.check:
            if not path.exists():
                print("저장된 세션이 없습니다. `python -m naver.login_setup` 으로 먼저 로그인하세요.")
                return 2
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(path), locale="ko-KR")
            ok = is_logged_in(context.new_page())
            browser.close()
            print("로그인 상태입니다." if ok else "로그인이 풀렸습니다. 다시 로그인해 주세요.")
            return 0 if ok else 2

        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(LOGIN_URL)
        print("열린 브라우저에서 네이버에 직접 로그인하세요. (로그인 상태 유지 체크 권장)")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if has_login_cookies(context):
                page.wait_for_timeout(2000)  # 쿠키가 모두 자리 잡을 시간
                break
            time.sleep(1)
        else:
            browser.close()
            print("시간 초과: 로그인이 확인되지 않았습니다.", file=sys.stderr)
            return 1

        context.storage_state(path=str(path))
        browser.close()
    print(f"세션을 저장했습니다: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
