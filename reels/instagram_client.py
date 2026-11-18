"""Instagram API(Instagram 로그인 방식) 클라이언트 — 릴스 게시.

흐름:  컨테이너 생성(media_type=REELS) → 영상 업로드 → 처리 완료(FINISHED) 대기 → media_publish
영상 전달 방식 두 가지
  * video_url(기본, 권장): 공개 접근 가능한 영상 주소를 알려 주면 Meta 가 가져감 (reels/hosting.py 가 올려 둠)
  * resumable(실험적)    : 컨테이너를 upload_type=resumable 로 만들고 응답의 주소로 파일을 직접 전송.
                           공식 문서는 이 방식이 Facebook 로그인 방식 앱 용이라고 안내해, Instagram 로그인에서는 동작이 불확실
※ Meta 가 엔드포인트/버전을 바꿀 수 있습니다. 동작하지 않으면 공식 문서(Instagram Platform → Content Publishing)를 확인하세요.
"""
from __future__ import annotations

import os
import random
import time

import requests

BASE = "https://graph.instagram.com"
UPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"


class InstagramError(Exception):
    def __init__(self, message: str, *, code: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class InstagramClient:
    def __init__(self, user_id: str, access_token: str, *, version: str = "v25.0",
                 session: requests.Session | None = None, max_retries: int = 3, timeout: int = 60):
        self.user_id = user_id
        self.token = access_token
        self.version = version
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.timeout = timeout

    def _request(self, method: str, path: str, **params) -> dict:
        url = f"{BASE}/{self.version}/{path}"
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.token
        for attempt in range(self.max_retries + 1):
            try:
                resp = (self.session.get(url, params=params, timeout=self.timeout) if method == "GET"
                        else self.session.post(url, data=params, timeout=self.timeout))
            except requests.RequestException:
                if attempt >= self.max_retries or method != "GET":
                    raise
                time.sleep(2 ** attempt + random.random())
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {}
            err = body.get("error") if isinstance(body, dict) else None
            if resp.status_code < 400 and not err:
                return body
            code = (err or {}).get("code")
            retryable = resp.status_code == 429 or resp.status_code >= 500 or bool((err or {}).get("is_transient"))
            if code == 190:
                retryable = False
            if retryable and attempt < self.max_retries:
                time.sleep(2 ** attempt * 2 + random.random())
                continue
            raise InstagramError(
                f"Instagram API 오류 (code={code}, http={resp.status_code}): {(err or {}).get('message') or resp.text[:200]}",
                code=code, http_status=resp.status_code)
        raise InstagramError("재시도 횟수 초과")  # pragma: no cover

    # ── 릴스 게시 ───────────────────────────────────────────
    def create_reels_container(self, caption: str, *, video_url: str | None = None,
                               share_to_feed: bool = True) -> dict:
        """컨테이너 생성. video_url 이 없으면 resumable 업로드용 컨테이너(응답에 uri 포함)."""
        return self._request(
            "POST", f"{self.user_id}/media",
            media_type="REELS",
            caption=caption,
            video_url=video_url,
            upload_type=None if video_url else "resumable",
            share_to_feed="true" if share_to_feed else "false",
        )

    def upload_video(self, container: dict, video_path: str) -> None:
        container_id = container["id"]
        url = container.get("uri") or f"{UPLOAD_BASE}/{self.version}/{container_id}"
        size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            resp = self.session.post(
                url,
                headers={"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size)},
                data=f,
                timeout=600,
            )
        if resp.status_code >= 400:
            raise InstagramError(f"영상 업로드 실패 (http={resp.status_code}): {resp.text[:200]}",
                                 http_status=resp.status_code)

    def wait_until_ready(self, container_id: str, *, timeout: int = 600, interval: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self._request("GET", container_id, fields="status_code,status")
            status = info.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"영상 처리 실패: {status} {info.get('status', '')}")
            time.sleep(interval)
        raise InstagramError("영상 처리 대기 시간 초과")

    def publish(self, container_id: str) -> str:
        return self._request("POST", f"{self.user_id}/media_publish", creation_id=container_id)["id"]

    def publish_reel(self, caption: str, video_path: str | None = None, *, video_url: str | None = None) -> str:
        """업로드 → 대기 → 발행을 한 번에. (video_url 모드면 video_url 만 전달)"""
        container = self.create_reels_container(caption, video_url=video_url)
        if not video_url:
            self.upload_video(container, video_path)
        self.wait_until_ready(container["id"])
        return self.publish(container["id"])

    def permalink(self, media_id: str) -> str | None:
        return self._request("GET", media_id, fields="permalink").get("permalink")

    def me(self) -> dict:
        return self._request("GET", "me", fields="user_id,username")
