"""The web app in other languages (French first), without touching the English templates.

English is the source: templates and Python messages stay as they are, and the English pages
come out byte for byte as before. For another language:

- Templates are compiled by a second Jinja environment whose `Translate` extension rewrites the
  template SOURCE: literal text, a few attributes (placeholder, title, aria-label, data-tip, alt),
  `confirm('...')` texts and UI string literals. `{{ values }}` are never looked at, so company
  and people data is never translated.
- Labels that live in Python are marked `L("...")` (translated when a template prints them),
  translated with `t("Saved {name}.", name=x)`, or passed through the `|tr` filter.
- Notices (`?notice=`), toasts (HX-Trigger) and stored English messages (`|msg` filter) are
  translated as they leave, by matching the message against the text it was built from
  ("Saved {0}." -> "{0} est enregistrée.").

Catalogs: `locale/<lang>/*.json`, `{"English": "Français"}`, read in file-name order (a later
file wins, so corrections go in a higher-numbered file). A missing entry stays in English.
The language is the `lang` cookie (Settings > Account), else the browser's Accept-Language.
`tools/i18n_extract.py` lists the strings to translate and what is missing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Any

from jinja2.ext import Extension
from jinja2.lexer import Token, TokenStream
from starlette.requests import Request
from starlette.templating import Jinja2Templates

LOCALE_DIR = Path(__file__).parent / "locale"
LANGUAGES = {"en": "English", "fr": "Français"}
COOKIE = "lang"

_lang: ContextVar[str] = ContextVar("pepite_lang", default="en")
Lookup = Callable[[str], str | None]


def current() -> str:
    """Language of the request being answered ("en" outside a request)."""
    return _lang.get()


def pick(cookie: str | None, accept_language: str | None) -> str:
    """The user's explicit choice, else the first supported language the browser asks for."""
    if cookie in LANGUAGES:
        return cookie
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return "en"


# --------------------------------------------------------------------------- catalogs


def _key(text: str) -> str:
    return " ".join(text.split())


@cache
def catalog(lang: str) -> dict[str, str]:
    """English -> translation, from every JSON file of the language (empty for English)."""
    out: dict[str, str] = {}
    for path in sorted((LOCALE_DIR / lang).glob("*.json")):
        for en, tr_ in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(tr_, str) and tr_.strip():
                out[_key(en)] = tr_
    return out


_SLOT = re.compile(r"\{(\d+)\}")


_PLURAL = {"s": "s", "es": "s", "ies": "s", "": "", "y": ""}  # English ending -> French ending


@cache
def _patterns(lang: str) -> list[tuple[re.Pattern[str], str, frozenset[str]]]:
    """Catalog entries with slots ("Saved {0}.") as regexes, longest literal text first.

    A slot glued to a word ("contact{1}", "compan{1}") is an English plural ending: it only
    matches one, and the translation gets "s" or nothing in its place.
    """
    found = []
    for en, tr_ in catalog(lang).items():
        if not _SLOT.search(en):
            continue
        parts = _SLOT.split(en)  # literal, slot number, literal, ...
        seen: set[str] = set()
        endings: set[str] = set()
        regex = ""
        for i, part in enumerate(parts):
            if i % 2 == 0:
                regex += re.escape(part)
            elif part in seen:  # the same slot twice must hold the same value
                regex += f"(?P=s{part})"
            elif parts[i - 1][-1:].isalpha():
                seen.add(part)
                endings.add(part)
                regex += f"(?P<s{part}>ies|es|y|s|)"
            else:
                seen.add(part)
                regex += f"(?P<s{part}>.+?)"
        literal = sum(len(p) for p in parts[0::2])
        found.append((literal, re.compile(f"^{regex}$", re.DOTALL), tr_, frozenset(endings)))
    found.sort(key=lambda item: -item[0])
    return [(rx, tr_, endings) for _, rx, tr_, endings in found]


def tr(text: str, lang: str | None = None) -> str:
    """A label in the current language (the text itself when there is no translation)."""
    lang = lang or current()
    if lang == "en" or not text:
        return text
    return catalog(lang).get(_key(text), text)


def t(text: str, **values: Any) -> str:
    """`t("Saved {name}.", name=x)`: translate, then fill the named slots."""
    return tr(text).format(**values) if values else tr(text)


class L(str):
    """A UI label defined in Python: templates print it in the reader's language."""

    __slots__ = ()


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _pattern(text: str, lang: str) -> str | None:
    for rx, tr_, endings in _patterns(lang):
        m = rx.match(text)
        if m:

            def fill(
                slot: re.Match[str], m: re.Match[str] = m, endings: frozenset[str] = endings
            ) -> str:
                value = m.group(f"s{slot.group(1)}")
                return _PLURAL[value] if slot.group(1) in endings else value

            return _SLOT.sub(fill, tr_)
    return None


