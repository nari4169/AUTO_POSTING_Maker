"""브라우저 실행 도우미 — Playwright 기본 Chromium 을 못 쓰는 환경(예: Debian 11)에서 시스템 Chromium 으로 대신한다.

Playwright 는 지원하는 리눅스 버전(Debian 12/13, Ubuntu 22.04/24.04 의 arm64/x86-64)에서만
자체 Chromium 을 내려받습니다(`playwright install chromium`). 그 밖의 OS(예: 라즈베리파이 OS Bullseye = Debian 11)에서는
"Playwright does not support chromium on debian11-arm64" 오류가 납니다. 이때의 대안:

  1) 시스템에 이미 설치된 Chromium 을 쓴다. 라즈베리파이 OS 에는 보통 chromium-browser 가 설치돼 있다:
         which chromium-browser chromium        (경로가 나오면 이미 있는 것 — 새로 설치하지 말 것)
     → 이 모듈이 자동으로 찾아 쓰거나, .env 의 NAVER_BROWSER_PATH 에 실행 파일 경로를 적으면 그것을 쓴다.
     ※ 라즈베리파이 OS 에서 `apt install chromium` 을 하면 라즈베리파이용 chromium-browser 가 제거될 수 있다.
  2) OS 를 지원 버전(Bookworm 이상)으로 새로 설치한다(권장).

실행 순서: NAVER_BROWSER_PATH 지정 → Playwright 기본 브라우저 → (기본이 없으면) 시스템 Chromium 자동 탐색
"""
from __future__ import annotations

import shutil

from common.config import env

SYSTEM_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def find_system_browser() -> str | None:
    for name in SYSTEM_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def launch_browser(p, *, headless: bool):
    """Playwright(sync) 의 chromium 을 실행해 browser 를 돌려준다."""
    explicit = env("NAVER_BROWSER_PATH")
    if explicit:
        return p.chromium.launch(executable_path=explicit, headless=headless)
    try:
        return p.chromium.launch(headless=headless)
    except Exception as e:  # noqa: BLE001 - 기본 브라우저가 없을 때만 대체 경로를 시도한다
        if "Executable doesn't exist" not in str(e) and "playwright install" not in str(e):
            raise
        path = find_system_browser()
        if not path:
            raise RuntimeError(
                "사용할 수 있는 Chromium 이 없습니다. 다음 중 하나를 하세요:\n"
                "  1) which chromium-browser chromium 으로 이미 설치돼 있는지 확인 (라즈베리파이 OS 는 보통 chromium-browser)\n"
                "     없으면: sudo apt install -y chromium-browser   (일반 데비안/우분투는 chromium)\n"
                "  2) .env 의 NAVER_BROWSER_PATH 에 브라우저 실행 파일 경로를 적기\n"
                "  3) 라즈베리파이 OS 를 Bookworm 이상으로 새로 설치(권장)"
            ) from e
        print(f"[브라우저] Playwright 기본 Chromium 이 없어 시스템 브라우저를 사용합니다: {path}")
        return p.chromium.launch(executable_path=path, headless=headless)
