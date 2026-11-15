"""Talking to the user's own mailbox over SMTP (sending) and IMAP (connection test).

Nothing here decides *whether* to send: callers check demo mode, suppression, limits and
windows first. Errors are mapped to three outcomes the scheduler understands:
- MailboxError: the mailbox itself is unusable right now (login, network); retry later.
- RecipientRefused: the server refused this address permanently (a bounce).
- SendError: anything else about this one message.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from leadcommons.engage.crypto import decrypt

TIMEOUT = 20.0
SECURITY = ("ssl", "starttls", "none")
IMAP_ERROR = imaplib.IMAP4.error


class MailboxError(Exception):
    """The mailbox cannot be used right now (login refused, host unreachable)."""


class RecipientRefused(Exception):
    """The server refused the recipient address: treat as a hard bounce."""


class SendError(Exception):
    """This message could not be sent for another reason."""


@dataclass
class Outgoing:
    to: str
    subject: str
    text: str
    html: str
    from_email: str
    from_name: str = ""
    in_reply_to: str | None = None
    references: str | None = None
    list_unsubscribe: str | None = None
    one_click: bool = False


def message_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower() or "localhost"


def build_message(out: Outgoing) -> tuple[EmailMessage, str]:
    msg = EmailMessage()
    message_id = make_msgid(domain=message_domain(out.from_email))
    msg["From"] = formataddr((out.from_name, out.from_email)) if out.from_name else out.from_email
    msg["To"] = out.to
    msg["Subject"] = out.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id
    if out.in_reply_to:
        msg["In-Reply-To"] = out.in_reply_to
        msg["References"] = out.references or out.in_reply_to
    if out.list_unsubscribe:
        msg["List-Unsubscribe"] = out.list_unsubscribe
        if out.one_click:
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(out.text)
    msg.add_alternative(out.html, subtype="html")
    return msg, message_id


def _context() -> ssl.SSLContext:
    return ssl.create_default_context()


def smtp_connect(settings: Any, mailbox: Any) -> smtplib.SMTP:
    """Open and log in. Raises MailboxError."""
    host, port = mailbox.smtp_host, int(mailbox.smtp_port or 0)
    if not host or not port:
        raise MailboxError("The SMTP server is not set.")
    try:
        if mailbox.smtp_security == "ssl":
            conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=_context())
        else:
            conn = smtplib.SMTP(host, port, timeout=TIMEOUT)
            if mailbox.smtp_security == "starttls":
                conn.ehlo()
                conn.starttls(context=_context())
        conn.ehlo()
        if mailbox.smtp_username:
            password = decrypt(settings, mailbox.smtp_password)
            if not password:
                raise MailboxError("No SMTP password is stored. Enter it again in Mailboxes.")
            conn.login(mailbox.smtp_username, password)
        return conn
    except MailboxError:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise MailboxError(f"Login refused by the server ({exc.smtp_code}).") from None
    except smtplib.SMTPNotSupportedError:
        raise MailboxError("The server does not support this security setting.") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise MailboxError(f"Could not reach {host}:{port} ({exc.__class__.__name__}).") from None


def _quit(conn: Any) -> None:
    try:
        conn.quit()
    except Exception:  # noqa: BLE001 - already done with it
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def test_smtp(settings: Any, mailbox: Any) -> str:
    """Log in and quit. Never sends. Returns a short success message; raises MailboxError."""
    conn = smtp_connect(settings, mailbox)
    try:
        conn.noop()
    finally:
        _quit(conn)
    return f"Signed in to {mailbox.smtp_host}."


def send(settings: Any, mailbox: Any, out: Outgoing, conn: Any = None) -> str:
    """Send one message, return its Message-ID. Pass `conn` to reuse an open connection."""
    msg, message_id = build_message(out)
    own = conn is None
    if own:
        conn = smtp_connect(settings, mailbox)
    try:
        refused = conn.send_message(msg, from_addr=out.from_email, to_addrs=[out.to])
        if refused:
            code, text = next(iter(refused.values()))
            raise RecipientRefused(f"{code} {_txt(text)}")
        return message_id
    except smtplib.SMTPRecipientsRefused as exc:
        code, text = next(iter(exc.recipients.values()), (550, b"refused"))
        if 400 <= int(code) < 500:
            raise SendError(f"Temporarily refused: {code} {_txt(text)}") from None
        raise RecipientRefused(f"{code} {_txt(text)}") from None
    except smtplib.SMTPSenderRefused as exc:
        raise MailboxError(f"The server refused the sender address ({exc.smtp_code}).") from None
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError) as exc:
        raise MailboxError(f"Connection lost ({exc.__class__.__name__}).") from None
    except smtplib.SMTPDataError as exc:
        if 500 <= exc.smtp_code < 600 and b"5.1." in (exc.smtp_error or b""):
            raise RecipientRefused(f"{exc.smtp_code} {_txt(exc.smtp_error)}") from None
        raise SendError(f"{exc.smtp_code} {_txt(exc.smtp_error)}") from None
    except smtplib.SMTPException as exc:
        raise SendError(str(exc) or exc.__class__.__name__) from None
    finally:
        if own:
            _quit(conn)


def _txt(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return str(value)[:300]


# ---------------------------------------------------------------- IMAP


def imap_connect(settings: Any, mailbox: Any) -> imaplib.IMAP4:
    host, port = mailbox.imap_host, int(mailbox.imap_port or 0)
    if not host or not port:
        raise MailboxError("The IMAP server is not set.")
    try:
        if mailbox.imap_security == "ssl":
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                host, port, ssl_context=_context(), timeout=TIMEOUT
            )
        else:
            conn = imaplib.IMAP4(host, port, timeout=TIMEOUT)
            if mailbox.imap_security == "starttls":
                conn.starttls(ssl_context=_context())
        username = mailbox.imap_username or mailbox.smtp_username
        password = decrypt(settings, mailbox.imap_password) or decrypt(
            settings, mailbox.smtp_password
        )
        if not password:
            raise MailboxError("No IMAP password is stored. Enter it again in Mailboxes.")
        conn.login(username, password)
        return conn
    except MailboxError:
        raise
    except IMAP_ERROR as exc:
        raise MailboxError(f"IMAP login refused ({_txt(exc)}).") from None
    except OSError as exc:
        raise MailboxError(f"Could not reach {host}:{port} ({exc.__class__.__name__}).") from None


def imap_logout(conn: Any) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


def test_imap(settings: Any, mailbox: Any) -> str:
    conn = imap_connect(settings, mailbox)
    try:
        typ, _ = conn.select(mailbox.imap_folder or "INBOX", readonly=True)
        if typ != "OK":
            raise MailboxError(f"The folder {mailbox.imap_folder or 'INBOX'} was not found.")
    finally:
        imap_logout(conn)
    return f"Signed in to {mailbox.imap_host}."
