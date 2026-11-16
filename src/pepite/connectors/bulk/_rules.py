"""SQL rules shared by the brreg, abr, canada_corps and wikidata_orgs bulk sources.

Everything here returns DuckDB SQL text for a connection made by `pepite.packs.bulk.connect`
(which installs the `lc_*` macros used below), so the rules run set-based over millions of rows.

**Named after a person.** Packs hold companies only. Some registers also list natural persons in
business: Norwegian sole proprietorships (ENK, whose name must by law contain the owner's surname,
foretaksnavneloven § 2-2), Australian partnerships and trusts named after their partners or
family. A name is judged on its words (letters and digits, upper-cased):

- `has_given_name`: a word is a common given name (`GIVEN_NAMES`: Norwegian, Nordic and English).
- `has_initial`: a word is a single letter ("J P SMITH & M A SMITH").
- `has_trade_word`: a word is a legal-form or trade word (`TRADE_WORDS`, whole words), or the
  name contains a trade stem (`TRADE_STEMS`, also inside compounds: "RØRLEGGERSERVICE").

`trade_name_sql` (strict, for entities that *are* a person: Norwegian ENK) keeps a name only when
it has a trade word and neither a given name nor an initial ("NORDMANN BYGG" is kept, "OLA
NORDMANN" and "FOTOGRAF KARI NORDMANN" are not). `personal_name_sql` (lenient, for everything
else that is not a company) flags a name with a given name or an initial and no trade word
("PER HANSEN OG KARI HANSEN", "J SMITH & M SMITH"). Both are heuristics: they err on the side of
dropping a business rather than publishing a person's name.
"""

from __future__ import annotations

from pepite.connectors.base import USER_AGENT
from pepite.models import SIZE_RANGES
from pepite.packs import bulk

