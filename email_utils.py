import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable, Union


def _parse_recipients(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    parts = [p.strip() for p in str(value).replace(";", ",").split(",")]
    return [p for p in parts if p]


def send_email(*, subject: str, body: str, to_addrs: Union[Iterable[str], str]) -> bool:
    host = os.environ.get("OMEGA_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("OMEGA_SMTP_PORT", "587"))
    user = os.environ.get("OMEGA_SMTP_USER", "").strip()
    password = os.environ.get("OMEGA_SMTP_PASS", "").strip()
    from_addr = os.environ.get("OMEGA_SMTP_FROM", user).strip()

    recipients = _parse_recipients(to_addrs)
    if not (host and port and user and password and recipients):
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body or "")

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)
    return True
