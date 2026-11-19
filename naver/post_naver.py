"""네이버 블로그 자동 글쓰기 (Playwright).

사용 예)
    python -m naver.login_setup                    # 최초 1회: 사람이 직접 로그인해 세션 저장
    python -m naver.post_naver --dry-run           # 글을 입력까지만 하고 발행하지 않음(화면으로 확인)
    python -m naver.post_naver --count 1           # 새 글 1건 발행

종료 코드:  0 정상 / 1 일반 오류 / 2 로그인 세션 만료 / 3 발행 실패(봇 검증·한도 등 의심)

⚠️ 네이버에는 블로그 글쓰기 공식 API 가 없어 "사람이 하는 클릭/입력"을 흉내 냅니다.
   - 에디터 화면(CSS 클래스)이 바뀌면 동작하지 않을 수 있습니다. 실패하면 data/debug/ 의 스크린샷을 확인하세요.
   - 과도한 자동 게시는 계정 제한 사유가 될 수 있습니다. 하루 몇 건 + 긴 간격으로 운영하세요.
   - 네이버 서비스 약관/운영정책을 직접 확인하고, 본인 블로그에만 사용하세요.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

from common.config import ConfigError, data_dir, env, env_bool, env_int, require
from common.notify import send_mail
from common.sources import get_items, pick_items
from common.store import AlreadyHandled, PendingConflict, PostStore

from .browser import launch_browser
from .compose import BlogPost, build_post
from .login_setup import state_path

CHANNEL = "naver"
EXIT_LOGIN_EXPIRED = 2
EXIT_PUBLISH_FAILED = 3


class LoginExpired(RuntimeError):
    pass


class PublishFailed(RuntimeError):
    """발행 단계 실패 — 실제로 올라갔는지 알 수 없을 수 있음(pending 유지)."""


class NotSent(PublishFailed):
    """발행 버튼을 누르기 전의 실패 — 아무것도 게시되지 않았음이 확실(failed 로 확정 가능)."""


# ── 화면 요소를 찾는 선택자(CSS) 목록 ─────────────────────────────────────────
# 네이버 에디터 화면이 바뀌면 이 목록만 고치면 됩니다. 앞에서부터 차례로 시도합니다.
# 어떤 선택자가 지금도 맞는지는 `python -m naver.inspect_editor` 로 점검할 수 있습니다.
TITLE_SELECTORS = [
    ".se-documentTitle .se-text-paragraph", ".se-title-text .se-text-paragraph",
    "div.se-component.se-documentTitle span.se-placeholder", ".se-title-text",
]
BODY_SELECTORS = [
    "div.se-component.se-text .se-text-paragraph", ".se-component.se-text", ".se-content .se-text-paragraph",
]
POPUP_CANCEL_SELECTORS = [".se-popup-button-cancel", "button:has-text('취소')"]
HELP_CLOSE_SELECTORS = [".se-help-panel-close-button", "button[aria-label*='닫기']"]


# ── 에디터 조작 도우미 ───────────────────────────────────────────────────────
def _roots(page):
    """페이지 + 모든 iframe (네이버 에디터는 mainFrame 안에 있을 수 있음)."""
    return [page] + [f for f in page.frames if f is not page.main_frame]


def _find_editor(page, timeout: float = 40.0):
    """에디터(.se-content)가 있는 frame 을 찾아 반환."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "nidlogin" in page.url or "nid.naver.com" in page.url:
            raise LoginExpired("로그인 페이지로 이동됨")
        for root in _roots(page):
            try:
                if root.locator(".se-content, .se-title-text").count() > 0:
                    return root
            except Exception:
                continue
        time.sleep(0.5)
    raise NotSent("글쓰기 에디터를 찾지 못했습니다(화면 구조 변경 또는 로딩 지연).")


def _click_first(page, selectors: list[str], *, timeout_ms: int = 3000, roots=None) -> bool:
    """selectors 중 먼저 보이는 것을 클릭. page/모든 frame 에서 탐색."""
    for root in roots or _roots(page):
        for sel in selectors:
            try:
                loc = root.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=timeout_ms)
                    return True
            except Exception:
                continue
    return False


def _dismiss_popups(page) -> None:
    """'작성 중인 글이 있습니다' 복구 팝업(→ 반드시 '취소')과 도움말 패널을 닫는다."""
    for root in _roots(page):
        try:
            if root.locator("text=작성 중인 글이 있습니다").count() > 0:
                # '확인'을 누르면 예전 글을 불러오므로 '취소'를 눌러야 한다
                _click_first(page, POPUP_CANCEL_SELECTORS, roots=[root])
        except Exception:
            pass
    _click_first(page, HELP_CLOSE_SELECTORS, timeout_ms=1000)


def _type_title(page, editor, title: str) -> None:
    ok = _click_first(page, TITLE_SELECTORS, roots=[editor])
    if not ok:
        raise NotSent("제목 입력칸을 찾지 못했습니다.")
    page.keyboard.type(title, delay=30)


def _type_body(page, editor, body: str) -> None:
    ok = _click_first(page, BODY_SELECTORS, roots=[editor])
    if not ok:
        raise NotSent("본문 입력칸을 찾지 못했습니다.")
    lines = body.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.type(line, delay=8)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")


PUBLISH_SELECTORS = [
    "[class*='publish_btn__']", "[class*='publish_btn__'] button",
    "[role='button'][aria-label*='발행']", "header button:has-text('발행')", "button:has-text('발행')",
]
CONFIRM_SELECTORS = [
    "[class*='confirm_btn__']", "[class*='confirm_btn__'] button",
    "[role='dialog'] button:has-text('발행')", "button:has-text('발행')", "button:has-text('확인')",
]


