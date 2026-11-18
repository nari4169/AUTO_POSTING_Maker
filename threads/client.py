"""Threads API 클라이언트 — 2단계 발행(컨테이너 생성 → 발행).

흐름:  POST /{user}/threads (컨테이너)  →  [이미지/댓글이면 상태 FINISHED 대기]  →  POST /{user}/threads_publish
에러 정책(Meta 응답의 error.code 기준):
  * 190(토큰 무효/만료)            → 재시도 금지, 즉시 예외
  * 4, 17, 32, 613 / HTTP 429       → 백오프 후 재시도
  * is_transient=true / HTTP 5xx    → 백오프 후 재시도
  * 그 외 알 수 없는 코드           → 재시도하지 않고 예외(베타 API 라 함부로 재시도하면 중복 게시 위험)
"""
from __future__ import annotations

import random
import time

import requests

BASE = "https://graph.threads.com"
RATE_LIMIT_CODES = {4, 17, 32, 613}


class ThreadsError(Exception):
    """Threads API 가 에러를 응답한 경우(= 서버가 요청을 처리하지 않았다고 알려 준 경우)."""

    def __init__(self, message: str, *, code: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class ThreadsClient:
    def __init__(
        self,
        user_id: str,
        access_token: str,
        *,
        version: str = "v1.0",
        session: requests.Session | None = None,
        max_retries: int = 3,
        timeout: int = 30,
    ):
        self.user_id = user_id
        self.access_token = access_token
        self.version = version
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.timeout = timeout

    # ── 저수준 ──────────────────────────────────────────────
    def _request(self, method: str, path: str, **params) -> dict:
        url = f"{BASE}/{self.version}/{path}"
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.access_token

        for attempt in range(self.max_retries + 1):
            try:
                if method == "GET":
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                else:
                    resp = self.session.post(url, data=params, timeout=self.timeout)
            except requests.RequestException:
                # 네트워크 오류: 서버가 처리했는지 알 수 없음 → 호출한 쪽이 판단하도록 그대로 올림
                if attempt >= self.max_retries or method != "GET":
                    raise
                self._sleep(attempt)
                continue

            try:
                body = resp.json()
            except ValueError:
                body = {}

            err = body.get("error") if isinstance(body, dict) else None
            if resp.status_code < 400 and not err:
                return body

            code = (err or {}).get("code")
            message = (err or {}).get("message") or resp.text[:200]
            retryable = (
                code in RATE_LIMIT_CODES
                or resp.status_code == 429
                or resp.status_code >= 500
                or bool((err or {}).get("is_transient"))
            )
            if code == 190:  # OAuthException: 토큰 문제는 재시도해도 소용없음
                retryable = False
            if retryable and attempt < self.max_retries:
                self._sleep(attempt, resp.headers.get("Retry-After"))
                continue
            raise ThreadsError(f"Threads API 오류 (code={code}, http={resp.status_code}): {message}",
                               code=code, http_status=resp.status_code)
        raise ThreadsError("재시도 횟수 초과")  # pragma: no cover

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            time.sleep(min(int(retry_after), 60))
        else:
            time.sleep(min(2 ** attempt * 2 + random.random(), 60))

    # ── 컨테이너 / 발행 ─────────────────────────────────────
    def create_container(
        self,
        text: str,
        *,
        image_url: str | None = None,
        video_url: str | None = None,
        reply_to_id: str | None = None,
    ) -> str:
        if video_url:
            media_type = "VIDEO"
        elif image_url:
            media_type = "IMAGE"
        else:
            media_type = "TEXT"
        body = self._request(
            "POST",
            f"{self.user_id}/threads",
            media_type=media_type,
            text=text,
            image_url=image_url,
            video_url=video_url,
            reply_to_id=reply_to_id,
        )
        return body["id"]

    def container_status(self, container_id: str) -> dict:
        return self._request("GET", container_id, fields="status,error_message")

    def wait_until_ready(self, container_id: str, *, timeout: int = 300, interval: float = 3.0) -> None:
        """FINISHED 가 될 때까지 대기. 바로 발행하면 'code 24 / subcode 4279009' 오류가 날 수 있음."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.container_status(container_id)
            status = info.get("status")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsError(f"컨테이너 처리 실패: {status} {info.get('error_message', '')}")
            time.sleep(interval)
        raise ThreadsError("컨테이너 처리 대기 시간 초과")

    def publish(self, container_id: str) -> str:
        body = self._request("POST", f"{self.user_id}/threads_publish", creation_id=container_id)
        return body["id"]

    # ── 편의 메서드 ─────────────────────────────────────────
    def post(self, text: str, *, image_url: str | None = None, video_url: str | None = None) -> str:
        container = self.create_container(text, image_url=image_url, video_url=video_url)
        if image_url or video_url:
            self.wait_until_ready(container)
        return self.publish(container)

    def reply(self, text: str, reply_to_id: str) -> str:
        container = self.create_container(text, reply_to_id=reply_to_id)
        self.wait_until_ready(container)
        return self.publish(container)

    # ── 조회 ────────────────────────────────────────────────
    def me(self) -> dict:
        return self._request("GET", "me", fields="id,username")

    def my_threads(self, limit: int = 10) -> list[dict]:
        body = self._request("GET", f"{self.user_id}/threads", fields="id,text,timestamp,permalink", limit=limit)
        return body.get("data", [])

    def replies(self, media_id: str) -> list[dict]:
        body = self._request("GET", f"{media_id}/replies", fields="id,text,username,timestamp", reverse="true")
        return body.get("data", [])
