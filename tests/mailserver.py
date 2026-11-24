"""Tiny SMTP and IMAP4 servers on 127.0.0.1 for the end-to-end mail tests.

They speak just the commands Pépite's client sends (smtplib / imaplib), over real sockets, in
plain, STARTTLS and implicit-TLS modes. Not for anything else: no queues, no real folders.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
import socketserver
import ssl
import threading
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

USER, PASSWORD = "me@sender.example", "app-password"


def make_cert(directory: Path) -> Path:
    """A self-signed certificate (key included) valid for 127.0.0.1 and localhost."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    path = directory / "cert.pem"
    path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return path


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler: Any, tls: ssl.SSLContext | None, mode: str) -> None:
        super().__init__(("127.0.0.1", 0), handler)
        self.tls, self.mode = tls, mode  # mode: "none" | "starttls" | "ssl"
        self.port = self.server_address[1]
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def setup(self) -> None:
        self.request.settimeout(1)
        super().setup()

    def send(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def upgrade(self) -> None:
        self.wfile.flush()
        self.request = self.server.tls.wrap_socket(self.request, server_side=True)
        self.rfile = self.request.makefile("rb")
        self.wfile = self.request.makefile("wb")

    def handle(self) -> None:
        try:
            if self.server.mode == "ssl" and self.server.tls:
                self.request = self.server.tls.wrap_socket(self.request, server_side=True)
                self.rfile = self.request.makefile("rb")
                self.wfile = self.request.makefile("wb")
            self.session()
        except (OSError, ssl.SSLError):
            pass  # the client hung up (or failed the TLS check): nothing to answer


# ---------------------------------------------------------------- SMTP


class SMTPServer(_Server):
    """Records accepted messages in `.messages` as (mail_from, [rcpt], raw bytes)."""

    def __init__(self, tls: ssl.SSLContext | None = None, mode: str = "none") -> None:
        self.messages: list[tuple[str, list[str], bytes]] = []
        self.refuse: set[str] = set()  # recipients answered with 550
        self.starttls_seen = 0
        super().__init__(SMTPHandler, tls, mode)


class SMTPHandler(_Handler):
    def session(self) -> None:
        srv: SMTPServer = self.server  # type: ignore[assignment]
        authed, sender, rcpts = False, "", []
        self.send("220 test ESMTP")
        while line := self.rfile.readline():
            cmd = line.decode("utf-8", "replace").strip()
            verb = cmd.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.send("250-test")
                if srv.mode == "starttls" and not isinstance(self.request, ssl.SSLSocket):
                    self.send("250-STARTTLS")
                self.send("250 AUTH PLAIN LOGIN")
            elif verb == "STARTTLS":
                self.send("220 go ahead")
                self.upgrade()
                srv.starttls_seen += 1
            elif verb == "AUTH":
                authed = self.auth(cmd)
                self.send("235 ok" if authed else "535 5.7.8 Authentication credentials invalid")
            elif verb == "MAIL":
                if srv.mode == "starttls" and not isinstance(self.request, ssl.SSLSocket):
                    self.send("530 Must issue a STARTTLS command first")
                    continue
                if not authed:
                    self.send("530 5.7.0 Authentication required")
                    continue
                sender, rcpts = re.search(r"<([^>]*)>", cmd).group(1), []  # type: ignore[union-attr]
                self.send("250 ok")
            elif verb == "RCPT":
                rcpt = re.search(r"<([^>]*)>", cmd).group(1)  # type: ignore[union-attr]
                if rcpt in srv.refuse:
                    self.send("550 5.1.1 No such user")
                else:
                    rcpts.append(rcpt)
                    self.send("250 ok")
            elif verb == "DATA":
                self.send("354 end with .")
                lines = []
                while (row := self.rfile.readline()) not in (b".\r\n", b""):
                    lines.append(row[1:] if row.startswith(b"..") else row)
                srv.messages.append((sender, rcpts, b"".join(lines)))
                self.send("250 queued")
            elif verb == "QUIT":
                self.send("221 bye")
                return
            else:  # NOOP, RSET
                self.send("250 ok")

    def auth(self, cmd: str) -> bool:
        import base64

        parts = cmd.split()
        if len(parts) >= 3 and parts[1].upper() == "PLAIN":
            raw = base64.b64decode(parts[2]).split(b"\0")
            return raw[-2:] == [USER.encode(), PASSWORD.encode()]
        if len(parts) >= 2 and parts[1].upper() == "LOGIN":
            self.send("334 VXNlcm5hbWU6")
            user = base64.b64decode(self.rfile.readline().strip())
            self.send("334 UGFzc3dvcmQ6")
            pw = base64.b64decode(self.rfile.readline().strip())
            return (user, pw) == (USER.encode(), PASSWORD.encode())
        return False


# ---------------------------------------------------------------- IMAP


class IMAPServer(_Server):
    """One INBOX; `.add(raw)` delivers a message and returns its UID."""

    def __init__(self, tls: ssl.SSLContext | None = None, mode: str = "none") -> None:
        self.inbox: dict[int, bytes] = {}
        self.selected: list[str] = []
        super().__init__(IMAPHandler, tls, mode)

    def add(self, raw: bytes) -> int:
        uid = max(self.inbox, default=0) + 1
        self.inbox[uid] = raw
        return uid


class IMAPHandler(_Handler):
    def session(self) -> None:
        srv: IMAPServer = self.server  # type: ignore[assignment]
        self.send("* OK ready")
        while line := self.rfile.readline():
            tag, _, rest = line.decode("utf-8", "replace").strip().partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            if verb == "CAPABILITY":
                tls = srv.mode == "starttls" and not isinstance(self.request, ssl.SSLSocket)
                self.send("* CAPABILITY IMAP4rev1" + (" STARTTLS" if tls else ""))
                self.send(f"{tag} OK done")
            elif verb == "STARTTLS":
                self.send(f"{tag} OK go ahead")
                self.upgrade()
            elif verb == "LOGIN":
                user, pw = (a.strip('"') for a in args.split(" ", 1))
                ok = (user, pw) == (USER, PASSWORD)
                self.send(f"{tag} OK logged in" if ok else f"{tag} NO [AUTHENTICATIONFAILED] bad")
            elif verb == "SELECT" or verb == "EXAMINE":
                name = args.strip('"')
                srv.selected.append(name)
                if name != "INBOX":
                    self.send(f"{tag} NO no such folder")
                    continue
                self.send(f"* {len(srv.inbox)} EXISTS")
                self.send(f"{tag} OK [READ-ONLY] selected")
            elif verb == "UID":
                self.uid(tag, args, srv)
            elif verb == "LOGOUT":
                self.send("* BYE")
                self.send(f"{tag} OK bye")
                return
            else:
                self.send(f"{tag} OK done")

    def uid(self, tag: str, args: str, srv: IMAPServer) -> None:
        sub, _, rest = args.partition(" ")
        if sub.upper() == "SEARCH":
            m = re.match(r"UID (\d+):\*", rest)
            uids = sorted(srv.inbox)
            if m:  # like real servers: "N:*" always includes the last message
                uids = [u for u in uids if u >= int(m.group(1))] or uids[-1:]
            self.send("* SEARCH " + " ".join(map(str, uids)))
            self.send(f"{tag} OK done")
        elif sub.upper() == "FETCH":
            uid_s, _, what = rest.partition(" ")
            raw = srv.inbox.get(int(uid_s))
            if raw is None:
                self.send(f"{tag} OK done")
                return
            if "HEADER.FIELDS" in what:
                raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                section = (
                    "BODY[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES FROM CONTENT-TYPE)]"
                )
            else:
                section = "BODY[]"
            self.wfile.write(f"* 1 FETCH (UID {uid_s} {section} {{{len(raw)}}}\r\n".encode())
            self.wfile.write(raw + b")\r\n")
            self.wfile.flush()
            self.send(f"{tag} OK done")
        else:
            self.send(f"{tag} BAD unsupported")
