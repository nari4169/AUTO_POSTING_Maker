"""릴스 자동 제작 + 게시 — 실행 진입점.

사용 예)
    python -m reels.post_reels --dry-run            # 영상만 만들어 data/reels_preview 에 저장(게시 안 함, IG 키 불필요)
    python -m reels.post_reels --count 1            # 새 릴스 1건 제작·게시

글감은 common.sources 에서 가져오며, 항목에 image_url / image_path 가 있으면 배경으로 쓰고 없으면 단색 배경을 씁니다.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from common import ai
from common.config import ConfigError, data_dir, env, env_int, require, resolve_path
from common.disclosure import disclosure_text
from common.notify import send_mail
from common.sources import Item, get_items, pick_items
from common.store import AlreadyHandled, DailyLimitReached, PendingConflict, PostStore

from .hosting import host_video, upload_mode
from .instagram_client import InstagramClient, InstagramError
from .make_video import VideoError, build_video, download_image

CHANNEL = "reels"


def script_for(item: Item, use_ai: bool) -> str:
    """나레이션 대본: item.text → (없으면) AI 가 title 로 작성 → (실패하면) title."""
    if item.text:
        return item.text
    if use_ai and item.title:
        prompt = ("30초 안에 읽을 수 있는 짧은 한국어 릴스 대본을 써라(120자 이내). 첫 문장은 시선을 끄는 질문으로 시작. "
                  f"해시태그/링크/이모지 없이 대본만 출력.\n\n주제: {item.title}")
        text = ai.generate_text(prompt, max_chars=200)
        if text:
            return text
    return item.title


def caption_for(item: Item, script: str) -> str:
    parts = [item.title or script]
    if item.link:
        parts.append(item.link)
        if disclosure_text(True):
            parts.append(disclosure_text(True))
    if item.hashtags:
        parts.append(" ".join(f"#{t}" for t in item.hashtags[:5]))
    return "\n\n".join(parts)[:2200]  # 인스타 캡션 한도


def make_for_item(item: Item, out_path: Path, use_ai: bool) -> Path:
    work = data_dir() / "reel_work"
    images: list[Path] = []
    if item.image_path:
        p = resolve_path(item.image_path)
        if p.exists():
            images.append(p)
        else:
            print(f"  (이미지 파일 없음, 단색 배경 사용: {p})")
    elif item.image_url:
        try:
            dl = data_dir() / "reel_dl"  # build_video 가 work 폴더를 비우므로 별도 폴더에 저장
            dl.mkdir(parents=True, exist_ok=True)
            images.append(download_image(item.image_url, dl / "dl_image.jpg"))
        except Exception as e:  # noqa: BLE001
            print(f"  (이미지 다운로드 실패, 단색 배경 사용: {e})")
    return build_video(script_for(item, use_ai), out_path, images=images or None, work_dir=work)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Instagram 릴스 자동 제작·게시")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--daily-limit", type=int, default=None, help="기본: .env REEL_DAILY_LIMIT")
    ap.add_argument("--dry-run", action="store_true", help="영상만 만들고 게시하지 않음")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--keep", action="store_true", help="게시 후에도 영상 파일 보관")
    args = ap.parse_args(argv)

    daily_limit = args.daily_limit or env_int("REEL_DAILY_LIMIT", 5)
    items = get_items()

    if args.dry_run:
        out_dir = data_dir() / "reels_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            for item in items[: args.count]:
                out = make_for_item(item, out_dir / f"{item.id}.mp4", not args.no_ai)
                print(f"[{item.id}] 영상 생성: {out}\n  캡션:\n{caption_for(item, script_for(item, False))}\n")
        except VideoError as e:
            print(f"[오류] {e}", file=sys.stderr)
            return 1
        return 0

    store = PostStore(CHANNEL)
    posted = 0
    try:
        cfg = require("IG_ACCESS_TOKEN", "IG_USER_ID")
        client = InstagramClient(cfg["IG_USER_ID"], cfg["IG_ACCESS_TOKEN"],
                                 version=env("IG_API_VERSION", "v25.0") or "v25.0")
        mode = upload_mode()

        for item in pick_items(items, store, args.count):
            print(f"[{item.id}] {item.title or item.text[:30]}")
            out = data_dir() / "reels_out" / f"{item.id}.mp4"
            try:
                store.reserve(item.id, daily_limit)
            except DailyLimitReached as e:
                print(f"[중단] {e}")
                break
            except (AlreadyHandled, PendingConflict) as e:
                print(f"[건너뜀] {e}")
                continue

            # 1) 영상 제작 + 업로드 — 실패해도 아직 게시 전이므로 failed 로 확정 가능
            hosted = None
            try:
                make_for_item(item, out, not args.no_ai)
                caption = caption_for(item, script_for(item, False))
                if mode == "resumable":
                    container = client.create_reels_container(caption)
                    client.upload_video(container, str(out))
                else:  # s3 / command: 공개 URL 로 올린 뒤 그 주소를 인스타그램에 알려 줌
                    hosted = host_video(out, mode)
                    container = client.create_reels_container(caption, video_url=hosted.url)
                client.wait_until_ready(container["id"])
            except Exception as e:  # noqa: BLE001
                store.mark_failed(item.id, str(e))
                print(f"  ✗ 실패: {e}")
                if hosted:
                    hosted.cleanup()
                continue

            # 2) 발행 — API 오류면 failed, 네트워크 오류면 결과 불명(pending 유지)
            try:
                media_id = client.publish(container["id"])
            except InstagramError as e:
                store.mark_failed(item.id, str(e))
                print(f"  ✗ 발행 실패: {e}")
                continue
            finally:
                if hosted:
                    hosted.cleanup()   # 발행 여부와 관계없이 임시 업로드 파일은 지움
            store.mark_published(item.id, media_id)
            posted += 1
            print(f"  ✓ 게시 완료 media_id={media_id}")
            if not args.keep:
                shutil.rmtree(out.parent, ignore_errors=True)
            time.sleep(5)
    except (ConfigError, InstagramError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        send_mail("[릴스 자동 게시 실패]", str(e))
        return 1
    finally:
        store.close()

    print(f"완료: 새로 게시 {posted}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
