"""게시 기록(state.db) 백업.

    python -m tools.backup            # data/backups/state_날짜시각.db 로 복사, 최근 14개만 보관
    python -m tools.backup --keep 30

이 파일이 사라지면 "이미 올린 글" 기록이 없어져 모든 글을 다시 올릴 수 있습니다. 하루 한 번 백업하세요.
SQLite 의 백업 기능을 써서, 프로그램이 파일을 쓰는 중이어도 안전하게 복사됩니다.
(.env 와 naver_state.json 은 비밀 파일이라 자동 백업에 넣지 않았습니다. 직접 암호화해서 따로 보관하세요.)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from common.config import data_dir


def backup(keep: int = 14, db_path: Path | None = None, out_dir: Path | None = None) -> Path:
    src_path = db_path or data_dir() / "state.db"
    if not src_path.exists():
        raise FileNotFoundError(f"백업할 파일이 없습니다: {src_path}")
    out_dir = out_dir or data_dir() / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_path = out_dir / f"state_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    old = sorted(out_dir.glob("state_*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return dst_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="state.db 백업")
    ap.add_argument("--keep", type=int, default=14, help="보관할 백업 개수(기본 14)")
    args = ap.parse_args(argv)
    try:
        path = backup(args.keep)
    except FileNotFoundError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    print(f"백업 완료: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
