"""Show what is still in English on the pages of another language.

    PEPITE_HOME=/tmp/x python tools/ux_audit/seed.py      # optional: demo people and a list
    PEPITE_HOME=/tmp/x python tools/i18n_leftovers.py /companies /people "/settings/account"
    PEPITE_HOME=/tmp/x python tools/i18n_leftovers.py --crawl          # every page it can reach

Each page is rendered in English and in the language (default fr) by the real app, in process.
A visible text or a placeholder/title/aria-label/data-tip that is the same in both is listed:
either it is not marked for translation (a label built in Python, a message, JavaScript text),
or it has no catalog entry, or it is data (a company name) and is fine as it is.
"""

from __future__ import annotations

import argparse
import re
from html import unescape

from fastapi.testclient import TestClient

from pepite.web import i18n
from pepite.web.app import create_app

_SKIP = re.compile(r"<(script|style|code|pre|textarea)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TEXT = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r'\b(placeholder|title|aria-label|data-tip|alt)="([^"]+)"')
_LINK = re.compile(r'href="(/[^"#?]*)')


def strings(html: str) -> list[str]:
    html = _SKIP.sub("", html)
    found = [m.group(1) for m in _TEXT.finditer(html)] + [m.group(2) for m in _ATTR.finditer(html)]
    return [" ".join(unescape(s).split()) for s in found if i18n.worth(s)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--lang", default="fr")
    parser.add_argument("--crawl", action="store_true", help="follow the app's own links")
    args = parser.parse_args()
    client = TestClient(create_app())
    todo, seen, total = list(args.urls or ["/"]), set(), 0
    while todo:
        url = todo.pop(0)
        pattern = re.sub(r"[0-9a-f]{12,}|(?:pe|co)_[0-9a-f]+", "*", url)  # one page per kind
        if pattern in seen or url.startswith(("/static", "/api/", "/logout")) or ".csv" in url:
            continue
        seen.add(pattern)
        english = client.get(url)
        if english.status_code != 200 or "text/html" not in english.headers.get("content-type", ""):
            continue
        other = client.get(url, headers={"accept-language": args.lang})
        same = sorted(set(strings(english.text)) & set(strings(other.text)))
        total += len(same)
        print(f"\n## {url}: {len(same)} the same in en and {args.lang}")
        for text in same:
            print("  ", text[:140])
        if args.crawl:
            todo += [u for u in _LINK.findall(english.text) if u not in todo]
    print(f"\n{len(seen)} pages, {total} strings the same in both languages")


if __name__ == "__main__":
    main()
