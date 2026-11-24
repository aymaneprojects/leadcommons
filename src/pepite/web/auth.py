"""Login for server mode. SHARED CONTRACT (signatures). Owner: app-core agent.

When settings.auth_required: every route except /login, /logout, /opt-out, /static/*,
/healthz needs a signed session cookie; HTML requests redirect to /login?next=...,
/api/* requests get 401 JSON. The login page renders template "login.html" with context
{"request", "error": str | None, "next": str} (template owned by the UI agent).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16

PUBLIC_PATHS = ("/login", "/logout", "/opt-out", "/healthz")
PUBLIC_PREFIXES = ("/static/", "/u/")

RATE_LIMIT_FAILURES = 5
RATE_LIMIT_WINDOW = 60.0


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int = 32) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=128 * n * r * 2 + 2**20,
    )


def hash_password(password: str) -> str:
    """scrypt hash, format 'scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>'."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = hashed.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        digest = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def safe_next(value: str | None) -> str:
    """Only relative same-site paths are allowed as redirect targets."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        if not any(ord(c) < 32 for c in value):
            return value
    return "/"


class _RateLimiter:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(key, []) if now - t < RATE_LIMIT_WINDOW]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= RATE_LIMIT_FAILURES

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(key, now)
            self._fails.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _token_path(path: str) -> bool:
    """Paths that API tokens (Authorization: Bearer) may open: the JSON API and /mcp."""
    return path == "/api" or path.startswith("/api/") or path == "/mcp" or path.startswith("/mcp/")


def bearer_token(request: Request) -> str | None:
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def token_valid(app: Any, token: str) -> bool:
    """True for a live API token created in Settings > API tokens (only hashes are stored)."""
    try:
        from pepite.store.tools import repo_for

        return repo_for(app.state.store).verify_token(token)
    except Exception:
        return False


def _render_login(
    request: Request, error: str | None, next_url: str, status: int = 200
) -> Response:
    try:
        from pepite.web.deps import templates

        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": error, "next": next_url},
            status_code=status,
        )
    except Exception:
        err = f'<p role="alert">{html.escape(error)}</p>' if error else ""
        body = (
            "<!doctype html><meta charset=utf-8><title>Sign in</title><h1>Sign in</h1>"
            f"{err}<form method=post action=/login>"
            f'<input type=hidden name=next value="{html.escape(next_url, quote=True)}">'
            "<input type=password name=password autofocus required>"
            "<button type=submit>Sign in</button></form>"
        )
        return HTMLResponse(body, status_code=status)


def install_auth(app: Any, settings: Any) -> None:
    """Add SessionMiddleware (settings.secret_key), the auth guard, and /login + /logout routes."""
    limiter = _RateLimiter()
    required = bool(settings.auth_required)

    # Workflows and the assistant reach the running app's store through this binding.
    from pepite import workflows
    from pepite.workflows import scheduler

    workflows.bind_app(app)
    scheduler.install(app)

    if required:

        class AuthGuard(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                path = request.url.path
                if _is_public(path) or request.session.get("auth") is True:
                    return await call_next(request)
                token = bearer_token(request) if _token_path(path) else None
                if token and await run_in_threadpool(token_valid, request.app, token):
                    return await call_next(request)
                if path.startswith("/api/") or path == "/api":
                    return JSONResponse({"detail": "Authentication required"}, status_code=401)
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

        app.add_middleware(AuthGuard)  # added first: SessionMiddleware must wrap it

    secure = os.environ.get("PEPITE_COOKIE_SECURE", "").lower() in {"1", "true"}
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=secure,
    )

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request, next: str = "/") -> Response:
        if not required:
            return RedirectResponse("/", status_code=303)
        return _render_login(request, None, safe_next(next))

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request) -> Response:
        if not required:
            return RedirectResponse("/", status_code=303)
        form = await request.form()
        password = str(form.get("password") or "")
        next_url = safe_next(str(form.get("next") or "/"))
        ip = request.client.host if request.client else "unknown"
        if limiter.blocked(ip):
            msg = "Too many attempts. Try again in a minute."
            return _render_login(request, msg, next_url, 429)
        ok = False
        if settings.password_hash:
            ok = verify_password(password, settings.password_hash)
        elif settings.password:
            ok = hmac.compare_digest(password.encode(), settings.password.encode())
        if not ok:
            limiter.fail(ip)
            return _render_login(request, "Incorrect password.", next_url, 401)
        limiter.reset(ip)
        request.session["auth"] = True
        return RedirectResponse(next_url, status_code=303)

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
