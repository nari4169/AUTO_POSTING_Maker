#!/bin/bash
# cron 실행용 래퍼.  사용법:  ./cron/run.sh <모듈> [옵션...]
#   예)  ./cron/run.sh threads.post_threads --count 1
#        ./cron/run.sh naver.post_naver --count 1
#
# 하는 일
#   1) 프로젝트 폴더로 이동하고 가상환경(.venv)의 파이썬을 사용
#   2) flock 으로 "같은 작업이 겹쳐 실행"되는 것을 방지 (이전 실행이 길어져도 안전)
#   3) 네이버(브라우저) 작업은 xvfb-run 으로 가상 화면에서 실행 (모니터 없는 파이용)
#   4) logs/<모듈>_날짜.log 에 기록하고, 30일 지난 로그는 삭제
#   5) 종료 코드가 0 이 아니면 실패 알림 메일 발송(.env 의 SMTP 설정이 있을 때)

set -u
cd "$(dirname "$0")/.." || exit 1

MODULE="${1:?모듈 이름이 필요합니다. 예) threads.post_threads}"
shift

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

mkdir -p logs
LOG="logs/${MODULE//./_}_$(date +%Y%m%d).log"
LOCK="/tmp/autopost_${MODULE//./_}.lock"

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date '+%F %T') 이전 실행이 아직 진행 중이라 건너뜀" >> "$LOG"
    exit 0
fi

{
    echo "===== $(date '+%F %T') 시작: $MODULE $* ====="
    if [[ "$MODULE" == naver.* ]] && command -v xvfb-run >/dev/null 2>&1; then
        xvfb-run -a "$PY" -m "$MODULE" "$@"
    else
        "$PY" -m "$MODULE" "$@"
    fi
    CODE=$?    # ← 반드시 바로 저장! (아래 echo 안의 $(date) 가 $? 를 0 으로 덮어쓰기 때문)
    echo "===== $(date '+%F %T') 종료 (코드 $CODE) ====="
} >> "$LOG" 2>&1

find logs -name '*.log' -mtime +30 -delete 2>/dev/null

if [ "${CODE:-0}" != "0" ]; then
    "$PY" -m common.notify "[autopost] $MODULE 실패 (코드 $CODE)" "$(tail -n 30 "$LOG")" || true
fi
exit "${CODE:-0}"
