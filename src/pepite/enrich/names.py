"""Person-name helpers: accent folding, cleaning, dedupe keys and email-friendly variants."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _table(spec: str) -> dict[int, str]:
    """str.translate table from "à:a ß:ss ..." pairs, upper-case forms included."""
    table: dict[int, str] = {}
    for pair in spec.split():
        src, dst = pair.split(":")
        table[ord(src)] = dst
        if len(src.upper()) == 1 and src.upper() != src:
            table[ord(src.upper())] = dst.capitalize()
    return table


# Common accents; anything else falls back to Unicode decomposition (NFKD).
_FOLD = _table(
    "à:a á:a â:a ã:a ä:a å:a ā:a ç:c č:c ć:c è:e é:e ê:e ë:e ē:e ę:e ě:e ì:i í:i î:i ï:i ı:i "
    "ñ:n ń:n ò:o ó:o ô:o õ:o ö:o ø:o ō:o ù:u ú:u û:u ü:u ů:u ý:y ÿ:y š:s ś:s ş:s ž:z ź:z ż:z "
    "ł:l đ:d ð:d ř:r ß:ss æ:ae œ:oe þ:th"
)
_GERMAN = _table("ä:ae ö:oe ü:ue")

# Lowercase name particles that start a compound surname (de Gaulle, van der Berg).
PARTICLES = frozenset("de du des van von der den da das dos di del della la le ten ter zu".split())

_HONORIFIC = re.compile(
    r"^(?:(?:dr|prof|mr|mrs|ms|mx|mme|mlle|herr|frau|sra?|srta|dott|ing|mag)\.?|m\.|me\."
    r"|dipl\.?-[a-z]+\.?)\s+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\s+(?:ph\.?d|mba|msc|bsc|md|jr|sr|ii|iii|iv|esq|cpa|ll\.?m)\.?$", re.I)
MAX_VARIANTS = 8


def fold(text: str, *, german: bool = False) -> str:
    """Strip accents with a small table (é->e, ß->ss); `german` maps ä/ö/ü to ae/oe/ue."""
    if german:
        text = text.translate(_GERMAN)
    text = text.translate(_FOLD)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def squeeze(text: str) -> str:
    """Folded lowercase alphanumerics only: "Atelier Nuage" -> "ateliernuage"."""
    return re.sub(r"[^a-z0-9]", "", fold(text).lower())


def name_key(name: str) -> str:
    """Dedupe key: "Jean-Pierre DUPONT" and "jean pierre dupont" give the same key."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(name).lower()))


def _fix_case(token: str) -> str:
    return token.title() if len(token) > 1 and token.isupper() else token


def clean_name(raw: str) -> str | None:
    """Drop honorifics, suffixes, brackets and trailing qualifiers; title-case ALL-CAPS tokens."""
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", raw)
    s = re.split(r"[,;|/•·]|\s[-–—]\s", s)[0]
    s = " ".join(s.split())
    while m := _HONORIFIC.match(s):
        s = s[m.end() :]
    while m := _SUFFIX.search(s):
        s = s[: m.start()]
    s = " ".join(_fix_case(t) for t in s.split()).strip(" :-–—")
    if s.endswith(".") and len(s.split()[-1]) > 2:  # sentence full stop, not an initial
        s = s[:-1]
    return s or None


def _surname_options(rest: list[str]) -> list[list[str]]:
    """Surname token lists to try, most likely first."""
    if len(rest) <= 1:
        return [rest]
    idx = next((i for i, t in enumerate(rest) if t.lower() in PARTICLES), None)
    if idx is not None:
        compound = rest[idx:]
        core = [t for t in compound if t.lower() not in PARTICLES]
        return [compound, core] if core else [compound]
    # No particle: middle names dropped, compound (Spanish) surname, first surname only.
    return [rest[-1:], rest, rest[:1]]