def message(text: Any, lang: str | None = None) -> Any:
    """A whole English message (notice, toast, stored status line) in the current language.

    The exact text first, then each sentence on its own (two messages are often joined), then
    the text against the slotted entries ("Saved {0}."); slot values are kept as they are.
    """
    lang = lang or current()
    if lang == "en" or not isinstance(text, str) or not text.strip():
        return text
    known = catalog(lang)
    whole = text.strip()
    if _key(whole) in known:
        return known[_key(whole)]
    parts = [known.get(_key(p)) or _pattern(p, lang) for p in _SENTENCE.split(whole)]
    if all(parts):
        return " ".join(parts)  # type: ignore[arg-type]
    return _pattern(whole, lang) or " ".join(
        tr_ or en for tr_, en in zip(parts, _SENTENCE.split(whole), strict=True)
    )


# --------------------------------------------------------------------------- template source

_ATTR = re.compile(r'(\b(?:placeholder|title|aria-label|data-tip|alt)=")([^"<>]*)(")')
_CONFIRM = re.compile(r"(confirm\(')((?:[^'\\]|\\.)*)('\))")
_TAG_NAME = re.compile(r"</?([a-zA-Z][\w-]*)")
_RAW = {"script", "style"}  # content is code: never touched
_KEEP = {"code", "pre", "kbd", "samp", "textarea"}  # content is literal: never translated
_WORD = re.compile(r"[^\W\d_]{2,}")
_CODE = re.compile(r"""[<>]|=["']""")  # markup or attributes kept in a string


def worth(text: str) -> bool:
    """Has a real word: not only numbers, punctuation, %-formats, or one css-like identifier."""
    if re.fullmatch(r"[\w-]*[-_][\w-]*", text.strip()):
        return False
    return bool(_WORD.search(re.sub(r"%[a-zA-Z]|&[a-z]+;|\{[^}]*\}", " ", text)))


class Scanner:
    """Walks the literal HTML of one template, token by token, and translates what is text.

    State (inside a tag, inside a quoted attribute, inside <script>) is kept between tokens,
    because a `{{ value }}` can sit in the middle of a tag.
    """

    def __init__(self, lookup: Lookup, lang: str = "") -> None:
        self.lookup, self.lang = lookup, lang
        self.in_tag = False
        self.quote = ""
        self.tag = ""  # name of the tag being read
        self.raw = ""  # "script", "style" or "comment" while inside one
        self.keep = ""  # "code", "pre"... while inside one

    def feed(self, value: str) -> str:
        out: list[str] = []
        i, n = 0, len(value)
        while i < n:
            if self.raw:
                end = value.find("-->", i) if self.raw == "comment" else self._raw_end(value, i)
                if end == -1:
                    out.append(value[i:])
                    break
                stop = end + 3 if self.raw == "comment" else end
                out.append(value[i:stop])
                i, self.raw = stop, ""
            elif self.in_tag:
                end = self._tag_end(value, i)
                chunk = value[i:] if end == -1 else value[i:end]
                out.append(self._attributes(chunk))
                if end == -1:
                    break
                i, self.in_tag = end, False
                self._closed_tag()
            else:
                start = self._tag_start(value, i)
                text = value[i:] if start == -1 else value[i:start]
                out.append(text if self.keep else self._text(text))
                if start == -1:
                    break
                i = start
                if value.startswith("<!--", i):
                    self.raw = "comment"
                    continue
                self.in_tag = True
                name = _TAG_NAME.match(value, i)
                slash = "/" if value.startswith("</", i) else ""
                self.tag = (slash + name.group(1)).lower() if name else ""
        return "".join(out)

    @staticmethod
    def _tag_start(value: str, i: int) -> int:
        """Index of the next `<` that opens a tag or a comment (-1: none). `a < b` is text."""
        while True:
            i = value.find("<", i)
            if i == -1 or _TAG_NAME.match(value, i) or value.startswith("<!", i):
                return i
            i += 1

    def _raw_end(self, value: str, i: int) -> int:
        return value.lower().find("</" + self.raw, i)

    def _tag_end(self, value: str, i: int) -> int:
        for k in range(i, len(value)):
            ch = value[k]
            if self.quote:
                if ch == self.quote:
                    self.quote = ""
            elif ch in "\"'":
                self.quote = ch
            elif ch == ">":
                return k + 1
        return -1

    def _closed_tag(self) -> None:
        name = self.tag
        if name in _RAW:
            self.raw = name
        elif name in _KEEP:
            self.keep = name
        elif name.startswith("/") and name[1:] == self.keep:
            self.keep = ""

    def _text(self, text: str) -> str:
        core = text.strip()
        if not core or not worth(core):
            return text
        found = self.lookup(_key(core))
        if found is None:
            return text
        lead = text[: len(text) - len(text.lstrip())]
        return lead + found.replace("<", "&lt;").replace(">", "&gt;") + text[len(text.rstrip()) :]

    def _attributes(self, chunk: str) -> str:
        if self.lang and chunk.startswith("<html"):
            chunk = chunk.replace('lang="en"', f'lang="{self.lang}"')

        def attr(m: re.Match[str]) -> str:
            found = self.lookup(_key(m.group(2))) if worth(m.group(2)) else None
            return m.group(0) if found is None else m.group(1) + found.replace('"', "&quot;") + '"'

        def confirm(m: re.Match[str]) -> str:
            found = self.lookup(_key(m.group(2)))
            # A typographic apostrophe cannot end the JavaScript string it sits in.
            safe = (found or "").replace("'", "’").replace('"', "&quot;")
            return m.group(0) if found is None else m.group(1) + safe + m.group(3)

        return _CONFIRM.sub(confirm, _ATTR.sub(attr, chunk))


