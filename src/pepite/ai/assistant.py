"""The Assistant: understands a request, acts on the local app and proposes next steps.

Rules first (pepite.ai.rules, offline). When PEPITE_LLM_URL and
PEPITE_LLM_MODEL are set, the same optional LLM helps to read searches and to summarise a
website; its output is validated and never trusted to run actions. Nothing here calls an LLM or
crawls in demo mode. Owner: tools agent.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx

from pepite.ai.llm import _extract_json, llm_config, parse_query
from pepite.ai.params import to_url
from pepite.ai.rules import Kind, ParsedQuery, parse_rules
from pepite.store.tools import Message, ToolsRepo

log = logging.getLogger(__name__)

DEMO_CRAWL = "Reading a website is turned off in the public demo."
DEMO_PEOPLE = "Finding people is turned off in the public demo."
TOP_CARDS = 5
KEEP_IDS = 25
LLM_TIMEOUT = 25.0
MAX_TEXT = 2000
MAX_FILE = 6000

COMMANDS: list[tuple[str, str, str]] = [
    ("/find", "Find companies", "/find software companies in Lyon with 11-50 people"),
    ("/people", "Find people", "/people CEOs of fintech startups in Germany"),
    ("/list", "Save the last results as a list", "/list Lyon software"),
    ("/site", "Read a company website", "/site example.com"),
    ("/enrich", "Enrich a CSV file", "/enrich"),
    ("/help", "What I can do", "/help"),
]

STARTERS = [
    (
        "building-2",
        "Find companies to sell to",
        "Find software companies in France with 11-50 people",
    ),
    ("user-search", "Find people at a company", "Find CEOs of fintech startups in Germany"),
    ("globe", "Summarise my website", "/site"),
]

_STOP_KEYWORDS = {
    "see",
    "show",
    "find",
    "search",
    "get",
    "give",
    "market",
    "create",
    "make",
    "list",
    "leads",
    "lead",
    "hi",
    "hello",
    "hey",
    "please",
    "can",
    "sell",
    "want",
    "need",
    "looking",
    "look",
    "me",
    "my",
    "the",
    "all",
    "some",
    "any",
    "now",
    "just",
    "also",
    "thanks",
    "thank",
    "you",
    "these",
    "those",
    "them",
    "that",
    "this",
    "companies",
    "company",
    "people",
    "contacts",
    "prospects",
    "targets",
    "customers",
    "clients",
    "who",
    "what",
    "which",
}
_LEAD_VERBS = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|i want to\s+|i need to\s+|i would like to\s+)*"
    r"(?:find|show|see|search(?: for)?|get|give|look for|list|display)"
    r"(?:\s+me)?(?:\s+all)?(?:\s+the)?\s+",
    re.I,
)
_DOMAIN = re.compile(
    r"(?<![@\w.-])(?:https?://)?(?:www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})"
    r"(?![\w@-])(?:/[^\s]*)?",
    re.I,
)
_PEOPLE_WORDS = re.compile(
    r"\b(people|person|contacts?|leads?|decision[- ]?makers?|founders?|owners?|ceos?|ctos?|cfos?|"
    r"cmos?|coos?|heads? of|directors?|managers?|vps?|vice presidents?|prospects?)\b",
    re.I,
)
_SIZE_PEOPLE = re.compile(
    r"\d[\d,.\s]*(?:[-\u2013+]\s*\d*)?\s*(?:people|persons|employees|staff|ppl)\b", re.I
)
_MARKET = re.compile(
    r"\b(my market|my audience|i can sell|we can sell|who (?:should|can) i (?:sell|target)|"
    r"my (?:ideal )?customers?|my icp)\b",
    re.I,
)
_SITE_WORDS = re.compile(
    r"\b(my (?:company|website|site|business)|our (?:company|website|site)|we (?:sell|offer|do)|"
    r"summar[iy]s?e|what (?:does|do) .{0,40} (?:sell|do))\b",
    re.I,
)
_SETUP = re.compile(
    r"\b(?:set ?up|connect|link|authenticate|configure|verify)\b.{0,40}"
    r"\b(?:mailbox|mail box|smtp|sending domain|my domain|dkim|spf|dmarc|deliverability)\b",
    re.I,
)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hola|bonjour|salut|good (?:morning|afternoon|evening))\b[\s!.,]*$", re.I
)


@dataclass
class Intent:
    name: str  # help find list enrich site sequence workflow greeting market unknown
    text: str = ""  # request without the command word
    kind: Kind = "companies"
    domain: str | None = None
    list_name: str = ""
    command: str = ""


def extract_domain(text: str) -> str | None:
    for m in _DOMAIN.finditer(text):
        host = m.group(1).lower()
        if host.rsplit(".", 1)[-1] in {"png", "jpg", "pdf", "csv", "txt", "html", "py", "js", "md"}:
            continue
        return host
    return None


def detect_intent(raw: str) -> Intent:
    """Map a message to an intent. Pure and offline; the LLM is never needed here."""
    text = (raw or "").strip()
    if text.startswith("/"):
        word, _, rest = text.partition(" ")
        cmd, rest = word.lower(), rest.strip()
        if cmd in ("/find", "/companies"):
            return Intent("find", rest, "companies", command=cmd)
        if cmd == "/people":
            return Intent("find", rest, "people", command=cmd)
        if cmd == "/list":
            return Intent("list", list_name=rest[:120], command=cmd)
        if cmd == "/site":
            return Intent("site", rest, domain=extract_domain(rest), command=cmd)
        if cmd == "/enrich":
            return Intent("enrich", rest, command=cmd)
        if cmd in ("/help", "/?"):
            return Intent("help", command=cmd)
        return Intent("help", text, command=cmd)  # unknown command: show the list
    low = text.lower()
    if _GREETING.match(text):
        return Intent("greeting")
    if _SETUP.search(text):
        return Intent("setup", text)
    if re.search(r"\b(what can you do|help|how do (?:i|you)|commands?)\b", low) and len(low) < 60:
        return Intent("help")
    if re.search(r"\b(enrich|csv|spreadsheet|upload a file)\b", low):
        return Intent("enrich", text)
    if re.search(r"\b(sequence|cadence|outreach|email them|send (?:them )?an? email)\b", low):
        return Intent("sequence", text)
    if re.search(
        r"\b(workflow|automate|automation|every (?:day|week|morning)|each (?:day|week))\b", low
    ):
        return Intent("workflow", text)
    if re.search(r"\b(save|create|make|put|add)\b.{0,40}\b(list)\b", low) or re.match(
        r"^(?:save|add) (?:these|them|those|the results)", low
    ):
        m = re.search(r"(?:called|named|as)\s+[\"“']?([^\"”']{2,80})", text, re.I)
        return Intent("list", list_name=(m.group(1).strip() if m else ""))
    dom = extract_domain(text)
    if dom and (len(text.split()) <= 3 or _SITE_WORDS.search(text)):
        return Intent("site", text, domain=dom)
    if _SITE_WORDS.search(text):
        return Intent("site", text, domain=dom)
    if _MARKET.search(text):
        kind: Kind = "people" if re.search(r"\bleads?\b", low) else "companies"
        return Intent("market", text, kind)
    no_sizes = _SIZE_PEOPLE.sub(" ", text)  # "11-50 people" is a size, not a request for people
    kind = "people" if _PEOPLE_WORDS.search(no_sizes) else "companies"
    if len(low.split()) >= 2 or low:
        return Intent("find", text, kind)
    return Intent("unknown", text)


# ---------------------------------------------------------------------------- LLM helpers


def llm_complete(system: str, user: str, *, json_mode: bool = False) -> str | None:
    """One chat completion on the configured OpenAI-compatible endpoint; None on any problem."""
    cfg = llm_config()
    if cfg is None:
        return None
    base, model, key = cfg
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user[:6000]},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = httpx.post(
            f"{base}/chat/completions", json=body, headers=headers, timeout=LLM_TIMEOUT
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"]).strip()
    except Exception as exc:
        log.warning("assistant LLM call failed (%s)", type(exc).__name__)
        return None


SITE_SYSTEM = (
    "You read the public homepage text of a company. Reply with ONE JSON object: "
    '{"summary": "2-3 plain sentences on what the company sells and to whom, using only the text", '
    '"buyers": ["up to 3 short search phrases describing companies that could buy from it, '
    'e.g. \\"logistics companies in Germany with 51-200 employees\\""]}. '
    "Never invent facts that the text does not support."
)
CHAT_SYSTEM = (
    "You are the assistant of Pépite, a free local-first prospecting app (companies from "
    "open data, people and emails found on public company websites). Answer in at most four "
    "short sentences, in the user's language. You can only suggest: searching companies "
    "(/find ...), finding people (/people ...), saving a list (/list), reading a website "
    "(/site domain), enriching a CSV (/enrich). Never claim to have done something you did not."
)


def llm_site(text: str) -> tuple[str, list[str]] | None:
    raw = llm_complete(SITE_SYSTEM, text, json_mode=True)
    if not raw:
        return None
    try:
        data = _extract_json(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return None
    buyers = [
        b.strip()[:120]
        for b in (data.get("buyers") if isinstance(data.get("buyers"), list) else [])
        if isinstance(b, str) and b.strip()
    ][:3]
    return data["summary"].strip()[:700], buyers


# ---------------------------------------------------------------------------- homepage reading


@dataclass
class SiteInfo:
    domain: str
    name: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    excerpt: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.description or self.headings or self.excerpt)


def read_site(domain: str, *, fetch: Callable[..., Any] | None = None) -> SiteInfo:
    """Read the public homepage (and an about page) with the polite enrich crawler."""
    from pepite.enrich import crawl

    domain = crawl.normalise_domain(domain)
    site = (fetch or crawl.get_site)(domain, max_pages=3)
    info = SiteInfo(domain=domain)
    if not site.pages:
        return info
    home = site.pages[0]
    tree = home.tree()

    def meta(selector: str) -> str:
        node = tree.css_first(selector)
        return " ".join((node.attributes.get("content") or "").split()) if node else ""

    title_node = tree.css_first("title")
    title = " ".join((title_node.text() if title_node else "").split())
    info.name = meta('meta[property="og:site_name"]') or re.split(r"\s[|–—-]\s", title)[0]
    info.description = (
        meta('meta[name="description"]') or meta('meta[property="og:description"]')
    )[:500]
    for node in tree.css("h1, h2"):
        t = " ".join(node.text().split())
        if 3 <= len(t) <= 120 and t not in info.headings:
            info.headings.append(t)
        if len(info.headings) >= 8:
            break
    info.pages = [p.url for p in site.pages]
    lines = [ln for ln in home.text.splitlines() if len(ln) >= 60]
    info.excerpt = " ".join(lines)[:MAX_TEXT]
    if not info.description and lines:
        info.description = lines[0][:300]
    return info


# ---------------------------------------------------------------------------- replies


@dataclass
class Reply:
    text: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    more: dict[str, str] | None = None
    options: list[dict[str, Any]] | None = None
    options_prompt: str = "Pick a next step"
    chips: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    context: dict[str, Any] | None = None
    tone: str = ""  # "" | "warn"

    def data(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.cards:
            d["cards"] = self.cards
        if self.more:
            d["more"] = self.more
        if self.chips:
            d["chips"] = self.chips
        if self.links:
            d["links"] = self.links
        if self.context:
            d["context"] = self.context
        if self.tone:
            d["tone"] = self.tone
        if self.options:
            d["options"] = {
                "prompt": self.options_prompt,
                "choices": [{"key": f"c{i}", **o} for i, o in enumerate(self.options)],
                "chosen": None,
            }
        return d


def _opt(label: str, **action: Any) -> dict[str, Any]:
    return {"label": label, "action": action}


def company_card(c: Any) -> dict[str, Any]:
    place = ", ".join(x for x in (c.city, c.country) if x)
    sub = " · ".join(x for x in (c.domain, place) if x)
    meta = " · ".join(
        x for x in ((f"{c.size_range} employees" if c.size_range else ""), c.industry_label) if x
    )
    return {
        "kind": "company",
        "id": c.id,
        "title": c.name,
        "sub": sub,
        "meta": meta or "",
        "href": f"/companies/{c.id}",
    }


def person_card(p: Any, company: Any, email: Any) -> dict[str, Any]:
    where = company.name if company else (p.company_domain or "")
    sub = " at ".join(x for x in (p.title, where) if x)
    meta = ""
    if email is not None:
        status = getattr(email.status, "value", email.status)
        meta = f"{email.address} ({str(status).replace('_', ' ')})"
    return {
        "kind": "person",
        "id": p.id,
        "title": p.full_name,
        "sub": sub,
        "meta": meta,
        "href": f"/people/{p.id}",
    }


def _explain(parsed: ParsedQuery) -> list[str]:
    return [e.replace("(AI) ", "") for e in parsed.explanation][:8]


def _has_filters(parsed: ParsedQuery, kind: Kind) -> bool:
    c = parsed.company
    p = parsed.people
    base = bool(c.countries or c.industries or c.size_ranges or c.keywords)
    return base or bool(kind == "people" and (p.titles or p.seniorities))


def _drop_noise(parsed: ParsedQuery) -> ParsedQuery:
    keep = lambda ks: [k for k in ks if k.lower() not in _STOP_KEYWORDS]  # noqa: E731
    parsed.company.keywords = keep(parsed.company.keywords)
    parsed.people.company.keywords = keep(parsed.people.company.keywords)
    parsed.explanation = [
        e
        for e in parsed.explanation
        if not (e.startswith("Keyword: ") and e[9:].lower() in _STOP_KEYWORDS)
    ]
    return parsed


class Assistant:
    """Conversation engine. `state_get`/`state_set` keep the small company profile."""

    def __init__(
        self,
        store: Any,
        search: Any,
        jobs: Any,
        settings: Any,
        repo: ToolsRepo,
        *,
        state_get: Callable[[], dict[str, Any]] | None = None,
        state_set: Callable[..., None] | None = None,
        fetch_site: Callable[..., Any] | None = None,
    ) -> None:
        self.store, self.search, self.jobs, self.settings, self.repo = (
            store,
            search,
            jobs,
            settings,
            repo,
        )
        self._state_get = state_get or (lambda: {})
        self._state_set = state_set or (lambda **kw: None)
        self._fetch_site = fetch_site
        self._file_ctx = ""

    @property
    def demo(self) -> bool:
        return getattr(self.settings, "mode", "") == "demo"

    @property
    def llm_on(self) -> bool:
        return not self.demo and llm_config() is not None

    # ------------------------------------------------------------------ public API

    def converse(
        self, cid: str, text: str, attachment: dict[str, str] | None = None
    ) -> list[Message]:
        """Store the user's message, run the request, store and return the assistant reply.

        `attachment` is a text file the browser already read: {"name": ..., "text": ...}. It is
        kept with the message and used as context; a CSV never comes here (it goes to /enrich).
        """
        text = (text or "").strip()[:2000]
        att = None
        if attachment and str(attachment.get("text") or "").strip():
            body = str(attachment["text"])[:MAX_FILE]
            att = {"name": str(attachment.get("name") or "file.txt")[:120], "text": body}
        if not text and not att:
            return []
        history = self.repo.messages(cid)
        if not history:
            self.repo.rename_conversation(cid, _title_for(text or (att or {}).get("name", "")))
        data = {"attachment": att} if att else None
        self.repo.add_message(cid, "user", text, data)
        self._file_ctx = att["text"] if att else self._last_file(history)
        try:
            reply = (
                self.file_reply(att, text) if att and not text else self.reply(cid, text, history)
            )
        except Exception:
            log.exception("assistant failed on %r", text[:80])
            reply = Reply(
                "Something went wrong while I worked on that. Nothing was changed. "
                "Try again, or type /help to see what I can do.",
                tone="warn",
            )
        return [self.repo.add_message(cid, "assistant", reply.text, reply.data())]

    def choose(self, cid: str, message_id: int, key: str) -> tuple[list[Message], dict[str, Any]]:
        """The user picked an option card. Returns (new messages, the chosen action)."""
        msg = self.repo.get_message(message_id)
        if msg is None or msg.conversation_id != cid or msg.role != "assistant":
            return [], {}
        opts = msg.data.get("options") or {}
        if opts.get("chosen"):
            return [], {}
        choice = next((c for c in opts.get("choices", []) if c.get("key") == key), None)
        if choice is None:
            return [], {}
        opts["chosen"] = key
        self.repo.update_message_data(message_id, {**msg.data, "options": opts})
        action = dict(choice.get("action") or {})
        out = [self.repo.add_message(cid, "user", str(choice.get("label")), {"choice": key})]
        try:
            reply = self.act(cid, action, msg)
        except Exception:
            log.exception("assistant action %r failed", action.get("type"))
            reply = Reply("That did not work. Nothing was changed.", tone="warn")
        out.append(self.repo.add_message(cid, "assistant", reply.text, reply.data()))
        return out, action

    # ------------------------------------------------------------------ routing

    @staticmethod
    def _last_file(history: list[Message]) -> str:
        for m in reversed(history):
            att = (m.data or {}).get("attachment") if m.role == "user" else None
            if att:
                return str(att.get("text") or "")
        return ""

    def file_reply(self, att: dict[str, str], text: str) -> Reply:
        """A text file with no question: use the model if there is one, else offer its websites."""
        name, body = att["name"], att["text"]
        words = len(body.split())
        sites: list[str] = []
        for m in _DOMAIN.finditer(body):
            host = m.group(1).lower()
            if extract_domain(host) and host not in sites:
                sites.append(host)
            if len(sites) >= 3:
                break
        opts = [_opt(f"Read {d}", type="site", domain=d) for d in sites]
        if self.llm_on:
            out = llm_complete(
                CHAT_SYSTEM,
                f"The user attached a text file named {name}:\n{body[:4000]}\n\n"
                "Say in at most four short sentences what it tells you about who they should "
                "sell to.",
            )
            if out:
                return Reply(out, options=opts or None)
        lead = f"I added **{name}** ({words} words) as context for this chat. "
        if self.demo or not self.llm_on:
            lead += "I cannot read it for meaning without a language model. "
        if sites:
            return Reply(
                lead
                + "I found these websites in it. Pick one and I will read its public homepage.",
                options=opts,
            )
        return Reply(
            lead + "I found no website address in it. Tell me who you want to sell to in plain "
            "words, or attach a CSV to enrich."
        )

    def setup(self) -> Reply:
        demo = " Sending is turned off in the public demo." if self.demo else ""
        return Reply(
            "To send from Pépite you connect a mailbox you already own, then publish the DNS "
            "records that show the domain is yours.\n"
            "- Connect the mailbox: pick your provider and the server details are filled in.\n"
            "- Test it: Pépite signs in and quits. Nothing is sent.\n"
            "- Check the domain: MX, SPF, DKIM and DMARC are looked up for you.\n"
            "- Set your daily limits and the unsubscribe footer." + demo,
            links=[
                {"label": "Connect a mailbox", "href": "/settings/mailboxes/new"},
                {"label": "Check my domain", "href": "/settings/email-health?tab=domains"},
                {"label": "Sending rules", "href": "/settings/sending"},
            ],
        )

    def last_context(self, history: list[Message]) -> dict[str, Any]:
        for m in reversed(history):
            if m.role == "assistant" and m.data.get("context"):
                return dict(m.data["context"])
        return {}

    def reply(self, cid: str, text: str, history: list[Message]) -> Reply:
        intent = detect_intent(text)
        ctx = self.last_context(history)
        name = intent.name
        if name == "help":
            return self.help(intent)
        if name == "greeting":
            return self.greeting()
        if name == "enrich":
            return self.enrich()
        if name == "setup":
            return self.setup()
        if name == "site":
            return self.site(intent)
        if name == "list":
            return self.make_list(ctx, intent.list_name)
        if name == "sequence":
            return self.sequence(ctx)
        if name == "workflow":
            return self.workflow(ctx)
        if name == "market":
            return self.market(intent)
        return self.find(intent, ctx)

    def act(self, cid: str, action: dict[str, Any], source: Message) -> Reply:
        kind = action.get("type")
        if kind == "find":
            return self.find(
                Intent("find", str(action.get("text") or ""), action.get("kind") or "companies"),
                self.last_context(self.repo.messages(cid)),
            )
        if kind == "find_people":
            return self.find_people(list(action.get("company_ids") or []))
        if kind == "make_list":
            return self.make_list(dict(action.get("context") or {}), str(action.get("name") or ""))
        if kind == "profile":
            return self.save_profile(dict(action.get("profile") or {}))
        if kind == "site":
            dom = str(action.get("domain") or "")
            return self.site(Intent("site", dom, domain=extract_domain(dom)))
        if kind == "say":
            return Reply(str(action.get("text") or ""))
        if kind == "site_prompt":
            return Reply(
                "Paste the website address of the company you want me to read, "
                "for example `example.com`, or type `/site example.com`."
            )
        if kind in ("link", "dialog"):  # handled by the page; keep a quiet acknowledgement
            return Reply(str(action.get("text") or "Opening that for you."))
        return Reply("I did not understand that choice.")

    # ------------------------------------------------------------------ intents

    def help(self, intent: Intent) -> Reply:
        lead = ""
        if intent.command and intent.text and intent.command not in ("/help", "/?"):
            lead = f"I do not know the command `{intent.command}`. "
        lines = "\n".join(f"- `{c}`  {d}. Example: `{ex}`" for c, d, ex in COMMANDS)
        return Reply(
            f"{lead}Here is what I can do. Write in plain words, or use a command:\n{lines}\n\n"
            "I search the open company data, show you the top results and suggest the next step. "
            "Nothing is sent to anyone without you clicking a button.",
            options=[
                _opt(label, type="find", text=ex, kind="companies") for _, label, ex in STARTERS[:2]
            ],
        )

    def greeting(self) -> Reply:
        return Reply(
            "Hello. Tell me who you want to sell to, for example “software companies in Lyon "
            "with 11-50 people”, or give me your website and I will work out who to target.",
            options=[
                _opt(label, type="find", text=ex, kind="companies") for _, label, ex in STARTERS[:2]
            ]
            + [_opt("Read my website", type="site_prompt")],
        )

    def enrich(self) -> Reply:
        return Reply(
            "To turn a file of names and companies into verified emails, use CSV enrichment. "
            "You upload the file, Pépite looks up each company and checks each address "
            "without contacting anyone.",
            links=[{"label": "Open CSV enrichment", "href": "/enrich"}],
        )

    def parse(self, text: str, kind: Kind) -> ParsedQuery:
        cleaned = _LEAD_VERBS.sub("", text).strip() or text
        try:
            parsed = parse_rules(cleaned, kind) if not self.llm_on else parse_query(cleaned, kind)
        except Exception:
            parsed = parse_rules(cleaned, kind)
        return _drop_noise(parsed)

    def find(self, intent: Intent, ctx: dict[str, Any]) -> Reply:
        kind: Kind = intent.kind
        text = intent.text
        if not text.strip():
            return Reply(
                "What are you looking for? Describe the companies or people, for example "
                "“accounting firms in Belgium with 11-50 people”.",
                options=[
                    _opt(lab, type="find", text=ex, kind="companies") for _, lab, ex in STARTERS[:2]
                ],
            )
        parsed = self.parse(text, kind)
        if not _has_filters(parsed, kind):
            if self.llm_on:
                ctx_file = (
                    f"Context file the user attached:\n{self._file_ctx[:3000]}\n\nRequest: "
                    if self._file_ctx
                    else ""
                )
                free = llm_complete(CHAT_SYSTEM, ctx_file + text)
                if free:
                    return Reply(free)
            return Reply(
                "I could not turn that into a search. Try naming a place, an industry or a size, "
                "for example “marketing agencies in Spain with 11-50 people”.",
                options=[
                    _opt(lab, type="find", text=ex, kind="companies") for _, lab, ex in STARTERS[:2]
                ],
                tone="warn",
            )
        return self.people(parsed, text) if kind == "people" else self.companies(parsed, text)

    def companies(self, parsed: ParsedQuery, text: str = "") -> Reply:
        res = self.search.search_companies(parsed.company, page=1, per_page=KEEP_IDS)
        query, href = to_url(parsed, "companies")
        chips = _explain(parsed)
        if res.total == 0:
            return Reply(
                "I did not find any company for that search. Try fewer filters, a wider size "
                "range or another country.",
                chips=chips,
                links=[{"label": "Open the search page", "href": href}],
                tone="warn",
            )
        items = list(res.items)
        shown = items[:TOP_CARDS]
        ids = [c.id for c in items]
        ctx = {"kind": "companies", "query": query, "ids": ids, "total": res.total}
        opts = []
        with_domain = [c for c in shown if c.domain]
        if with_domain and not self.demo:
            opts.append(
                _opt(
                    f"Find people at these {len(with_domain)} companies",
                    type="find_people",
                    company_ids=[c.id for c in with_domain],
                )
            )
        opts.append(
            _opt(
                f"Save the top {len(ids)} in a list",
                type="make_list",
                context=ctx,
                name=_list_name(parsed, "companies"),
            )
        )
        opts.append(
            _opt(
                "Automate this search",
                type="link",
                href=f"/workflows/new?{urlencode({'source': 'companies', 'query': query})}",
            )
        )
        opts.append(
            _opt(
                "Show me people instead",
                type="find",
                text=text or _as_people(parsed),
                kind="people",
            )
        )
        n = res.total
        return Reply(
            f"I found **{n:,}** compan{'ies' if n != 1 else 'y'} that match. Here are the top "
            f"{len(shown)}.",
            cards=[company_card(c) for c in shown],
            more={"label": f"See all {n:,} on the Companies page", "href": href}
            if n > len(shown)
            else None,
            options=opts,
            chips=chips,
            context=ctx,
        )

    def people(self, parsed: ParsedQuery, text: str = "") -> Reply:
        res = self.store.search_people(parsed.people, page=1, per_page=KEEP_IDS)
        query, href = to_url(parsed, "people")
        chips = _explain(parsed)
        if res.total:
            people = list(res.items)
            shown = people[:TOP_CARDS]
            cards = [
                person_card(p, self._company(p.company_id, p.company_domain), self._email(p.id))
                for p in shown
            ]
            ids = [p.id for p in people]
            ctx = {"kind": "people", "query": query, "ids": ids, "total": res.total}
            opts = [
                _opt(
                    f"Save the top {len(ids)} in a list",
                    type="make_list",
                    context=ctx,
                    name=_list_name(parsed, "people"),
                ),
                _opt(
                    "Add them to a sequence",
                    type="dialog",
                    href=f"/partials/sequence-picker?ids={','.join(ids[:KEEP_IDS])}",
                    text="Choose a sequence in the window that just opened.",
                ),
                _opt(
                    "Automate this search",
                    type="link",
                    href=f"/workflows/new?{urlencode({'source': 'people', 'query': query})}",
                ),
            ]
            n = res.total
            return Reply(
                f"You already have **{n:,}** {'person' if n == 1 else 'people'} matching this. "
                f"Here are the top {len(shown)}.",
                cards=cards,
                more={"label": f"See all {n:,} on the People page", "href": href}
                if n > len(shown)
                else None,
                options=opts,
                chips=chips,
                context=ctx,
            )
        # nobody saved yet: offer to look at the matching companies' public pages
        co = self.search.search_companies(parsed.people.company, page=1, per_page=KEEP_IDS)
        if co.total == 0:
            return Reply(
                "I have no saved people for that and no company in the open data matches the "
                "company part of your request. Try other words.",
                chips=chips,
                tone="warn",
                links=[{"label": "Open the companies search", "href": "/companies"}],
            )
        items = list(co.items)
        shown = items[:TOP_CARDS]
        q2, _h2 = to_url(parsed, "companies")
        ctx = {"kind": "companies", "query": q2, "ids": [c.id for c in items], "total": co.total}
        text = (
            f"You have not found any person for this yet. **{co.total:,}** "
            f"compan{'ies' if co.total != 1 else 'y'} match, and people are found on a company's "
            "own public website. Here are the top companies."
        )
        opts: list[dict[str, Any]] = []
        with_domain = [c for c in shown if c.domain]
        if self.demo:
            text += " " + DEMO_PEOPLE
        elif with_domain:
            opts.append(
                _opt(
                    f"Find people at these {len(with_domain)} companies",
                    type="find_people",
                    company_ids=[c.id for c in with_domain],
                )
            )
        opts.append(
            _opt(
                "Save these companies in a list",
                type="make_list",
                context=ctx,
                name=_list_name(parsed, "companies"),
            )
        )
        return Reply(
            text,
            cards=[company_card(c) for c in shown],
            options=opts,
            chips=chips,
            context=ctx,
            tone="warn" if self.demo else "",
        )

    def find_people(self, company_ids: list[str]) -> Reply:
        if self.demo:
            return Reply(DEMO_PEOPLE, tone="warn")
        started, skipped = [], []
        for cid in company_ids[:10]:
            c = self._company(cid, None)
            if c is None or not c.domain:
                skipped.append(c.name if c else str(cid))
                continue
            self.jobs.submit(
                "find_people",
                {"domain": c.domain, "company_id": c.id},
                f"Find people at {c.domain}",
            )
            started.append(c.domain)
        if not started:
            return Reply(
                "None of these companies has a website address, so I cannot look for people.",
                tone="warn",
            )
        text = (
            f"Started looking for people on the public websites of **{len(started)}** "
            f"compan{'ies' if len(started) != 1 else 'y'} ({', '.join(started[:4])}"
            f"{'…' if len(started) > 4 else ''}). It takes a few minutes. Watch the jobs menu at "
            "the top, then open People."
        )
        if skipped:
            text += f" Skipped for lack of a website: {', '.join(skipped[:3])}."
        return Reply(
            text,
            links=[{"label": "Open People", "href": "/people"}],
            options=[
                _opt(
                    "Remind me what to do next",
                    type="say",
                    text="When the jobs finish, open People, select the ones you want and "
                    "choose Add to sequence, or ask me to save them in a list.",
                )
            ],
        )

    def make_list(self, ctx: dict[str, Any], name: str) -> Reply:
        ids = [str(i) for i in (ctx.get("ids") or [])]
        kind = ctx.get("kind")
        if not ids or kind not in ("people", "companies"):
            return Reply(
                "There is nothing to save yet. Ask me for a search first, then say "
                "“save these in a list”.",
                tone="warn",
            )
        name = (name or f"Assistant list {date.today().isoformat()}").strip()[:120]
        list_id = self.store.create_list(name, kind)
        if kind == "companies":
            for cid in ids:
                c = self._company(cid, None)
                if c is not None:
                    self.store.save_company(c)
        self.store.add_to_list(list_id, ids)
        what = "companies" if kind == "companies" else "people"
        return Reply(
            f"Done. I saved **{len(ids)}** {what} in a new list called “{name}”.",
            links=[{"label": f"Open the list “{name}”", "href": f"/lists/{list_id}"}],
            context=ctx,
        )

    def sequence(self, ctx: dict[str, Any]) -> Reply:
        ids = [str(i) for i in (ctx.get("ids") or [])]
        if ctx.get("kind") == "people" and ids:
            href = f"/partials/sequence-picker?ids={','.join(ids[:KEEP_IDS])}"
            return Reply(
                f"I can add these {len(ids)} people to a sequence. Pick one in the window "
                "that opens. Nobody is emailed until the sequence is running and a mailbox is "
                "connected.",
                options=[
                    _opt(
                        "Choose a sequence",
                        type="dialog",
                        href=href,
                        text="Choose a sequence in the window that just opened.",
                    )
                ],
                links=[{"label": "Open Sequences", "href": "/sequences"}],
                context=ctx,
            )
        if ctx.get("kind") == "companies" and ids:
            return Reply(
                "Sequences email people, not companies. First I need to find the people at "
                "these companies.",
                options=[
                    _opt(
                        "Find people at these companies",
                        type="find_people",
                        company_ids=ids[:TOP_CARDS],
                    )
                ]
                if not self.demo
                else None,
                links=[{"label": "Open Sequences", "href": "/sequences"}],
                context=ctx,
            )
        return Reply(
            "To email prospects, search for people first (for example “/people CEOs of "
            "accounting firms in Belgium”), then I can add them to a sequence.",
            links=[{"label": "Open Sequences", "href": "/sequences"}],
        )

    def workflow(self, ctx: dict[str, Any]) -> Reply:
        if ctx.get("query") is not None and ctx.get("kind") in ("people", "companies"):
            href = "/workflows/new?" + urlencode({"source": ctx["kind"], "query": ctx["query"]})
            return Reply(
                "I can turn the last search into a workflow that runs every day or week and "
                "acts on new matches, for example by adding them to a list or creating a task.",
                links=[{"label": "Create a workflow from this search", "href": href}],
            )
        return Reply(
            "Workflows run steps for you when something happens or on a schedule. "
            "Start from a blank one, or run a search with me first and I will pre-fill it.",
            links=[{"label": "Open Workflows", "href": "/workflows"}],
        )

    # ---- my company / my market

    def market(self, intent: Intent) -> Reply:
        profile = self._state_get().get("assistant_profile") or {}
        words = [k for k in profile.get("keywords", []) if k]
        if not words:
            return Reply(
                "I need to know what you sell before I can say who to target. Give me your "
                "website address and I will read the public homepage.",
                options=[_opt("Read my website", type="site_prompt")],
            )
        text = " ".join(words[:4])
        return self.find(Intent("find", text, intent.kind), {})

    def save_profile(self, profile: dict[str, Any]) -> Reply:
        clean = {
            "domain": str(profile.get("domain") or "")[:200],
            "name": str(profile.get("name") or "")[:200],
            "summary": str(profile.get("summary") or "")[:800],
            "keywords": [str(k)[:40] for k in (profile.get("keywords") or [])][:8],
        }
        self._state_set(assistant_profile=clean)
        return Reply(
            "Saved your company profile on this machine. When you say “my market” or “leads "
            "in my market”, I will search with these words: "
            + (", ".join(clean["keywords"]) or "none yet")
            + "."
        )

    def site(self, intent: Intent) -> Reply:
        domain = intent.domain or extract_domain(intent.text)
        if not domain:
            profile = self._state_get().get("assistant_profile") or {}
            domain = profile.get("domain") or None
        if not domain:
            return Reply(
                "Which website should I read? Paste the address, for example `example.com`.",
            )
        if self.demo:
            return Reply(
                DEMO_CRAWL + " Install Pépite on your computer to use it.", tone="warn"
            )
        try:
            info = read_site(domain, fetch=self._fetch_site)
        except Exception:
            log.exception("reading %s failed", domain)
            info = SiteInfo(domain=domain)
        if not info.readable:
            return Reply(
                f"I could not read a public homepage at **{domain}**. The site may be down, block "
                "robots, or load its text with scripts. Check the address and try again.",
                tone="warn",
                options=[_opt("Prospect a different company", type="site_prompt")],
            )
        summary, buyers, engine = self._summarise(info)
        pages = len(info.pages)
        profile = parse_rules(" ".join([info.description, *info.headings]), "companies")
        keywords = [k for k in profile.company.keywords if k.lower() not in _STOP_KEYWORDS][:6]
        text = (
            f"I read the public pages of **{info.domain}** "
            f"({pages} page{'s' if pages != 1 else ''}).\n\n"
            f"{summary}\n\nThat tells me who you should be selling to: companies connected to "
            f"{', '.join(keywords[:4]) or 'this field'}."
        )
        opts: list[dict[str, Any]] = []
        market_text = " ".join(keywords[:4]) or info.name
        opts.append(
            _opt("See companies I can sell to", type="find", text=market_text, kind="companies")
        )
        for b in buyers[:2]:
            opts.append(_opt(f"Search: {b}", type="find", text=b, kind="companies"))
        opts.append(
            _opt(
                "See leads in my market",
                type="find",
                text=f"{market_text} founders ceo directors",
                kind="people",
            )
        )
        opts.append(
            _opt(
                "Set up my company profile",
                type="profile",
                profile={
                    "domain": info.domain,
                    "name": info.name,
                    "summary": summary,
                    "keywords": keywords,
                },
            )
        )
        opts.append(_opt("Prospect different company", type="site_prompt"))
        return Reply(
            text,
            options=opts,
            chips=[f"Keyword: {k}" for k in keywords],
            context={"kind": "site", "domain": info.domain, "engine": engine},
        )

    def _summarise(self, info: SiteInfo) -> tuple[str, list[str], str]:
        material = "\n".join(
            [info.name, info.description, *info.headings, info.excerpt[:1500]]
        ).strip()
        if self.llm_on:
            got = llm_site(material)
            if got:
                return got[0], got[1], "llm"
        parts = [info.description.rstrip(".")] if info.description else []
        if info.headings:
            parts.append("Its homepage talks about " + "; ".join(info.headings[:4]))
        text = ". ".join(p for p in parts if p).strip() or info.excerpt[:300]
        name = info.name or info.domain
        return f"{name}: {text}.", [], "rules"

    # ------------------------------------------------------------------ small lookups

    def _company(self, company_id: str | None, domain: str | None) -> Any:
        if company_id:
            for src in (self.store, self.search):
                try:
                    found = src.get_company(company_id)
                except Exception:
                    found = None
                if found is not None:
                    return found
        return None

    def _email(self, person_id: str) -> Any:
        try:
            return self.store.best_email(person_id)
        except Exception:
            return None


def _title_for(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:60] + "…") if len(t) > 60 else t


def _list_name(parsed: ParsedQuery, kind: str) -> str:
    bits = [e.split(":", 1)[-1].split("(")[0].strip() for e in parsed.explanation[:3]]
    base = ", ".join(b for b in bits if b)[:60] or "Assistant results"
    return f"{base} ({date.today().isoformat()})"


def _as_people(parsed: ParsedQuery) -> str:
    bits = [e.split(":", 1)[-1].split("(")[0].strip() for e in parsed.explanation[:4]]
    return "founders ceo " + " ".join(bits)


__all__ = [
    "COMMANDS",
    "STARTERS",
    "Assistant",
    "Intent",
    "Reply",
    "detect_intent",
    "extract_domain",
    "llm_complete",
    "read_site",
]