# Common given names (upper case). Words that are also ordinary words in business names are left
# out on purpose (DAG, ODD, LIV, SOL, GRO, EVEN, MAY, JUNE, MARK, GRACE, ROSE, VICTORIA, JACK...).
GIVEN_NAMES: frozenset[str] = frozenset(
    """
    OLA OLE PER JAN BJØRN LARS KJELL KNUT ARNE SVEIN HANS THOMAS GEIR TOR MORTEN TERJE ERIK
    MARTIN ANDREAS JOHN ANDERS RUNE TROND TORE JON ÅGE HARALD GUNNAR STEIN ROLF NILS EINAR LEIF
    KRISTIAN ESPEN ØYVIND ØIVIND FRODE STIAN MAGNUS KJETIL HENRIK JØRGEN EIRIK DANIEL MARIUS SIMEN
    JONAS SINDRE KRISTOFFER PETTER PETER FREDRIK ROBERT ALEXANDER CHRISTIAN TOMMY ROGER VIDAR
    ARILD ASBJØRN IVAR HALVOR OLAV OLAF SIGURD EGIL ROAR KÅRE REIDAR FINN HELGE JOHAN JOHANNES KARL
    PAUL PÅL STEINAR SVERRE TORBJØRN VEGARD ØYSTEIN AUDUN BÅRD HÅKON HÅVARD JARLE KENNETH LASSE
    OSKAR OSCAR SEBASTIAN TOBIAS EMIL MATHIAS MATTIAS ADRIAN AKSEL AXEL BENDIK ELIAS FILIP ISAK
    JAKOB JACOB KASPER LUDVIG MATS NIKOLAI OLIVER SANDER SONDRE TORGEIR TORSTEIN ULRIK VEBJØRN
    ASLE ARVID BIRGER EYVIND GUTTORM HALLVARD INGVAR JOAKIM KJARTAN LEIV MAGNE NJÅL RAGNAR SIGMUND
    SIGVE SNORRE TRYGVE TRYGGVE VIGGO YNGVE ØRJAN ERLEND ERLING GJERMUND TORMOD TORLEIF ODDVAR
    ODDBJØRN ARNFINN BJARNE HALLGEIR KOLBJØRN
    ANNE INGER KARI MARIT INGRID BERIT ASTRID BJØRG HILDE ANNA SOLVEIG MARIANNE RANDI IDA NINA TONE
    ELISABETH KRISTIN HEIDI ELLEN GRETHE GRETE SILJE HANNE MARIA CAMILLA TURID WENCHE KJERSTI
    RAGNHILD GURO MONICA MONIKA ANITA LENE TONJE MARI JULIE EMMA NORA SARA SARAH THEA INGEBORG
    ELIN HEGE LISE KAREN BIRGIT GUNN SIGRID TOVE UNNI VIGDIS TORIL TORILL GERD SISSEL MARGRETHE
    TRINE VIBEKE CATHRINE KATRINE KATHRINE CHRISTINE KRISTINE SUSANNE MERETE SIRI ÅSE AUD EVA
    MALIN MARTE MARTHE MAREN MATHILDE SOFIE SOPHIE SYNNØVE TERESE THERESE TORUNN VILDE HELENE
    HELEN HELGA INGUNN JORUNN LAILA LINDA LISBETH MARGIT OLAUG RAGNA REIDUN SIGNE SOLFRID
    SVANHILD ÅSHILD ÅSLAUG ASTRI BENTE BIRGITTE BRITT CECILIE ELSE ERNA GUNHILD GURI HJØRDIS IRENE
    JANNE JENNY KIRSTEN KJELLAUG ODDNY ODDRUN ANNIKEN ANETTE ANNETTE
    DAVID MICHAEL JAMES WILLIAM RICHARD JOSEPH CHARLES CHRISTOPHER MATTHEW ANTHONY DONALD STEVEN
    STEPHEN ANDREW JOSHUA KEVIN BRIAN TIMOTHY RONALD EDWARD JASON JEFFREY RYAN GARY NICHOLAS
    ERIC JONATHAN LARRY JUSTIN SCOTT BRANDON BENJAMIN SAMUEL GREGORY PATRICK RAYMOND DENNIS JERRY
    TYLER AARON ADAM NATHAN HENRY DOUGLAS ZACHARY KYLE NOAH ETHAN JEREMY WALTER KEITH TERRY SEAN
    GERALD CARL HAROLD LAWRENCE JESSE BRYAN BRUCE GABRIEL ALAN ALLAN LOUIS WAYNE RANDY VINCENT
    RUSSELL PHILIP PHILLIP BRADLEY GRAHAM GRAEME IAN NEIL ROSS STUART TREVOR GEOFFREY GEOFF COLIN
    DARREN CRAIG SHANE BRETT GLENN BARRY NIGEL MALCOLM LACHLAN ANGUS HAMISH BRENDAN DECLAN LIAM
    CONNOR HARRY CHARLIE MARY PATRICIA JENNIFER ELIZABETH BARBARA SUSAN JESSICA MARGARET NANCY
    LISA BETTY SANDRA ASHLEY KIMBERLY EMILY DONNA MICHELLE CAROL AMANDA MELISSA DEBORAH STEPHANIE
    REBECCA SHARON LAURA CYNTHIA KATHLEEN AMY ANGELA SHIRLEY BRENDA PAMELA NICOLE SAMANTHA
    KATHERINE CATHERINE DEBRA RACHEL CAROLYN JANET HEATHER DIANE JOYCE KELLY CHRISTINA LAUREN
    JOAN EVELYN JUDITH MEGAN CHERYL ANDREA HANNAH JACQUELINE MARTHA GLORIA TERESA JANICE MARIE
    JULIA KATHRYN FRANCES ALICE ABIGAIL SOPHIA DENISE BEVERLEY BEVERLY DANIELLE THERESA DIANA
    NATALIE LORRAINE JOANNE LEANNE KYLIE TRACEY TRACY BELINDA NARELLE SHARYN LEONIE
    """.split()
)

# Legal-form and trade words, matched as whole words.
TRADE_WORDS: frozenset[str] = frozenset(
    """
    AS ASA ANS DA SA BA KS NUF IKS SF HF KF FKF IL FK BK SK IF LTD PTY INC LLC PLC LLP CO CORP
    BIL IT VVS TV PR EL BAR PUB SPA KRO BÅT TAK MAT VIN LYD LYS INN GYM ART IKT
    """.split()
)