def _spellings(tokens: list[str], german: bool) -> list[str]:
    """Email-safe spellings of a name part: hyphen kept and removed, apostrophes removed."""
    joined = re.sub(r"[^a-z0-9-]", "", fold("".join(tokens), german=german).lower()).strip("-")
    if not joined:
        return []
    return [joined, joined.replace("-", "")] if "-" in joined else [joined]


def name_variants(full_name: str, *, german_first: bool = False) -> list[tuple[str, str]]:
    """(first, last) pairs ready for email patterns, most likely first.

    Accents are folded; a name with ä/ö/ü yields both "ae" and "a" spellings (the "ae" one
    first when `german_first`). Compound surnames also yield a particle-free variant, hyphens a
    hyphen-free one. Single-token names give (first, "").
    """
    cleaned = clean_name(full_name) or ""
    tokens = [t.strip(".") for t in cleaned.split() if t.strip(".")]
    if not tokens:
        return []
    first, rest = tokens[0], tokens[1:]
    if rest:
        rest = [t for t in rest[:-1] if len(t) > 1] + rest[-1:]  # drop middle initials
    schemes = [False]
    if any(c in "äöüÄÖÜ" for c in cleaned):
        schemes = [True, False] if german_first else [False, True]
    out: list[tuple[str, str]] = []
    for german in schemes:
        for surname in _surname_options(rest):
            for f in _spellings([first], german):
                for last in _spellings(surname, german) or [""]:
                    if (f, last) not in out:
                        out.append((f, last))
    return out[:MAX_VARIANTS]


# ----------------------------------------------------------------------------- email spellings
#
# Everything below serves email-address inference (enrich.patterns). It never changes the
# helpers above, which the people extractor relies on.

# Common given names (folded, lowercase) in the languages Pépite meets most often. Used to
# tell a middle name from a first surname, a given name from a family name in an address shape
# ("anna.berg" vs "berg.anna") and family-first East Asian names.
GIVEN_NAMES = frozenset(
    """
aaron adam adrian ahmed aidan aisha alain alan albert alberto alejandro alessandro alex alexander
alexandra alexandre alexis alfred ali alice alicia alina alison amanda amelia amina amir amira
amit amy ana anais anastasia anders andre andrea andreas andrei andres andrew andy angela
angelica anita ann anna anne annette anthony antoine antonio arjun arnaud arthur astrid aurelie
ava axel barbara barbel bart beatrice beatriz ben benjamin benoit bernard bertrand birgit bjorn
blanca bob boris brian bridget bruno camille carl carla carlos carmen carol caroline catherine
cecile celine charles charlie charlotte chiara chloe chris christian christine christoph
christopher claire clara claude claudia clement colin cristina daan daniel daniela danielle
david deborah deepa denis diana diego dimitri dirk dominique doris dorte edward elena elias
elisa elisabeth elizabeth ella ellen elodie emil emilia emily emma enrique eric erik ernst
esther ethan eva evelyn fabian fabien fabrice fatima federico felix femke fernando filip
florence florian francesca francesco francisco francois francoise frank franz freja frederic
frederik gabriel gabriela gaelle george georg gerard gerhard gilles giorgio giovanni giulia
grace gregor guillaume gunter gustav hamza hannah hans harry hassan hector heinz helen helena
helene henri henrik henry herve hiroshi hugo huda ibrahim ida ines ingrid irene isabel isabella
isabelle ivan jack jacob jacques jakob james jan jana jane janet jasmin javier jean jeanne jeff
jennifer jens jeremy jerome jesus jill joan joao joel johan johann johanna john jonas jonathan
jordi jorg jorge jose josef joseph josh joshua juan judith julia julian julie julien jurgen
justin jutta kai karen karim karin karl kate katharina katherine kathrin katja kavya kees ken
kenji kevin khalid kim klaus kristina lars laura laurent layla lea leah lena leo leon leonie
liam lieke linda lisa lotte louis louise luc luca lucas lucia lucie luis lukas luke magnus
maja manon manuel marc marco marcus margaret margot maria marie mariam marianne marie-claire
marina mario mark marta martin martina mary mateo mathias mathieu matilda matteo matthew
matthias max maxime maximilian megan mei melanie mia michael michel michele michelle miguel
mika mikael mohamed mohammed monica monika moritz muhammad nadia nadine nathalie nathan neha
nicolas niels nikolai nils nina noah noemie noor nora nuria olga oliver olivia omar oscar pablo
pamela paola pascal patricia patrick paul paula pauline pedro per peter petra philip philipp
philippe pierre pieter pilar pooja priya rachel rafael rahul rania raquel rasmus raul rebecca
regina rene ricardo richard rick rita robert roberto rohan roman rosa ruben ryan sabine sam
samir samuel sandra sanjay sanne sara sarah sebastian sebastien selma sergio silvia simon
simone sofia sofie sophia sophie soren stefan stefano stephane stephanie steve steven susan
susanne sven sylvie tanja tariq teresa theo theresa thierry thomas thor tim tina tobias tom
tomas tony ulrich ursula uwe valentin valeria valerie vanessa vera veronique victor victoria
vikram vincent virginie walter werner william wim yann yasmin yusuf yves zoe
""".split()
)

