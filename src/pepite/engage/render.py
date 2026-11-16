"""Personalisation variables, the unsubscribe footer and the plain-text to HTML body."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

VARIABLES = ("first_name", "last_name", "full_name", "company", "title", "sender_name")
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*(?:\|\s*([^}]*?))?\s*\}\}")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def split_name(full_name: str | None) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def company_label(domain: str | None) -> str:
    """Readable name from a domain when nothing better is known: acme-tools.io -> Acme Tools."""
    if not domain:
        return ""
    stem = domain.lower().split(".")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", stem) if w)


def person_vars(person: Any, company_name: str | None = None) -> dict[str, str]:
    if person is None:
        return {}
    first, last = split_name(getattr(person, "full_name", ""))
    return {
        "first_name": first,
        "last_name": last,
        "full_name": (getattr(person, "full_name", "") or "").strip(),
        "company": company_name or company_label(getattr(person, "company_domain", None)),
        "title": getattr(person, "title", None) or "",
    }


def render(text: str, values: dict[str, Any]) -> str:
    """Replace {{first_name}} and {{first_name|fallback}}. Unknown or empty -> fallback or ""."""

    def sub(m: re.Match[str]) -> str:
        value = str(values.get(m.group(1).lower()) or "").strip()
        return value or (m.group(2) or "").strip()

    return _VAR_RE.sub(sub, text or "")


def missing_vars(text: str, values: dict[str, Any]) -> list[str]:
    """Variables used without a fallback that have no value for this contact."""
    out: list[str] = []
    for m in _VAR_RE.finditer(text or ""):
        name = m.group(1).lower()
        if not str(values.get(name) or "").strip() and not (m.group(2) or "").strip():
            if name not in out:
                out.append(name)
    return out


def unknown_vars(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _VAR_RE.finditer(text or "")} - set(VARIABLES))


def footer_text(cfg: dict[str, Any], unsubscribe_url: str) -> str:
    lines = [
        str(cfg.get("footer_text") or "").strip(),
        str(cfg.get("postal_address") or "").strip(),
    ]
    lines = [ln for ln in lines if ln]
    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def compose_text(body: str, signature: str, footer: str) -> str:
    parts = [body.rstrip()]
    if signature.strip():
        parts.append("-- \n" + signature.strip())
    parts.append(footer)
    return "\n\n".join(parts) + "\n"


def _linkify(escaped: str) -> str:
    return _URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)


def text_to_html(body: str, signature: str, footer: str) -> str:
    def paras(text: str) -> str:
        blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
        return "".join(
            "<p>" + _linkify(html.escape(b)).replace("\n", "<br>") + "</p>" for b in blocks
        )

    out = [paras(body)]
    if signature.strip():
        out.append(f'<div style="color:#555">{paras(signature)}</div>')
    foot = _linkify(html.escape(footer)).replace("\n", "<br>")
    out.append(f'<p style="color:#888;font-size:12px;margin-top:24px">{foot}</p>')
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:1.5;color:#222">' + "".join(out) + "</body></html>"
    )


def mailto_unsubscribe(address: str) -> str:
    return f"mailto:{address}?subject={quote('unsubscribe')}"
