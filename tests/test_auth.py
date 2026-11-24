from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pepite.web import auth
from pepite.web.auth import hash_password, safe_next, verify_password
from tests.test_api import build_app, make_settings


def test_hash_format_and_verify() -> None:
    h = hash_password("s3cret")
    scheme, n, r, p, salt, digest = h.split("$")
    assert (scheme, n, r, p) == ("scrypt", str(2**14), "8", "1")
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32
    assert verify_password("s3cret", h)
    assert not verify_password("other", h)
    assert hash_password("s3cret") != h  # random salt
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "bcrypt$1$2$3$aa$bb")
    assert not verify_password("x", "scrypt$a$b$c$zz$yy")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/companies?q=1", "/companies?q=1"),
        ("//evil.com", "/"),
        ("https://evil.com", "/"),
        ("/\\evil.com", "/"),
        ("evil.com", "/"),
        ("", "/"),
        (None, "/"),
        ("/a\r\nb", "/"),
    ],
)
def test_safe_next(value, expected) -> None:
    assert safe_next(value) == expected


def client_for(tmp_path: Path, **kw) -> TestClient:
    kw.setdefault("password", "pw")
    app = build_app(make_settings(tmp_path, **kw))
    return TestClient(app, follow_redirects=False)


def test_guard_redirects_html_and_401_for_api(tmp_path: Path) -> None:
    c = client_for(tmp_path)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2F"
    r = c.get("/api/companies?q=a")
    assert r.status_code == 401 and r.json()["detail"]
    assert c.get("/healthz").status_code == 200
    assert c.get("/login").status_code == 200
    r = c.get("/login", params={"next": "/api/x"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_login_success_flow(tmp_path: Path) -> None:
    c = client_for(tmp_path)
    r = c.post("/login", data={"password": "pw", "next": "/companies?x=1"})
    assert r.status_code == 303 and r.headers["location"] == "/companies?x=1"
    assert c.get("/").status_code == 200
    assert c.get("/api/companies").status_code == 200
    r = c.post("/login", data={"password": "pw"})
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("bad", ["//evil.com", "https://evil.com", "/\\evil.com"])
def test_open_redirect_blocked(tmp_path: Path, bad: str) -> None:
    c = client_for(tmp_path)
    r = c.post("/login", data={"password": "pw", "next": bad})
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_login_failure(tmp_path: Path) -> None:
    c = client_for(tmp_path)
    r = c.post("/login", data={"password": "nope", "next": "/"})
    assert r.status_code == 401
    assert c.get("/").status_code == 303


def test_password_hash_preferred(tmp_path: Path) -> None:
    c = client_for(tmp_path, password=None, password_hash=hash_password("hashed"))
    assert c.post("/login", data={"password": "wrong"}).status_code == 401
    assert c.post("/login", data={"password": "hashed"}).status_code == 303
    assert c.get("/").status_code == 200


def test_rate_limit(tmp_path: Path) -> None:
    c = client_for(tmp_path)
    for _ in range(5):
        assert c.post("/login", data={"password": "bad"}).status_code == 401
    assert c.post("/login", data={"password": "bad"}).status_code == 429
    # even the right password is refused while limited
    assert c.post("/login", data={"password": "pw"}).status_code == 429


def test_rate_limit_window_expires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    c = client_for(tmp_path)
    for _ in range(5):
        c.post("/login", data={"password": "bad"})
    assert c.post("/login", data={"password": "pw"}).status_code == 429
    now[0] += 61
    assert c.post("/login", data={"password": "pw"}).status_code == 303


def test_logout(tmp_path: Path) -> None:
    c = client_for(tmp_path)
    c.post("/login", data={"password": "pw"})
    assert c.get("/").status_code == 200
    r = c.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/").status_code == 303


def test_server_mode_requires_auth(tmp_path: Path) -> None:
    c = client_for(tmp_path, mode="server", password=None)
    assert c.get("/").status_code == 303
    assert c.post("/login", data={"password": "anything"}).status_code == 401


def test_auth_disabled(tmp_path: Path) -> None:
    c = client_for(tmp_path, password=None)
    assert c.get("/").status_code == 200
    assert c.get("/api/companies").status_code == 200
    r = c.get("/login")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.post("/login", data={"password": "x"}).status_code == 303


def test_cookie_secure_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPITE_COOKIE_SECURE", "1")
    app = build_app(make_settings(tmp_path, password="pw"))
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    r = c.post("/login", data={"password": "pw"})
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "samesite=lax" in cookie