def _publish(page, blog_id: str, timeout: float = 60.0) -> str:
    """'발행' 2단계(헤더 버튼 → 설정 레이어의 발행 버튼)를 누르고 게시글 주소를 돌려준다."""
    _dismiss_popups(page)
    page.wait_for_timeout(1500)
    if not _click_first(page, PUBLISH_SELECTORS):
        raise NotSent("발행 버튼을 찾지 못했습니다.")
    page.wait_for_timeout(2500)  # 발행 설정 레이어가 뜰 시간
    if not _click_first(page, CONFIRM_SELECTORS):
        raise PublishFailed("발행 확인 버튼을 찾지 못했습니다.")

    # 발행되면 글쓰기 화면을 떠나 게시글 주소(.../{blog_id}/{logNo} 또는 PostView)로 이동한다
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = page.url
        if re.search(rf"blog\.naver\.com/{re.escape(blog_id)}/\d+", url) or "PostView" in url or "logNo=" in url:
            return url
        time.sleep(1)
    raise PublishFailed("발행 후 게시글 화면으로 이동하지 않았습니다(발행 여부 확인 필요).")


def _debug_screenshot(page, name: str) -> None:
    try:
        d = data_dir() / "debug"
        d.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(d / f"{name}.png"), full_page=True)
    except Exception:
        pass


# ── 1건 게시 ─────────────────────────────────────────────────────────────────
def write_post(context, blog_id: str, post: BlogPost, *, dry_run: bool) -> str | None:
    page = context.new_page()
    try:
        page.goto(f"https://blog.naver.com/{blog_id}?Redirect=Write", wait_until="domcontentloaded")
        editor = _find_editor(page)
        page.wait_for_timeout(1500)
        _dismiss_popups(page)
        _type_title(page, editor, post.title)
        _type_body(page, editor, post.body)
        if dry_run:
            print("  (dry-run) 입력만 하고 발행하지 않습니다. 화면을 확인하세요(30초 후 종료).")
            page.wait_for_timeout(30000)
            return None
        return _publish(page, blog_id)
    except Exception:
        _debug_screenshot(page, f"fail_{int(time.time())}")
        raise
    finally:
        page.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="네이버 블로그 자동 글쓰기")
    ap.add_argument("--count", type=int, default=None, help="이번 실행 최대 글 수 (기본: .env NAVER_MAX_POSTS)")
    ap.add_argument("--dry-run", action="store_true", help="입력까지만 하고 발행하지 않음(화면 필요)")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--preview", action="store_true", help="브라우저 없이 만들어질 제목/본문만 출력")
    args = ap.parse_args(argv)

    count = args.count if args.count is not None else env_int("NAVER_MAX_POSTS", 1)
    items = get_items()

    if args.preview:
        for item in items[:count]:
            post = build_post(item, use_ai=not args.no_ai)
            print(f"--- [{item.id}] {post.title} ---\n{post.body}\n")
        return 0

    from playwright.sync_api import sync_playwright

    store = PostStore(CHANNEL)
    posted = 0
    try:
        blog_id = require("NAVER_BLOG_ID")["NAVER_BLOG_ID"]
        path = state_path()
        if not path.exists():
            raise LoginExpired("저장된 로그인 세션이 없습니다. `python -m naver.login_setup` 을 먼저 실행하세요.")

        with sync_playwright() as p:
            headless = env_bool("NAVER_HEADLESS", False) and not args.dry_run
            browser = launch_browser(p, headless=headless)
            context = browser.new_context(storage_state=str(path), locale="ko-KR",
                                          viewport={"width": 1280, "height": 900})
            try:
                targets = pick_items(items, store, count)
                for n, item in enumerate(targets):
                    print(f"[{item.id}] {item.title or item.text[:30]}")
                    post = build_post(item, use_ai=not args.no_ai)
                    if args.dry_run:
                        write_post(context, blog_id, post, dry_run=True)
                        continue
                    try:
                        store.reserve(item.id)
                    except (AlreadyHandled, PendingConflict) as e:
                        print(f"[건너뜀] {e}")
                        continue
                    try:
                        url = write_post(context, blog_id, post, dry_run=False)
                    except LoginExpired:
                        store.mark_failed(item.id, "로그인 만료")
                        raise
                    except NotSent as e:
                        store.mark_failed(item.id, str(e))
                        print(f"  ✗ {e}")
                        raise
                    except PublishFailed as e:
                        # 발행 버튼 이후 단계의 실패는 실제로 올라갔을 수도 있으므로 pending 유지
                        print(f"  ✗ {e}\n  → 실제로 올라갔는지 블로그에서 확인하세요. "
                              "`python -m tools.resolve_pending list`")
                        raise
                    store.mark_published(item.id, url)
                    posted += 1
                    print(f"  ✓ 발행 완료 {url}")
                    if n < len(targets) - 1:
                        wait = env_int("NAVER_POST_INTERVAL_SEC", 120)
                        time.sleep(wait + random.randint(0, 30))
            finally:
                # 세션 쿠키가 갱신됐을 수 있으므로 다시 저장
                try:
                    context.storage_state(path=str(path))
                except Exception:
                    pass
                browser.close()
    except LoginExpired as e:
        msg = f"네이버 로그인이 만료됐습니다: {e}\n화면이 있는 환경에서 `python -m naver.login_setup` 을 다시 실행하세요."
        print(f"[오류] {msg}", file=sys.stderr)
        send_mail("[네이버 블로그] 로그인 만료", msg)
        return EXIT_LOGIN_EXPIRED
    except PublishFailed as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[네이버 블로그] 발행 실패", str(e))
        return EXIT_PUBLISH_FAILED
    except ConfigError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    finally:
        store.close()

    print(f"완료: 새로 발행 {posted}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
