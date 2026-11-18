"""환경 변수(.env) 로딩 도우미.

모든 설정은 .env 에서 읽습니다. 코드 안에 키/토큰을 직접 적지 마세요.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

# 셸에 이미 설정된 환경 변수가 있으면 그 값이 우선합니다(override=False).
load_dotenv(ENV_PATH)


class ConfigError(RuntimeError):
    """필수 설정이 비어 있을 때 발생."""


def env(name: str, default: str | None = None) -> str | None:
    """환경 변수를 읽는다. 비어 있거나 공백뿐이면 default."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    value = env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f".env 의 {name} 은 숫자여야 합니다: {value!r}")


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "y", "on")


def require(*names: str) -> dict[str, str]:
    """필수 환경 변수를 모아서 반환. 하나라도 비어 있으면 안내 메시지와 함께 예외."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = env(name)
        if value is None:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        hint = ""
        if not ENV_PATH.exists():
            hint = "\n  → .env 파일이 없습니다. `.env.example` 을 `.env` 로 복사한 뒤 값을 채우세요."
        raise ConfigError(f".env 에 다음 값을 채워 주세요: {', '.join(missing)}{hint}")
    return values


def resolve_path(value: str) -> Path:
    """상대 경로는 프로젝트 루트 기준으로 해석."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def data_dir() -> Path:
    path = resolve_path(env("DATA_DIR", "data") or "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def update_env_file(key: str, value: str, path: Path = ENV_PATH) -> None:
    """.env 의 KEY=... 줄을 갱신(없으면 추가). 토큰 발급/갱신 결과를 저장할 때 사용."""
    if not path.exists():
        raise ConfigError(".env 가 없습니다. `.env.example` 을 `.env` 로 복사하세요.")
    lines = path.read_text(encoding="utf-8").splitlines()
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