# Family names that come first in Chinese, Japanese, Korean and Vietnamese names.
EAST_ASIAN_FAMILY = frozenset(
    """
wang li zhang liu chen yang huang zhao wu zhou xu sun ma zhu hu guo he lin gao luo zheng liang
xie tang han feng deng cao peng zeng xiao tian dong pan yuan cai jiang yu du ye cheng wei su lu
ding ren shen yao lu jin qian kong bai cui kang mao qiu qin gu hou shao meng long wan duan
sato suzuki takahashi tanaka watanabe ito yamamoto nakamura kobayashi kato yoshida yamada sasaki
yamaguchi matsumoto inoue kimura hayashi shimizu yamazaki mori abe ikeda hashimoto ishikawa
ogawa okada goto hasegawa murakami kondo ishii saito sakamoto endo aoki fujii nishimura fukuda
kim lee park choi jung jeong kang cho yoon yun jang lim im han oh seo shin kwon hwang ahn song
nguyen tran le pham hoang huynh phan vu vo dang bui do ho ngo duong ly
""".split()
)
# Given names common in those cultures (romanised): tells "Wei Zhang" from "Zhang Wei".
EAST_ASIAN_GIVEN = frozenset(
    """
wei fang jing lei min jun yan hui xiaoming xiaoyu jian ming na ying hao yu zhiwei jiahui yifan
zihan haoran yuxuan xinyi qiang tao lan xin yi jie ping hong yong bin bo chao gang hua kai
hiroshi takashi kenji yuki haruto sota ren yuto daiki akira satoshi naoki yumi aiko haruka yui
sakura mei rin emi keiko naomi ayaka kaori kenta shota yusuke takumi
minjun seoyeon jiho haeun doyun jiwoo seojun subin hyunwoo jimin eunji youngho sungmin hyejin
""".split()
)

# Name particles for email spelling (wider than PARTICLES, which the people extractor uses).
_EMAIL_PARTICLES = PARTICLES | frozenset("al el ben ait abu dal dei degli do e y st saint".split())
_PATRONYMIC = frozenset("bin ibn bint".split())
_HISPANIC_CC = frozenset("es mx ar co cl pe ve uy ec bo py cr gt hn sv ni pa do cu pr".split())
_GENERIC_SLD = frozenset("co com net org ac gov edu or ne go".split())
_RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")
MAX_EMAIL_VARIANTS = 12
SHOWN_SURNAME = 0.75  # Spanish-speaking company, one surname shown: the address uses it
NICKNAME_SHARE = 0.08  # people with a common short form who use it in their address
# Formal English given names and the short form most often used in work addresses.
NICKNAMES = dict(
    pair.split(":")
    for pair in """
abigail:abby alexander:alex alexandra:alex andrew:andy anthony:tony benjamin:ben charles:charlie
christopher:chris daniel:dan deborah:deb edward:ed elizabeth:liz frederick:fred gregory:greg
james:jim jeffrey:jeff jennifer:jen jonathan:jon joseph:joe joshua:josh katherine:kate
catherine:kate kenneth:ken kimberly:kim margaret:maggie matthew:matt michael:mike
nicholas:nick pamela:pam patricia:pat rebecca:becky richard:rick robert:bob ronald:ron
samuel:sam stephen:steve steven:steve susan:sue theodore:ted thomas:tom timothy:tim
victoria:vicky william:bill zachary:zach
""".split()
)

