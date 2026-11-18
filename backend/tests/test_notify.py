"""E-mails straight to a mail server. No test opens a real connection."""

import smtplib

import pytest

from robotweb.config import get_settings
from robotweb.notify import Notifier, NotifyError, Smtp, from_settings, smtp_from_settings


class FakeServer:
    """Stands in for smtplib.SMTP; records what was done with it."""

    log: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        FakeServer.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeServer.log.append(("quit",))

    def starttls(self, context=None):
        FakeServer.log.append(("starttls",))

    def login(self, user, password):
        if FakeServer.fail:
            raise FakeServer.fail
        FakeServer.log.append(("login", user, password))

    def send_message(self, message):
        FakeServer.log.append(("send", message["From"], message["To"], message["Subject"], message.get_content()))


@pytest.fixture
def server(monkeypatch):
    FakeServer.log = []
    FakeServer.fail = None
    monkeypatch.setattr(smtplib, "SMTP", FakeServer)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeServer)
    return FakeServer


SMTP = Smtp(host="mail.example.org", port=587, sender="bl22@example.org", user="robot", password="s3cret")


async def test_one_message_goes_to_all_addresses(server):
    notifier = Notifier(None, ["a@example.org", "b@example.org"], SMTP)
    await notifier.send("BL22HT_Run_resumed", "The beam is back.\n  The run goes on.")
    assert server.log == [
        ("connect", "mail.example.org", 587),
        ("starttls",),
        ("login", "robot", "s3cret"),
        ("send", "bl22@example.org", "a@example.org, b@example.org", "BL22HT Run resumed",
         "The beam is back. The run goes on.\n"),
        ("quit",),
    ]


async def test_a_refusal_is_reported_without_the_password(server):
    server.fail = smtplib.SMTPAuthenticationError(535, b"authentication failed")
    with pytest.raises(NotifyError) as caught:
        await Notifier(None, ["a@example.org"], SMTP).send("Subject", "text")
    assert "authentication failed" in str(caught.value) and "s3cret" not in str(caught.value)
    assert "s3cret" not in repr(SMTP)


def test_a_password_is_never_sent_unencrypted():
    with pytest.raises(NotifyError):
        Smtp(host="mail.example.org", port=25, sender="bl22@example.org", security="none", user="robot", password="x")
    with pytest.raises(NotifyError):
        Smtp(host="mail.example.org", port=587, sender="not an address")
    with pytest.raises(NotifyError):
        Smtp(host="mail.example.org", port=587, sender="bl22@example.org", security="tls1")


def test_settings(monkeypatch):
    assert smtp_from_settings(get_settings()) is None  # no host: the control server sends
    monkeypatch.setenv("ROBOTWEB_SMTP_HOST", "mail.example.org")
    monkeypatch.setenv("ROBOTWEB_SMTP_FROM", "bl22@example.org")
    monkeypatch.setenv("ROBOTWEB_SMTP_SECURITY", "SSL")
    monkeypatch.setenv("ROBOTWEB_SMTP_PORT", "465")
    smtp = smtp_from_settings(get_settings())
    assert (smtp.host, smtp.port, smtp.security, smtp.user) == ("mail.example.org", 465, "ssl", "")
    # the simulator never sends real e-mails
    monkeypatch.setenv("ROBOTWEB_SIMULATION", "1")
    assert smtp_from_settings(get_settings()) is None


async def test_wrong_settings_do_not_stop_the_backend(monkeypatch):
    monkeypatch.setenv("ROBOTWEB_OPERATOR_EMAILS", "a@example.org")
    monkeypatch.setenv("ROBOTWEB_SMTP_HOST", "mail.example.org")
    monkeypatch.setenv("ROBOTWEB_SMTP_FROM", "bl22@example.org")
    monkeypatch.setenv("ROBOTWEB_SMTP_SECURITY", "none")
    monkeypatch.setenv("ROBOTWEB_SMTP_USER", "robot")
    notifier = from_settings(None, get_settings())
    assert "ROBOTWEB_SMTP_USER" in notifier.problem
    with pytest.raises(NotifyError) as caught:
        await notifier.send("Subject", "text")
    assert "mail settings are wrong" in str(caught.value)
