"""릴스 영상을 인스타그램이 가져갈 수 있도록 "공개 주소(URL)"로 올려 두는 부분.

Instagram API 의 릴스 게시는 기본적으로 `video_url`(인터넷에서 누구나 열 수 있는 영상 주소)을 요구합니다.
그래서 만든 영상을 잠깐 어딘가에 올려 두고, 게시가 끝나면 지웁니다. 방식은 .env 의 REELS_UPLOAD_MODE 로 고릅니다.

  firebase    Firebase Storage(구글 클라우드 스토리지)에 올림  ← 권장. 서비스 계정 JSON 키 1개 + 버킷 이름만 있으면 됨
  s3          S3 호환 저장소(Cloudflare R2, AWS S3, Backblaze B2 등)에 올림
  command     내가 정한 명령(rclone, scp 등)으로 올리고, 그 명령이 공개 URL 을 출력하게 함
  resumable   (실험적) 인스타그램에 파일을 직접 전송. 공식 문서상 Instagram 로그인 방식에서의 지원 여부가
              불확실하니 s3 가 안 될 때만 시도해 보세요. (이 모듈은 사용하지 않고 instagram_client 가 처리)
"""
from __future__ import annotations

import shlex
import subprocess
import uuid
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.config import ConfigError, env, require

MODES = ("firebase", "s3", "command", "resumable")


class HostingError(RuntimeError):
    pass


@dataclass
class Hosted:
    url: str
    cleanup: Callable[[], None]  # 게시가 끝난 뒤 호출해 올려 둔 파일을 지움


def upload_mode() -> str:
    mode = (env("REELS_UPLOAD_MODE", "firebase") or "firebase").lower()
    if mode not in MODES:
        raise ConfigError(f"REELS_UPLOAD_MODE 는 {', '.join(MODES)} 중 하나여야 합니다: {mode!r}")
    return mode


def host_video(path: Path, mode: str | None = None) -> Hosted:
    mode = mode or upload_mode()
    if mode == "firebase":
        return _host_firebase(path)
    if mode == "s3":
        return _host_s3(path)
    if mode == "command":
        return _host_command(path)
    raise HostingError(f"{mode} 방식은 공개 URL 업로드를 사용하지 않습니다.")


# ── Firebase Storage (구글 클라우드 스토리지) ────────────────────────────────
def _host_firebase(path: Path) -> Hosted:
    cfg = require("FIREBASE_STORAGE_BUCKET", "FIREBASE_CREDENTIALS")
    key_file = Path(cfg["FIREBASE_CREDENTIALS"]).expanduser()
    if not key_file.is_file():
        raise ConfigError(f"FIREBASE_CREDENTIALS 파일을 찾을 수 없습니다: {key_file}")
    try:
        from google.cloud import storage
    except ImportError as e:  # pragma: no cover
        raise HostingError("google-cloud-storage 가 설치돼 있지 않습니다: pip install google-cloud-storage") from e

    bucket_name = cfg["FIREBASE_STORAGE_BUCKET"].removeprefix("gs://").strip("/")
    if bucket_name.startswith("your-"):
        raise ConfigError(
            f"FIREBASE_STORAGE_BUCKET 이 예시 값({bucket_name})입니다. "
            "Firebase 콘솔 > Storage 의 버킷 이름(gs:// 뒤의 값)으로 바꿔 주세요."
        )
    client = storage.Client.from_service_account_json(str(key_file))
    bucket = client.bucket(bucket_name)
    name = f"{env('FIREBASE_STORAGE_PREFIX', 'reels/')}{uuid.uuid4().hex[:12]}-{path.name}"
    blob = bucket.blob(name)
    try:
        blob.upload_from_filename(str(path), content_type="video/mp4")
    except Exception as e:  # noqa: BLE001 - 흔한 원인(버킷 이름 오타)을 쉬운 말로 알려 준다
        if getattr(e, "code", None) == 404:
            raise HostingError(
                f"버킷 '{bucket_name}' 을(를) 찾을 수 없습니다. Firebase 콘솔 > Storage 에서 버킷 이름을 확인해 "
                "FIREBASE_STORAGE_BUCKET 에 넣었는지, 키 파일이 같은 프로젝트의 것인지 확인하세요."
            ) from e
        raise
    # 파일은 비공개로 두고, 1시간 동안만 열리는 서명된 주소를 만들어 인스타그램에 알려 준다.
    url = blob.generate_signed_url(version="v4", expiration=timedelta(hours=1), method="GET")

    def cleanup() -> None:
        try:
            blob.delete()
        except Exception as e:  # noqa: BLE001 - 정리 실패가 게시 결과를 바꾸면 안 됨
            print(f"[hosting] 임시 파일 삭제 실패(직접 지워 주세요): {name} ({e})")

    return Hosted(url=url, cleanup=cleanup)


# ── S3 호환 저장소 ────────────────────────────────────────────────────────────
def _host_s3(path: Path) -> Hosted:
    cfg = require("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:  # pragma: no cover
        raise HostingError("boto3 가 설치돼 있지 않습니다: pip install boto3") from e

    client = boto3.client(
        "s3",
        endpoint_url=env("S3_ENDPOINT_URL"),          # AWS S3 면 비워 둠, R2/B2 면 해당 주소
        region_name=env("S3_REGION", "auto"),
        aws_access_key_id=cfg["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["S3_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
    )
    bucket = cfg["S3_BUCKET"]
    key = f"{env('S3_PREFIX', 'reels/')}{uuid.uuid4().hex[:12]}-{path.name}"
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "video/mp4"})

    base = env("S3_PUBLIC_BASE_URL")
    if base:   # 버킷이 공개 설정이면 고정 주소 사용
        url = f"{base.rstrip('/')}/{key}"
    else:      # 비공개 버킷이면 1시간짜리 임시 주소(presigned URL) 사용
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600)

    def cleanup() -> None:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as e:  # noqa: BLE001 - 정리 실패가 게시 결과를 바꾸면 안 됨
            print(f"[hosting] 임시 파일 삭제 실패(직접 지워 주세요): {key} ({e})")

    return Hosted(url=url, cleanup=cleanup)


# ── 사용자 지정 명령 ──────────────────────────────────────────────────────────
def _host_command(path: Path) -> Hosted:
    template = require("REELS_UPLOAD_COMMAND")["REELS_UPLOAD_COMMAND"]
    args = [part.replace("{file}", str(path)) for part in shlex.split(template)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise HostingError(f"업로드 명령 실패:\n{proc.stderr[-500:]}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    url = lines[-1] if lines else ""
    if not url.startswith(("http://", "https://")):
        raise HostingError(f"업로드 명령의 마지막 출력 줄이 URL 이어야 합니다: {url!r}")

    delete_template = env("REELS_DELETE_COMMAND")

    def cleanup() -> None:
        if not delete_template:
            return
        try:
            cmd = [part.replace("{url}", url) for part in shlex.split(delete_template)]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as e:  # noqa: BLE001
            print(f"[hosting] 삭제 명령 실패: {e}")

    return Hosted(url=url, cleanup=cleanup)