# Cyrillic and Greek to Latin. Emails use everyday spellings: "common" (Yulia, Dmitry, Alexey),
# ICAO passport spellings (Iuliia, Dmitrii) and BGN/PCGN (Yuliya, Dmitriy).
_CYR = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch _ y _ e yu ya".split(),
        strict=True,
    )
)
_CYR_ICAO = _CYR | {"й": "i", "ю": "iu", "я": "ia", "ъ": "ie"}
_CYR_EXTRA = {"є": "ye", "і": "i", "ї": "yi", "ґ": "g", "ў": "u"}  # Ukrainian, Belarusian
_GREEK = dict(
    zip(
        "αάβγδεέζηήθιίϊΐκλμνξοόπρσςτυύϋΰφχψωώ",
        "a a v g d e e z i i th i i i i k l m n x o o p r s s t y y y y f ch ps o o".split(),
        strict=True,
    )
)
_EXPANDED = _table("ä:ae ö:oe ü:ue ø:oe å:aa")  # German, Danish, Norwegian house spellings
_EXPANDABLE = frozenset("äöüøåÄÖÜØÅ")


def country_hint(domain: str) -> str:
    """Country code a domain suggests: "firma.de" -> "de", "x.co.uk" -> "uk", "x.com" -> ""."""
    d = domain.lower().rstrip(".")
    for suffix in _RESERVED_TLDS:  # test fixtures: "firma.de.example" reads as "firma.de"
        d = d.removesuffix(suffix)
    labels = d.split(".")
    tld = labels[-1] if labels else ""
    if len(tld) == 2:
        return tld
    if len(labels) >= 3 and labels[-2] in _GENERIC_SLD and len(labels[-1]) == 2:
        return labels[-1]
    return ""


def _script(text: str) -> str:
    """latin | cyrillic | greek | other (scripts we cannot spell in ASCII without a dictionary)."""
    for c in text:
        if c.isalpha() and ord(c) > 0x24F:
            if 0x400 <= ord(c) <= 0x52F:
                return "cyrillic"
            if 0x370 <= ord(c) <= 0x3FF:
                return "greek"
            if not 0x1E00 <= ord(c) <= 0x1EFF:  # Latin Extended Additional (Vietnamese)
                return "other"
    return "latin"


_PINYIN = re.compile(
    r"^(?:(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?(?:iang|iong|uang|ang|eng|ing|ong|uan|ian|iao|uai"
    r"|ai|ei|ao|ou|an|en|in|un|er|ia|ie|iu|ua|uo|ue|ui|a|e|i|o|u|v))+$"
)
_ROMAJI = re.compile(r"^(?:(?:sh|ch|ts|[kgsztdnhbpmyrwfj]y?)?[aeiou]n?)+$")


def _east_asian_given_like(token: str) -> bool:
    """Romanised Chinese, Japanese or Korean given name ("Wei", "Hiroshi", "Min-jun")."""
    t = fold(token).lower()
    if t.replace("-", "") in EAST_ASIAN_GIVEN:
        return True
    if "-" in t:
        return all(0 < len(p) <= 4 and _PINYIN.match(p) for p in t.split("-"))
    return bool(_PINYIN.match(t) or _ROMAJI.match(t))