# Trade stems (four letters or more), matched anywhere in the name, also inside compound words.
TRADE_STEMS: tuple[str, ...] = tuple(
    """
    BYGG SERVICE CONSULT KONSULENT RÅDGIV TRANSPORT REGNSKAP DESIGN EIENDOM HANDEL AGENTUR
    ELEKTR RØRLEGG MALER SNEKKER TØMRER MURER ANLEGG MASKIN VERKSTED FRISØR SALONG KLINIKK TERAPI
    TANNLEGE ADVOKAT REVISJON REVISOR MEDIA FOTO FILM MUSIKK STUDIO KUNST GÅRD GARDSBRUK SKOG FISKE
    LANDBRUK GARTNER DRIFT PRODUKSJON IMPORT EKSPORT EXPORT TAXI AUTO RENHOLD TEKNIKK TEKNISK
    SYSTEM SOLUTION PARTNER MANAGEMENT INVEST HOLDING UTVIKLING ARKITEKT INTERIØR MONTASJE
    MONTERING BETONG GRAVING SPRENG LOGISTIKK BUTIKK KAFE CAFE RESTAURANT CATERING BAKERI HOTELL
    CAMPING UTLEIE MEGL FORLAG TRYKK REKLAME MARKED KOMMUNIKASJON UNDERVISNING KURS TOLK OVERSETT
    SYKEPLEI HELSE OMSORG PSYKOLOG LEGESENTER LEGEKONTOR VETERINÆR HUNDE STALL TRENING YOGA COACH
    SPORT EVENT ARRANGEMENT ENTREPREN FASADE TAKST GLASS BLIKK SVEIS LAKK DEKK VASK SKADE MARINE
    SAMEIE BORETTSLAG STIFTELSE FORENING FORBUND KLUBB SAMVIRKE KOMMUNE IDRETT
    TRADING GROUP PRODUCTION CREATIVE DIGITAL SOFTWARE TECH ENGINEER LOGISTIC CONSTRUCT ELECTRIC
    PLUMB CARPENT CLEAN REPAIR PROPERT REALTY MOTORS MEDICAL DENTAL PHARMAC CLINIC LEGAL LAWYER
    ACCOUNT FINANC INSURANCE CAPITAL VENTURE ENTERPRISE INDUSTR MANUFACTUR SUPPL DISTRIBUT
    WHOLESALE RETAIL STORE SHOP MARKET FARM PASTORAL AGRICULT VINEYARD WINE HOTEL MOTEL BAKERY
    BUTCHER HAULAGE FREIGHT COURIER TRAVEL TOURS MARKETING ADVERTIS PRINTING PUBLISH ARCHITECT
    SURVEY MECHANIC AUTOMOTIVE PANEL TYRES ROOFING FENCING CONCRETE EXCAVAT LANDSCAP GARDEN
    NURSERY SECURITY TRAINING EDUCATION ACADEMY SCHOOL CHILDCARE CHURCH MINISTR ASSOCIATION
    SOCIETY FOUNDATION INSTITUTE COUNCIL COOPERATIVE ASSOCIATES SOLICITOR BARRISTER HEALTH PHYSIO
    CHIRO OPTOM VETERINAR LIMITED INCORPORATED CORPORATION COMPANY SERVICES IMPORTS
    """.split()
)


def user_agent(contact: str | None = None) -> str:
    """The Pépite User-Agent, with the declared contact when one is given."""
    return f"{USER_AGENT[:-1]}; contact: {contact})" if contact else USER_AGENT


def _sql_list(values) -> str:
    return "[" + ", ".join(bulk.sql_str(v) for v in sorted(values)) + "]"


def name_words_sql(expr: str) -> str:
    """List of the upper-cased words (letters and digits) of a name."""
    keep = bulk.list_lambda("t", "t <> ''")
    return f"list_filter(string_split_regex(upper({expr}), '[^\\pL\\pN]+'), {keep})"


def has_given_name_sql(expr: str) -> str:
    return f"list_has_any({name_words_sql(expr)}, {_sql_list(GIVEN_NAMES)})"


def has_initial_sql(expr: str) -> str:
    one = bulk.list_lambda("t", "length(t) = 1 AND regexp_full_match(t, '\\pL')")
    return f"(len(list_filter({name_words_sql(expr)}, {one})) > 0)"


def has_trade_word_sql(expr: str) -> str:
    stems = "|".join(sorted(TRADE_STEMS, key=len, reverse=True))
    return (
        f"(list_has_any({name_words_sql(expr)}, {_sql_list(TRADE_WORDS)}) "
        f"OR regexp_matches(upper({expr}), {bulk.sql_str(stems)}))"
    )


