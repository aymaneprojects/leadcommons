"""Reply and bounce detection over IMAP.

New messages in the watched folder are matched to emails LeadCommons sent through their
In-Reply-To / References headers (and, for delivery reports, the original Message-ID quoted
in the report). Messages are read with BODY.PEEK so nothing is marked as read.
"""

from __future__ import annotations

import email
import email.policy
import logging
import re
from datetime import datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

from leadcommons.engage import core, mailer
from leadcommons.models import utcnow
from leadcommons.store.engage import MailboxRow, repo_for

log = logging.getLogger("leadcommons.engage")

MAX_PER_POLL = 300
_MSGID_RE = re.compile(r"<[^<>\s]+@[^<>\s]+>")
_HEADERS = "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES FROM CONTENT-TYPE)])"


def _ids(value: str | None) -> list[str]:
    return _MSGID_RE.findall(value or "")


def _parse(raw: bytes) -> Message:
    return email.message_from_bytes(raw, policy=email.policy.default)


def _fetch_raw(conn: Any, uid: bytes, what: str) -> bytes | None:
    typ, data = conn.uid("FETCH", uid, what)
    if typ != "OK" or not data:
        return None
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
            return part[1]
    return None


def _uid_list(conn: Any, last_uid: int, since: datetime) -> list[bytes]:
    if last_uid > 0:
        typ, data = conn.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    else:
        typ, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    if typ != "OK" or not data:
        return []
    uids = [u for u in (data[0] or b"").split() if u.isdigit() and int(u) > last_uid]
    return sorted(uids, key=int)[:MAX_PER_POLL]


def _text_of(msg: Message) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        return str(text)
    except Exception:  # noqa: BLE001 - odd encodings: keep going without the body
        return ""


def _is_report(msg: Message) -> bool:
    ctype = (msg.get("Content-Type") or "").lower()
    sender = (msg.get("From") or "").lower()
    return "report-type=delivery-status" in ctype or any(
        k in sender for k in ("mailer-daemon", "postmaster")
    )


def _received_at(msg: Message) -> datetime | None:
    try:
        dt = parsedate_to_datetime(str(msg.get("Date") or ""))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else None


def poll_mailbox(
    store: Any, settings: Any, mailbox: MailboxRow, now: datetime | None = None
) -> dict[str, int]:
    """Check one mailbox for replies and bounces. Returns {"replies": n, "bounces": n}."""
    stats = {"replies": 0, "bounces": 0}
    if core.is_demo(settings) or not mailbox.has_imap:
        return stats
    repo = repo_for(store)
    cfg = repo.get_settings()
    now = now or utcnow()
    first = repo.first_sent_at(mailbox.id)
    if first is None:
        repo.update_mailbox(mailbox.id, imap_polled_at=now)
        return stats  # nothing sent yet: nothing can be a reply
    since = max(first, now - timedelta(days=30)) - timedelta(days=1)
    try:
        conn = mailer.imap_connect(settings, mailbox)
    except mailer.MailboxError as exc:
        repo.update_mailbox(
            mailbox.id, imap_status="error", imap_error=str(exc), imap_polled_at=now
        )
        return stats
    last_uid = int(mailbox.imap_last_uid or 0)
    try:
        typ, _ = conn.select(mailbox.imap_folder or "INBOX", readonly=True)
        if typ != "OK":
            raise mailer.MailboxError(f"The folder {mailbox.imap_folder or 'INBOX'} was not found.")
        for uid in _uid_list(conn, last_uid, since):
            last_uid = max(last_uid, int(uid))
            head = _fetch_raw(conn, uid, _HEADERS)
            if head is None:
                continue
            h = _parse(head)
            refs = _ids(str(h.get("In-Reply-To") or "")) + _ids(str(h.get("References") or ""))
            report = _is_report(h)
            if not refs and not report:
                continue
            originals = repo.outbound_by_message_ids(refs) if refs else []
            if not originals and not report:
                continue
            raw = _fetch_raw(conn, uid, "(BODY.PEEK[])")
            if raw is None:
                continue
            msg = _parse(raw)
            if report:
                quoted = repo.outbound_by_message_ids(_ids(raw.decode("utf-8", errors="replace")))
                for original in quoted:
                    if original.status == "bounced":
                        continue
                    repo.update_message(original.id, status="bounced", error="Delivery report")
                    core.handle_bounce(store, original.to_email, "delivery report", cfg)
                    stats["bounces"] += 1
                continue
            original = max(originals, key=lambda m: m.sent_at or m.created_at)
            if core.record_reply(
                store,
                original,
                message_id=str(msg.get("Message-ID") or "").strip(),
                from_email=email.utils.parseaddr(str(msg.get("From") or ""))[1],
                subject=str(msg.get("Subject") or ""),
                body=_text_of(msg),
                received_at=_received_at(msg),
                cfg=cfg,
            ):
                stats["replies"] += 1
        repo.update_mailbox(
            mailbox.id, imap_last_uid=last_uid, imap_polled_at=now, imap_status="ok", imap_error=""
        )
    except (mailer.MailboxError, OSError, Exception) as exc:  # noqa: BLE001 - report, never crash
        log.warning("IMAP poll failed for mailbox %s: %s", mailbox.id, exc.__class__.__name__)
        repo.update_mailbox(
            mailbox.id,
            imap_last_uid=last_uid,
            imap_status="error",
            imap_error=str(exc)[:300] or exc.__class__.__name__,
            imap_polled_at=now,
        )
    finally:
        mailer.imap_logout(conn)
    return stats
