import asyncio
import os
import smtplib
from email.message import EmailMessage

from decorators import retry, traced
from logging_setup import log_event


def build_notice(*, username, user_uuid, event_id):
    subject = "Spammer detected"
    body = (
        "Spammer detected. Event was not processed.\n"
        f"User: {username or '-'}\n"
        f"UUID: {user_uuid}\n"
        f"Event: {event_id}\n"
    )
    return subject, body


class RecordingMailer:
    def __init__(self):
        self.sent = []

    async def send_spammer_notice(self, **kwargs):
        subject, body = build_notice(**kwargs)
        self.sent.append({"subject": subject, "body": body, **kwargs})


class SmtpMailer:
    def __init__(self, host, port, sender, recipient):
        self.host = host
        self.port = port
        self.sender = sender
        self.recipient = recipient

    async def send_spammer_notice(self, **kwargs):
        subject, body = build_notice(**kwargs)
        await _send_smtp(self.host, self.port, self.sender, self.recipient, subject, body)
        log_event(
            "mail_sent",
            message_id=kwargs.get("event_id"),
            user_uuid=kwargs.get("user_uuid"),
        )


@retry(times=3, delay=0.4, exceptions=(OSError, smtplib.SMTPException))
@traced
async def _send_smtp(host, port, sender, recipient, subject, body):
    await asyncio.to_thread(_deliver_smtp, host, port, sender, recipient, subject, body)


def _deliver_smtp(host, port, sender, recipient, subject, body):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP(host, int(port), timeout=10) as smtp:
        smtp.send_message(message)


def from_env():
    return SmtpMailer(
        host=os.environ.get("SMTP_HOST", "127.0.0.1"),
        port=int(os.environ.get("SMTP_PORT", "1025")),
        sender=os.environ.get("ALERT_FROM", "desk@localhost"),
        recipient=os.environ.get("ALERT_EMAIL", "ops@localhost"),
    )
