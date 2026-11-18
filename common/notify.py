"""실패 알림 메일(SMTP). 설정이 없으면 조용히 건너뜁니다.

CLI:  python -m common.notify "제목" "본문"
"""
from __future__ import annotations

import smtplib
import sys
from email.mime.text import MIMEText

from .config import env, env_int


def send_mail(subject: str, body: str) -> bool:
    host, user, password, to = env("SMTP_HOST"), env("SMTP_USER"), env("SMTP_PASSWORD"), env("MAIL_TO")
    if not (host and user and password and to) or password.startswith("your-"):
        return False
    port = env_int("SMTP_PORT", 465)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server:
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notify] 메일 발송 실패: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else "알림"
    body = sys.argv[2] if len(sys.argv) > 2 else ""
    sys.exit(0 if send_mail(subject, body) else 1)
