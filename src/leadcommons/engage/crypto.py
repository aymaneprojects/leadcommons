"""Secrets for engage: mailbox passwords encrypted at rest and signed unsubscribe links.

One random key lives in ``settings.home/secret.key`` (created 0600 on first use, never logged).
Mailbox passwords are encrypted with Fernet under that key; unsubscribe tokens are signed with
a key derived from it, so links in emails keep working across restarts. The web app and the
worker must share the same LEADCOMMONS_HOME.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

KEY_FILE = "secret.key"
UNSUBSCRIBE_SALT = "leadcommons.unsubscribe.v1"
UNSUBSCRIBE_MAX_AGE = 3 * 365 * 24 * 3600  # links in old emails keep working for three years

_lock = threading.Lock()


class TokenError(ValueError):
    """A bad or expired unsubscribe token. `expired` tells which."""

    def __init__(self, message: str, *, expired: bool = False) -> None:
        super().__init__(message)
        self.expired = expired


def key_path(settings: Any) -> Path:
    return Path(settings.home) / KEY_FILE


def load_key(settings: Any) -> bytes:
    """The Fernet key, created with mode 0600 the first time it is needed."""
    path = key_path(settings)
    with _lock:
        try:
            raw = path.read_bytes().strip()
            if raw:
                return raw
        except FileNotFoundError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process won the race
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key


def encrypt(settings: Any, plain: str) -> str:
    if not plain:
        return ""
    return Fernet(load_key(settings)).encrypt(plain.encode()).decode()


def decrypt(settings: Any, token: str | None) -> str:
    """Plain text, or "" when nothing is stored or the key changed (the user re-enters it)."""
    if not token:
        return ""
    try:
        return Fernet(load_key(settings)).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def _signing_secret(settings: Any) -> str:
    digest = hashlib.sha256(b"leadcommons-unsubscribe|" + load_key(settings)).digest()
    return base64.urlsafe_b64encode(digest).decode()


def _serializer(settings: Any) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_signing_secret(settings), salt=UNSUBSCRIBE_SALT)


def make_unsubscribe_token(settings: Any, email: str, message_id: int | None = None) -> str:
    payload: dict[str, Any] = {"e": email.strip().lower()}
    if message_id is not None:
        payload["m"] = int(message_id)
    return _serializer(settings).dumps(payload)


def read_unsubscribe_token(
    settings: Any, token: str, max_age: int = UNSUBSCRIBE_MAX_AGE
) -> dict[str, Any]:
    """Return {"e": email, "m": message id or None}. Raises TokenError."""
    try:
        data = _serializer(settings).loads(token, max_age=max_age)
    except SignatureExpired:
        raise TokenError("This unsubscribe link has expired.", expired=True) from None
    except (BadData, ValueError, TypeError):
        raise TokenError("This unsubscribe link is not valid.") from None
    if not isinstance(data, dict) or "@" not in str(data.get("e") or ""):
        raise TokenError("This unsubscribe link is not valid.")
    return {"e": str(data["e"]), "m": data.get("m")}