def _cyrillic(text: str, scheme: str) -> str:
    t = text.lower()
    if scheme == "common":  # Дмитрий -> dmitry, Алексей -> alexey, Юлия -> yulia
        t = re.sub(r"ий\b", "y", t).replace("кс", "x")
        t = re.sub(r"ия\b", "ia", t)
    elif scheme == "bgn":
        t = re.sub(r"ий\b", "iy", t)
    table = (_CYR_ICAO if scheme == "icao" else _CYR) | _CYR_EXTRA
    return "".join(table.get(c, c) for c in t).replace("_", "")


def _greek(text: str) -> str:
    t = text.lower().replace("ου", "ou").replace("αι", "ai").replace("ει", "ei")
    return "".join(_GREEK.get(c, c) for c in t)


def _ascii(token: str, expand: bool) -> str:
    """Folded lowercase spelling of one name token, hyphens kept, other punctuation dropped."""
    if expand:
        token = token.translate(_EXPANDED)
    return re.sub(r"[^a-z0-9-]", "", fold(token).lower()).strip("-")


@dataclass(frozen=True)
class NameVariant:
    """One way to spell a name in an address. Each combo lists the house rules that lead to
    it, as (rule, choice, prior) triples; `weight` sums the products of the priors. Weights
    add up to less than 1 when some readings have no spelling (a surname the page omits)."""

    first: str
    last: str
    weight: float
    choices: tuple[tuple[str, str, float], ...] = ()  # rules every way to this spelling shares
    combos: tuple[tuple[tuple[str, str, float], ...], ...] = ()  # each way to this spelling


@dataclass
class _Parsed:
    given: list[str]
    family: list[str]
    middle: list[str]
    order_prior: float | None  # P(family-first reading) for ambiguous East Asian names


def _caps_family(raw: str, tokens: list[str]) -> set[int]:
    """Indexes of tokens written in capitals ("Jean DUPONT", "ZHANG Wei"): the family name."""
    caps = {
        t.strip(".,").lower()
        for t in raw.split()
        if len(t.strip(".,")) > 1
        and t.strip(".,").replace("-", "").replace("'", "").isalpha()
        and t.strip(".,").isupper()
    }
    marked = {i for i, t in enumerate(tokens) if t.lower() in caps}
    return marked if marked and len(marked) < len(tokens) else set()


def _parse(raw: str) -> _Parsed | None:
    cleaned = clean_name(raw) or ""
    tokens = [t.strip(".") for t in cleaned.split() if t.strip(".")]
    if not tokens:
        return None
    caps = _caps_family(raw, tokens)
    # Gulf patronymic ("Khalid bin Saeed Al-Harbi"): never part of the address.
    i = 1
    while i < len(tokens) - 2:
        if tokens[i].lower() in _PATRONYMIC:
            del tokens[i : i + 2]
            caps = {j - 2 if j > i else j for j in caps if j not in (i, i + 1)}
        else:
            i += 1
    if caps:
        first_caps = min(caps)
        family = [t for j, t in enumerate(tokens) if j in caps]
        given = [t for j, t in enumerate(tokens) if j not in caps]
        if first_caps == 0 or len(given) == 1:
            return _Parsed(given[:1], family, given[1:], None)
    if len(tokens) == 1:
        return _Parsed(tokens, [], [], None)
    a, b = fold(tokens[0]).lower(), fold(tokens[-1]).lower().replace("-", "")
    order: float | None = None
    if (
        len(tokens) <= 3
        and a in EAST_ASIAN_FAMILY
        and (b in EAST_ASIAN_FAMILY or _east_asian_given_like(tokens[-1]))
        and fold(tokens[-1]).lower() not in GIVEN_NAMES - EAST_ASIAN_GIVEN
    ):
        a_given, b_given = a in EAST_ASIAN_GIVEN, b in EAST_ASIAN_GIVEN or "-" in tokens[-1]
        if b not in EAST_ASIAN_FAMILY:
            order = 0.8
        elif b_given and not a_given:
            order = 0.6
        elif a_given and not b_given:
            order = 0.3
        elif b not in GIVEN_NAMES:
            order = 0.5
    if order is not None:
        return _Parsed(tokens[1:], tokens[:1], [], order)
    given, rest = tokens[:1], [t for t in tokens[1:-1] if len(t) > 1] + tokens[-1:]
    middle: list[str] = []
    idx = next((j for j, t in enumerate(rest) if t.lower() in _EMAIL_PARTICLES), None)
    if idx is not None and idx < len(rest) - 1:
        middle, rest = rest[:idx], rest[idx:]
    else:
        while len(rest) > 1 and fold(rest[0]).lower() in GIVEN_NAMES:
            middle.append(rest.pop(0))
    return _Parsed(given, rest, middle, None)


