"""현황 한눈에 보기 — 채널별 게시 수, 문제 항목, 디스크 여유.

    python -m tools.status            # 화면에 출력
    python -m tools.status --mail     # 같은 내용을 알림 메일로 발송 (cron 으로 하루 한 번 받아 보기 좋음)

"문제 있음" 표시가 붙은 항목(pending / 실패)은 3.7.2절의 방법으로 확인하세요.
"""
from __future__ import annotations

import argparse
import shutil
import sys

from common.config import ROOT, data_dir
from common.notify import send_mail
from common.store import PostStore

CHANNELS = ("threads", "threads_reply", "reels", "naver", "telegram")


def build_report() -> tuple[str, bool]:
    """(보고서 텍스트, 문제가 있는지)"""
    lines: list[str] = []
    has_problem = False
    for ch in CHANNELS:
        store = PostStore(ch)
        st = store.stats()
        store.close()
        c = st["counts"]
        if not (c["published"] or c["pending"] or c["failed"]):
            continue  # 한 번도 쓰지 않은 채널은 생략
        lines.append(f"[{ch}] 게시 {c['published']}건 (최근 24시간 {st['published_24h']}건) / "
                     f"pending {c['pending']} / 실패 {c['failed']}")
        lines.append(f"    마지막 게시: {st['last_published'] or '없음'}")
        for p in st["problems"]:
            has_problem = True
            err = f" — {p['error']}" if p["error"] else ""
            lines.append(f"    ⚠ 문제 있음: {p['item_id']} ({p['status']}, 실패 {p['fail_count']}회){err}")

    usage = shutil.disk_usage(data_dir())
    free_gb = usage.free / 1024 ** 3
    lines.append(f"[디스크] 여유 {free_gb:.1f} GB / 전체 {usage.total / 1024 ** 3:.1f} GB")
    if free_gb < 2:
        has_problem = True
        lines.append("    ⚠ 디스크 여유가 2GB 미만입니다. 오래된 로그·영상 파일을 정리하세요.")

    logs = ROOT / "logs"
    if logs.exists():
        size = sum(f.stat().st_size for f in logs.glob("*.log"))
        lines.append(f"[로그] logs/ {size / 1024 ** 2:.1f} MB")
    if not lines[:-1] or all(not ln.startswith("[") or ln.startswith(("[디스크]", "[로그]")) for ln in lines):
        lines.insert(0, "(아직 게시 기록이 없습니다)")
    return "\n".join(lines), has_problem


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="자동 게시 현황")
    ap.add_argument("--mail", action="store_true", help="결과를 알림 메일로도 보냄")
    args = ap.parse_args(argv)
    report, problem = build_report()
    print(report)
    if args.mail:
        subject = "[autopost] 일일 현황" + (" — 확인 필요" if problem else "")
        if not send_mail(subject, report):
            print("(메일 발송 설정이 없거나 실패해 메일은 보내지 않았습니다.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
