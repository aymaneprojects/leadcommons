"""List the interface strings to translate, and what a language still misses.

    python tools/i18n_extract.py                      # counts per source, for French
    python tools/i18n_extract.py --missing out.json   # strings without a translation
    python tools/i18n_extract.py --all out.json       # every string

Sources: the literal text of the templates (read by the same code that translates them, see
pepite/web/i18n.py), the messages and `L()`/`t()` labels of the Python web code, and the `T()`
strings of the JavaScript. Each entry: {"key", "where", "context"}; translations go to
src/pepite/web/locale/<lang>/*.json as {"key": "translation"}.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from pepite.web import i18n
from pepite.web.deps import STATIC_DIR, TEMPLATES_DIR, templates

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pepite"
PY_SOURCES = ["web", "engage", "workflows", "ai", "pipeline.py", "insights"]
MARKERS = {"L", "t", "tr"}
_JS_T = re.compile(r"""\bT\(\s*(["'])((?:\\.|(?!\1).)+)\1""")


class Recorder:
    """Stands in for a catalog: remembers what was looked up, translates nothing."""

    def __init__(self) -> None:
        self.where = ""
        self.found: dict[str, str] = {}

    def __call__(self, key: str) -> None:
        self.found.setdefault(key, self.where)


def template_strings() -> dict[str, dict[str, str]]:
    recorder = Recorder()
    env = templates.env.overlay(extensions=[i18n.Translate])
    env.pepite_recorder = recorder
    lines: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        lines[path.name] = source.splitlines()
        env.parse(source, path.name)
    out = {}
    for key, where in recorder.found.items():
        name, _, lineno = where.partition(":")
        line = lines[name][int(lineno) - 1].strip() if lineno.isdigit() else ""
        out[key] = {"where": where, "context": line[:220]}
    return out


def _fstring_key(node: ast.JoinedStr) -> str | None:
    parts, slot = [], 0
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if "{" in value.value or "}" in value.value:
                return None
            parts.append(value.value)
        else:
            parts.append(f"{{{slot}}}")
            slot += 1
    return "".join(parts)


def _sentence(text: str) -> bool:
    """Looks like a message for a person: starts with a capital, has several words."""
    text = text.strip()
    return (
        2 < len(text) <= 300
        and text[0].isupper()
        and " " in text
        and i18n.worth(text)
        and not re.search(r"<[a-z/]|://|\bSELECT\b|\bFROM\b|\\[dws]|^[A-Z_ ]+$|%\(", text)
    )


def python_strings() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    files: list[Path] = []
    for entry in PY_SOURCES:
        path = SRC / entry
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    files = [f for f in files if f.name != "techrules.py"]  # product names stay as they are
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()  # docstrings and what goes to the log, not to a person
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
                skip.add(id(body[0].value))
            if isinstance(node, ast.Call):
                fn = node.func
                owner = (
                    fn.value.id
                    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    else ""
                )
                if owner in {"log", "logger", "logging"} or (
                    isinstance(fn, ast.Name) and fn.id in {"ValueError", "RuntimeError", "print"}
                ):
                    skip.update(id(n) for a in node.args for n in ast.walk(a))
        rel = path.relative_to(ROOT / "src")
        for node in ast.walk(tree):
            marked = isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in MARKERS)
                or (isinstance(node.func, ast.Attribute) and node.func.attr in MARKERS)
            )
            if marked and node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and i18n.worth(value):
                    out.setdefault(
                        " ".join(value.split()),
                        {"where": f"{rel}:{node.lineno}", "context": "label"},
                    )
                continue
            if id(node) in skip:
                continue
            key = None
            if isinstance(node, ast.JoinedStr):
                key = _fstring_key(node)
                skip.update(id(n) for n in ast.walk(node))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                key = node.value
            if key and _sentence(key):
                out.setdefault(
                    " ".join(key.split()), {"where": f"{rel}:{node.lineno}", "context": "message"}
                )
    return out


def js_strings() -> dict[str, dict[str, str]]:
    out = {}
    for path in sorted((STATIC_DIR / "js").glob("*.js")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in _JS_T.finditer(line):
                key = m.group(2).replace("\\'", "'").replace('\\"', '"')
                out.setdefault(
                    " ".join(key.split()),
                    {"where": f"{path.name}:{lineno}", "context": "javascript"},
                )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", default="fr")
    parser.add_argument("--missing", metavar="FILE")
    parser.add_argument("--all", metavar="FILE")
    args = parser.parse_args()
    sources = {
        "templates": template_strings(),
        "python": python_strings(),
        "javascript": js_strings(),
    }
    done = i18n.catalog(args.lang)
    entries, missing = [], []
    for source, strings in sources.items():
        todo = [k for k in strings if k not in done]
        n = len(strings)
        print(f"{source}: {n} strings, {n - len(todo)} translated, {len(todo)} missing")
        for key, info in strings.items():
            entry = {"key": key, "source": source, **info}
            entries.append(entry)
            if key not in done:
                missing.append(entry)
    for path, data in ((args.all, entries), (args.missing, missing)):
        if path:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"wrote {len(data)} entries to {path}")


if __name__ == "__main__":
    main()