def _options(rule: str, weights: dict[str, float]) -> list[tuple[str, str, float]]:
    total = sum(weights.values())
    return [(rule, k, v / total) for k, v in weights.items() if v > 0]


def email_variants(full_name: str, domain: str = "") -> list[NameVariant]:
    """Spellings of a person's name for addresses, with prior weights summing to 1.

    Covers accent folding and the house rules a company applies to every address: German and
    Nordic letters (ü -> ue or u, ø -> oe or o), hyphens (jean-pierre, jeanpierre, jean),
    particles (vandenberg or berg, alharbi or harbi), compound surnames (Spanish first surname,
    both, or the last one), middle names, Cyrillic and Greek transliteration and family-first
    East Asian names. `enrich.patterns` learns each company's choices from its public addresses.
    Names in other scripts give no variants.
    """
    script = _script(full_name)
    if script == "other":
        return []
    parsed = _parse(full_name)
    if parsed is None:
        return []
    cc = country_hint(domain)
    text = " ".join(parsed.given + parsed.family)
    readings: list[tuple[list[str], list[str], list[tuple[str, str, float]]]] = []
    if parsed.order_prior is not None:  # family-first or as shown
        p = parsed.order_prior
        readings.append((parsed.given, parsed.family, [("order", "family_first", p)]))
        readings.append((parsed.family, parsed.given, [("order", "as_shown", 1 - p)]))
    else:
        readings.append((parsed.given, parsed.family, []))

    spell: list[tuple[str, str, float]] = [("", "", 1.0)]
    if script == "cyrillic":
        spell = _options("cyrillic", {"common": 0.5, "icao": 0.3, "bgn": 0.2})
    elif any(c in _EXPANDABLE for c in text):
        expand = {"de": 0.75, "at": 0.75, "ch": 0.7, "dk": 0.35, "no": 0.35}.get(cc, 0.2)
        spell = _options("translit", {"expanded": expand, "plain": 1 - expand})
    hispanic = cc in _HISPANIC_CC or bool(re.search(r"[áéíóúñ]|ez\b", text.lower()))

    acc: dict[tuple[str, str], list] = {}
    for given, family, base in readings:
        given_opts: list[list[tuple[str, str, float]]] = [[]]
        if parsed.middle and parsed.order_prior is None:
            given_opts = [[("given", "first", 0.75)], [("given", "with_middle", 0.25)]]
        family_core = family
        lead: list[str] = []
        while len(family_core) > 1 and family_core[0].lower() in _EMAIL_PARTICLES:
            lead.append(family_core[0])
            family_core = family_core[1:]
        arabic_prefix = bool(family_core) and bool(re.match(r"(?i)^(al|el)-", family_core[0]))
        particle_opts: list[list[tuple[str, str, float]]] = [[]]
        if lead or arabic_prefix:
            particle_opts = [[c] for c in _options("particle", {"keep": 0.65, "drop": 0.35})]
        compound_opts: list[list[tuple[str, str, float]]] = [[]]
        if cc in _HISPANIC_CC and len(family_core) == 1 and not lead and len(readings) == 1:
            # Spanish-speaking company, one surname shown: the address is spellable only if the
            # company uses the first surname (the second one, or both, would be unknown).
            compound_opts = [[("surname", "first", SHOWN_SURNAME)]]
        elif len(family_core) >= 2:
            w = (
                {"first": 0.6, "joined": 0.25, "last": 0.15}
                if hispanic
                else {"last": 0.6, "first": 0.2, "joined": 0.2}
            )
            compound_opts = [[c] for c in _options("surname", w)]
        for sp in spell:
            for g_opt in given_opts:
                for p_opt in particle_opts:
                    for c_opt in compound_opts:
                        choices = [*base, *g_opt, *p_opt, *c_opt]
                        if sp[0]:
                            choices.append(sp)
                        firsts, lasts = _assemble(
                            given,
                            parsed.middle,
                            lead,
                            family_core,
                            dict((r, c) for r, c, _ in choices),
                            script,
                            sp[1],
                        )
                        for f, f_choice in firsts:
                            for last, l_choice in lasts:
                                full = choices + f_choice + l_choice
                                weight = 1.0
                                for _, _, prior in full:
                                    weight *= prior
                                key = (f, last)
                                if key in acc:
                                    acc[key][0] += weight
                                    acc[key][1] = acc[key][1] & set(full)
                                    acc[key][2].append(tuple(full))
                                else:
                                    acc[key] = [weight, set(full), [tuple(full)]]
    ranked = sorted(acc.items(), key=lambda kv: -kv[1][0])[:MAX_EMAIL_VARIANTS]
    return [
        NameVariant(f, last, v[0], tuple(sorted(v[1])), tuple(v[2])) for (f, last), v in ranked if f
    ]


