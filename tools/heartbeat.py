"""생존 신고(하트비트) — "파이가 꺼졌을 때"를 알아채기 위한 장치.

실패 알림 메일은 프로그램이 **살아서** 보내는 것이라, 정전·인터넷 끊김·SD카드 고장으로 파이 자체가 멈추면
메일도 오지 않습니다. 그래서 반대로 "정해진 시간마다 신호를 보내고, 신호가 끊기면 알려 주는" 외부 서비스를 씁니다.

    python -m tools.heartbeat

.env 의 HEALTHCHECK_URL 에 서비스(예: healthchecks.io)가 알려 준 "핑 주소"를 넣으세요. 비워 두면 아무것도 하지 않습니다.
cron 예)  0 */6 * * *   ./cron/run.sh tools.heartbeat
"""
from __future__ import annotations

import sys

import requests

from common.config import env


def main() -> int:
    url = env("HEALTHCHECK_URL")
    if not url or "example" in url:
        print("HEALTHCHECK_URL 이 설정되지 않아 건너뜁니다.")
        return 0
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[오류] 생존 신고 실패: {e}", file=sys.stderr)
        return 1
    print("생존 신고 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