_LOGIC_BEFORE = {"eq", "ne", "lt", "gt", "lteq", "gteq", "lbracket", "dot", "tilde"}
_LOGIC_AFTER = {"eq", "ne", "lt", "gt", "lteq", "gteq", "colon", "rbracket", "tilde"}
_LOGIC_NAMES = {"in", "is", "not", "include", "extends", "from", "import"}


def ui_string(tokens: list[Token], i: int) -> bool:
    """Is this string literal text for the reader (a macro argument, a `set`, a default)?

    Not when it is compared, used as a key, joined to something else or names a template:
    translating those would change what the template does.
    """
    value = tokens[i].value
    if not isinstance(value, str) or not (value[:1].isupper() or " " in value.strip()):
        return False
    if not worth(value) or "/" in value or value.endswith(".html") or _CODE.search(value):
        return False
    before, after = tokens[i - 1] if i else None, tokens[i + 1] if i + 1 < len(tokens) else None
    if before is not None and (
        before.type in _LOGIC_BEFORE or (before.type == "name" and before.value in _LOGIC_NAMES)
    ):
        return False
    return not (
        after is not None
        and (after.type in _LOGIC_AFTER or (after.type == "name" and after.value == "in"))
    )


class Translate(Extension):
    """Jinja extension of the non-English environments: translate the source as it is read."""

    def filter_stream(self, stream: TokenStream) -> Iterable[Token]:
        lang = getattr(self.environment, "pepite_lang", "en")
        recorder = getattr(self.environment, "pepite_recorder", None)
        lookup: Lookup = recorder or catalog(lang).get
        scanner = Scanner(lookup, "" if recorder else lang)
        tokens = list(stream)
        for i, tok in enumerate(tokens):
            if recorder is not None:
                recorder.where = f"{stream.name}:{tok.lineno}"
            if tok.type == "data":
                yield Token(tok.lineno, "data", scanner.feed(tok.value))
            elif tok.type == "string" and ui_string(tokens, i):
                found = lookup(_key(tok.value))
                yield tok if found is None else Token(tok.lineno, "string", found)
            else:
                yield tok


def _finalize(value: Any) -> Any:
    return tr(value) if type(value) is L else value


class LocaleTemplates(Jinja2Templates):
    """Jinja2Templates that renders with the environment of the request's language."""

    def environment(self, lang: str) -> Any:
        if lang == "en":
            return self.env
        envs: dict[str, Any] = self.__dict__.setdefault("_envs", {})
        if lang not in envs:
            env = self.env.overlay(extensions=[Translate], finalize=_finalize)
            env.pepite_lang = lang  # type: ignore[attr-defined]
            envs[lang] = env
        return envs[lang]

    def get_template(self, name: str) -> Any:
        return self.environment(current()).get_template(name)


def install_filters(env: Any) -> None:
    """`|tr` for a label and `|msg` for a stored English message; `ui_lang()` and `languages`."""
    env.filters["tr"] = lambda value: tr(str(value)) if value else value
    env.filters["msg"] = message
    env.globals["ui_lang"] = current
    env.globals["languages"] = LANGUAGES


# --------------------------------------------------------------------------- requests


class LocaleMiddleware:
    """Sets the request's language and translates the toast an HTMX answer carries."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        lang = pick(request.cookies.get(COOKIE), request.headers.get("accept-language"))
        token = _lang.set(lang)

        async def send_translated(event: Any) -> None:
            if lang != "en" and event["type"] == "http.response.start":
                event = {**event, "headers": list(_toast_headers(event.get("headers", []), lang))}
            await send(event)

        try:
            await self.app(scope, receive, send_translated)
        finally:
            _lang.reset(token)


def _toast_headers(headers: list[tuple[bytes, bytes]], lang: str) -> Iterator[tuple[bytes, bytes]]:
    for name, value in headers:
        if name.lower() == b"hx-trigger":
            try:
                data = json.loads(value)
                toast = data.get("toast") if isinstance(data, dict) else None
                if isinstance(toast, dict) and isinstance(toast.get("message"), str):
                    toast["message"] = message(toast["message"], lang)
                    value = json.dumps(data).encode("ascii")
            except ValueError:
                pass
        yield name, value