def trade_name_sql(expr: str) -> str:
    """TRUE when a sole proprietor's name is a trade name (strict rule, see module docstring)."""
    return (
        f"coalesce({has_trade_word_sql(expr)} AND NOT {has_given_name_sql(expr)} "
        f"AND NOT {has_initial_sql(expr)}, false)"
    )


def personal_name_sql(expr: str) -> str:
    """TRUE when a name looks like one or more persons' names (lenient rule)."""
    return (
        f"coalesce(({has_given_name_sql(expr)} OR {has_initial_sql(expr)}) "
        f"AND NOT {has_trade_word_sql(expr)}, false)"
    )


def size_range_sql(expr: str) -> str:
    """Employee count -> SIZE_RANGES label (as packs.normalize.size_bucket)."""
    whens = []
    for label in SIZE_RANGES:
        if label.endswith("+"):
            whens.append(f"ELSE {bulk.sql_str(label)}")
        else:
            whens.append(f"WHEN {expr} <= {int(label.split('-')[1])} THEN {bulk.sql_str(label)}")
    return f"CASE WHEN {expr} IS NULL OR {expr} < 1 THEN NULL {' '.join(whens)} END"


# Hosts shared by many sites: a profile page on them (facebook.com/acme, acme.wixsite.com) is not
# the company's own domain, so its domain is left empty (the URL stays in `website`). The root of
# such a host (https://www.facebook.com/) is the host company's own site and keeps its domain.
# Public-sector parents (lunner.kommune.no, x.nsw.gov.au) are treated the same way: their
# registrable domain is shared by hundreds of bodies.
SHARED_HOSTS: frozenset[str] = frozenset(
    """
    facebook.com fb.com instagram.com twitter.com x.com linkedin.com youtube.com youtu.be
    tiktok.com pinterest.com google.com goo.gl blogspot.com blogger.com wordpress.com wix.com
    wixsite.com weebly.com squarespace.com jimdo.com jimdofree.com webnode.com webnode.no
    tumblr.com medium.com github.io github.com gitlab.io wikipedia.org wikidata.org archive.org
    t.me linktr.ee about.me yelp.com tripadvisor.com gulesider.no proff.no 1881.no finn.no
    business.site godaddysites.com mystrikingly.com carrd.co site123.me
    kommune.no fylkeskommune.no vgs.no gouv.fr gc.ca sch.uk nhs.uk police.uk nsw.gov.au
    vic.gov.au qld.gov.au wa.gov.au sa.gov.au tas.gov.au act.gov.au nt.gov.au
    """.split()
)

_SCHEME = "^[a-zA-Z][a-zA-Z0-9+.-]*://"


def domain_sql(url: str) -> str:
    """Registrable domain of a company's own website (NULL for profile pages on shared hosts)."""
    path = f"regexp_extract({url}, '^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?[^/?#]*(.*)$', 1)"
    return (
        f"CASE WHEN lc_domain({url}) IN {bulk.sql_in(SHARED_HOSTS)} "
        f"AND (lc_host({url}) <> lc_domain({url}) OR trim({path}, '/') <> '') THEN NULL "
        f"ELSE lc_domain({url}) END"
    )


def website_sql(url: str) -> str:
    """The URL with a scheme ("www.x.no" -> "http://www.x.no"); NULL when it has no valid host."""
    return (
        f"CASE WHEN lc_domain({url}) IS NULL THEN NULL "
        f"WHEN regexp_matches(trim({url}), '{_SCHEME}') THEN trim({url}) "
        f"ELSE 'http://' || trim({url}) END"
    )


def map_case_sql(expr: str, mapping: dict[str, str], default: str = "NULL") -> str:
    """CASE expr WHEN key THEN value ... ELSE default END."""
    whens = " ".join(f"WHEN {bulk.sql_str(k)} THEN {bulk.sql_str(v)}" for k, v in mapping.items())
    return f"CASE {expr} {whens} ELSE {default} END"


__all__ = [
    "GIVEN_NAMES",
    "SHARED_HOSTS",
    "TRADE_STEMS",
    "TRADE_WORDS",
    "domain_sql",
    "has_given_name_sql",
    "has_initial_sql",
    "has_trade_word_sql",
    "map_case_sql",
    "name_words_sql",
    "personal_name_sql",
    "size_range_sql",
    "trade_name_sql",
    "user_agent",
    "website_sql",
]
