"""네이버 글쓰기 화면 점검 도구 — 에디터 화면이 바뀌어 post_naver 가 멈출 때 원인을 찾는 진단용입니다.

    python -m naver.inspect_editor

저장된 로그인 세션으로 글쓰기 화면을 열어(브라우저 창이 보입니다) 다음을 출력합니다.
  1) 화면의 frame 구조
  2) post_naver.py 의 선택자(제목/본문/발행 등)가 지금 화면에서 각각 몇 개 찾아지는지
  3) 화면에 보이는 버튼들의 글자·class (새 선택자를 고를 때 참고)
그리고 data/debug/inspect.png 에 화면을 저장합니다. 아무것도 입력·발행하지 않습니다.
"""
from __future__ import annotations

import sys

from common.config import data_dir

from . import post_naver as pn
from .browser import launch_browser
from .login_setup import state_path

JS_BUTTONS = """
() => Array.from(document.querySelectorAll('button, [role="button"], a'))
  .map(b => ({text: (b.innerText || '').trim().slice(0, 18),
              cls: String(b.className || '').slice(0, 50),
              aria: b.getAttribute('aria-label') || ''}))
  .filter(x => x.text || x.aria)
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    from common.config import ConfigError, require
    try:
        blog_id = require("NAVER_BLOG_ID")["NAVER_BLOG_ID"]
    except ConfigError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    path = state_path()
    if not path.exists():
        print("저장된 세션이 없습니다. 먼저 `python -m naver.login_setup` 을 실행하세요.")
        return 2

    groups = {
        "제목": pn.TITLE_SELECTORS, "본문": pn.BODY_SELECTORS,
        "발행(1차)": pn.PUBLISH_SELECTORS, "발행(2차 확인)": pn.CONFIRM_SELECTORS,
        "복구 팝업 취소": pn.POPUP_CANCEL_SELECTORS, "도움말 닫기": pn.HELP_CLOSE_SELECTORS,
    }
    with sync_playwright() as p:
        browser = launch_browser(p, headless=False)
        context = browser.new_context(storage_state=str(path), locale="ko-KR", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(f"https://blog.naver.com/{blog_id}?Redirect=Write", wait_until="domcontentloaded")
        try:
            pn._find_editor(page)
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 에디터를 찾지 못했습니다: {e}")
        page.wait_for_timeout(3000)

        print("== frame 구조 ==")
        for i, root in enumerate(pn._roots(page)):
            print(f"  [{i}] {(root.url or '')[:100]}")

        print("\n== 선택자별 찾은 개수 (0이면 화면이 바뀐 것) ==")
        for name, sels in groups.items():
            print(f" [{name}]")
            for sel in sels:
                total = 0
                for root in pn._roots(page):
                    try:
                        total += root.locator(sel).count()
                    except Exception:
                        pass
                print(f"   {total:>3}  {sel}")

        print("\n== 화면의 버튼(글자 / class / aria-label) — 앞 40개 ==")
        shown = 0
        for root in pn._roots(page):
            try:
                for b in root.evaluate(JS_BUTTONS):
                    print(f"   {b['text']!r:22} {b['cls']!r:54} {b['aria']!r}")
                    shown += 1
                    if shown >= 40:
                        break
            except Exception:
                continue
            if shown >= 40:
                break

        out = data_dir() / "debug"
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / "inspect.png"), full_page=True)
        print(f"\n화면 저장: {out / 'inspect.png'}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