def _hyphen_forms(word: str, rule: str, weights: dict[str, float]) -> list[tuple[str, list]]:
    if "-" not in word:
        return [(word, [])]
    parts = [p for p in word.split("-") if p]
    forms = {"keep": word, "drop": "".join(parts), "first": parts[0]}
    return [(forms[k], [c]) for c in _options(rule, weights) for k in [c[1]]]


def _assemble(
    given: list[str],
    middle: list[str],
    lead: list[str],
    family: list[str],
    rules: dict[str, str],
    script: str,
    spelling: str,
) -> tuple[list[tuple[str, list]], list[tuple[str, list]]]:
    """(first spellings, last spellings), each with the hyphen choices it implies."""

    def asc(token: str) -> str:
        if script == "cyrillic":
            return re.sub(r"[^a-z0-9-]", "", _cyrillic(token, spelling))
        if script == "greek":
            return re.sub(r"[^a-z0-9-]", "", fold(_greek(token)).lower())
        return _ascii(token, spelling == "expanded")

    g = [asc(t) for t in given]
    first = "".join(g) if len(g) > 1 else (g[0] if g else "")
    if rules.get("given") == "with_middle" and middle:
        first += asc(middle[0])
    if not first:
        return [], []
    firsts = _hyphen_forms(first, "hyphen_given", {"keep": 0.48, "drop": 0.44, "first": 0.08})
    nick = NICKNAMES.get(first)
    if nick and len(g) == 1 and rules.get("given") != "with_middle":
        firsts = [
            (first, [("nickname", "formal", 1 - NICKNAME_SHARE)]),
            (nick, [("nickname", "short", NICKNAME_SHARE)]),
        ]
    if not family:
        return firsts, [("", [])]
    core = family
    if len(core) >= 2:
        core = {"first": core[:1], "last": core[-1:], "joined": core}[rules.get("surname", "last")]
    words = [asc(t) for t in core]
    if rules.get("particle") == "drop":
        words = [re.sub(r"^(al|el)-", "", w) for w in words]
        prefix = ""
    else:
        prefix = "".join(asc(t) for t in lead)
    last_word = prefix + "".join(words)
    weights = {"keep": 0.35, "drop": 0.55, "first": 0.10}
    if re.match(r"^(al|el)-", last_word):  # Arabic article: al-harbi or alharbi, never "al"
        weights = {"keep": 0.35, "drop": 0.65}
    lasts = _hyphen_forms(last_word, "hyphen_family", weights)
    return firsts, [(w.strip("-"), c) for w, c in lasts if w.strip("-")]
