"""Job-title vocabulary (EN/FR/DE/ES, some NL/IT): role detection, seniority and department.

Matching runs on accent-folded lowercase text. Words match on letter boundaries; "stems" match
anywhere so German compounds work (Vertriebsleiter, Geschäftsführerin, Mitgründer).
"""

from __future__ import annotations

import re

from pepite.enrich.names import fold as _fold
from pepite.models import Seniority


def fold(text: str) -> str:
    """Lowercase and strip accents (ä -> a, ß -> ss)."""
    return _fold(text).lower()


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split(",") if w.strip()]


def _matcher(words: str, stems: str = "") -> re.Pattern[str]:
    """Regex for comma-separated `words` (on letter boundaries) and `stems` (anywhere)."""
    alts = [r"[\s-]+".join(map(re.escape, w.split())) for w in _words(words)]
    alts.sort(key=len, reverse=True)
    pattern = r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])"
    if stems:
        pattern += "|" + "|".join(map(re.escape, _words(stems)))
    return re.compile(pattern)


_GF = "geschaftsfuhr, geschaeftsfuehr"  # Geschäftsführer, -in, -ung, -ende ...
_GRUND = "grunder, gruender"

_INTERN = (
    "intern, internship, stagiaire, praktikant, praktikantin, werkstudent, werkstudentin, "
    "becario, becaria, tirocinante, stagiair"
)
_OWNER = (
    "owner, co-owner, proprietor, proprietaire, inhaber, inhaberin, eigenaar, propietario, "
    "propietaria, titolare, innehaver, eier"
)
_FOUNDER = (
    "founder, cofounder, fondateur, fondatrice, cofondateur, cofondatrice, fundador, "
    "fundadora, cofundador, cofundadora, oprichter, fondatore"
)
_VP = (
    "vp, svp, evp, avp, vice president, vicepresident, vice presidente, vicepresidente, "
    "vice presidenta"
)
_C_SUITE = (
    "ceo, cto, cfo, coo, cmo, cio, cso, cpo, cro, cdo, chro, cco, chief, president, presidente, "
    "presidenta, pdg, dg, directeur general, directrice generale, director general, "
    "directora general, managing director, general manager, gerant, gerante, chairman, "
    "chairwoman, chairperson, vorstand, consejero delegado, consejera delegada, "
    "amministratore delegato, amministratore unico, administrador unico, gerente general, "
    "algemeen directeur, gesetzlicher vertreter, representant legal, legale rappresentante, "
    "chair of the board, board chair, daglig leder, styreleder, administrerende direktor, "
    "adm direktor, directeur general delegue, directrice generale deleguee, cogerant, cogerante, "
    "administrateur unique, administratrice unique"
)
# Register roles of the board: not executives, so below C-suite (Companies House, Brreg, EDGAR).
_DEPUTY_CHAIR = (
    "vice chair, vice chairman, vice chairwoman, vice chairperson, deputy chair, deputy chairman, "
    "deputy chairwoman, deputy chairperson, nestleder"
)
_PARTNER = (
    "partner, partners, associe, associee, socio, socia, teilhaber, teilhaberin, gesellschafter, "
    "gesellschafterin, vennoot, llp member, llp designated member, designated member, komplementar"
)
_TEAM_LEAD = "team lead, team leader, teamleiter, teamleiterin, teamleitung"
_HEAD = "head, leiter, leiterin, leitung, jefe, jefa, hoofd, responsabile"
_DIRECTOR = (
    "director, directors, directeur, directrice, direktor, direktorin, directora, direttore, "
    "direttrice, prokurist, prokuristin, board member, board observer, styremedlem, varamedlem, "
    "administrateur, administratrice, membre du conseil"
)
_MANAGER = (
    "manager, responsable, gerente, chef, cheffe, supervisor, superviseur, encargado, "
    "company secretary, corporate secretary"
)
_SENIOR = "senior, sr, principal, staff, lead, confirme, confirmee, expert, experte, experto"
_ENTRY = (
    "junior, jr, assistant, assistante, assistenz, asistente, assistente, trainee, apprentice, "
    "apprenti, apprentie, alternant, alternante, azubi, auszubildende, auszubildender, "
    "graduate, engineer, developer, designer, analyst, consultant, accountant, specialist, "
    "representative, associate, technician, ingenieur, ingenieure, developpeur, developpeuse, "
    "comptable, consultante, analyste, technicien, technicienne, charge, chargee, commercial, "
    "commerciale, entwickler, entwicklerin, berater, beraterin, referent, referentin, "
    "sachbearbeiter, sachbearbeiterin, ingeniero, ingeniera, desarrollador, desarrolladora, "
    "consultor, consultora, tecnico"
)
# Generic role nouns: a text containing one of these reads as a job title.
_ROLE_NOUNS = (
    "officer, executive, coordinator, coordinateur, coordinatrice, advisor, adviser, "
    "conseiller, conseillere, asesor, asesora, scientist, researcher, architect, mitarbeiter, "
    "mitarbeiterin, employee, member, board, partenaire"
)

_ASSISTANT_TO = re.compile(
    r"(?<![a-z])(?:assistant|assistante|assistenz|asistente|assistente)(?![a-z]).*"
    r"(?<![a-z])(?:to|of|de|du|der|des|di|del|la)(?![a-z])"
)

_SENIORITY_RULES: tuple[tuple[Seniority, re.Pattern[str]], ...] = (
    (Seniority.INTERN, _matcher(_INTERN)),
    (Seniority.ENTRY, _ASSISTANT_TO),  # "Assistant to the CEO" is not C-suite
    (Seniority.OWNER, _matcher(_OWNER)),
    (Seniority.FOUNDER, _matcher(_FOUNDER, _GRUND)),
    (Seniority.VP, _matcher(_VP)),  # before C-suite: "Vice President" contains "president"
    (Seniority.DIRECTOR, _matcher(_DEPUTY_CHAIR)),  # before C-suite: contains "chairman"
    (Seniority.C_SUITE, _matcher(_C_SUITE, _GF)),
    (Seniority.PARTNER, _matcher(_PARTNER)),
    (Seniority.MANAGER, _matcher(_TEAM_LEAD)),  # before head: "Teamleiter" contains "leiter"
    (Seniority.HEAD, _matcher(_HEAD, "leiter, leitung")),
    (Seniority.DIRECTOR, _matcher(_DIRECTOR)),
    (Seniority.MANAGER, _matcher(_MANAGER)),
    (Seniority.SENIOR, _matcher(_SENIOR)),
    (Seniority.ENTRY, _matcher(_ENTRY, "entwickler, ingenieur, berater")),
)

_DEPARTMENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "sales",
        _matcher(
            "sales, vente, ventes, commercial, commerciale, business development, bizdev, "
            "account executive, account manager, key account, ventas, comercial, vendite, "
            "verkoop",
            "vertrieb, verkauf",
        ),
    ),
    (
        "marketing",
        _matcher(
            "marketing, cmo, brand, communication, communications, comms, growth, seo, "
            "content, pr, public relations, social media, kommunikation, comunicacion, press, "
            "presse",
            "marketing",
        ),
    ),
    (
        "finance",
        _matcher(
            "finance, financial, cfo, accounting, accountant, controller, controlling, "
            "treasury, comptable, comptabilite, financier, financiere, finances, daf, "
            "finanzas, contable, contabilidad",
            "buchhalt, finanz",
        ),
    ),
    (
        "hr",
        _matcher(
            "hr, human resources, people operations, talent, recruiter, recruiting, "
            "recruitment, chro, ressources humaines, rh, drh, recrutement, personal, "
            "personalwesen, recursos humanos, rrhh"
        ),
    ),
    (
        "legal",
        _matcher(
            "legal, counsel, lawyer, attorney, compliance, juridique, juriste, avocat, avocate, "
            "rechtsanwalt, rechtsanwaltin, justitiar, justiziar, abogado, abogada, "
            "data protection, dpo, protection des donnees, company secretary, corporate secretary",
            "datenschutz",
        ),
    ),
    (
        "support",
        _matcher(
            "support, customer success, customer service, customer care, service client, "
            "relation client, helpdesk, help desk, kundenservice, kundendienst, "
            "kundenbetreuung, atencion al cliente, soporte"
        ),
    ),
    (
        "engineering",
        _matcher(
            "engineer, engineering, developer, software, cto, tech, technical, technique, "
            "technology, it, devops, data, architect, developpeur, developpeuse, informatique, "
            "ingeniero, ingeniera, desarrollador, desarrolladora, informatica, sviluppatore, "
            "programmer, programmeur, sre, qa, security, infrastructure, logiciel",
            "entwickl, ingenieur, technik",
        ),
    ),
    (
        "product",
        _matcher("product, produit, produkt, producto, prodotto, ux, ui, design, designer, cpo"),
    ),
    (
        "operations",
        _matcher(
            "operations, ops, coo, logistics, supply chain, procurement, purchasing, "
            "office manager, logistique, achats, betrieb, einkauf, operaciones, compras, "
            "facility",
            "logisti",
        ),
    ),
    (
        "executive",
        _matcher(
            "ceo, founder, cofounder, owner, president, presidente, chief executive, "
            "managing director, general manager, chairman, chairwoman, board, pdg, dg, "
            "directeur general, directrice generale, director general, gerant, gerante, "
            "fondateur, fondatrice, cofondateur, inhaber, inhaberin, fundador, fundadora, "
            "gesetzlicher vertreter, representant legal, chairperson, chair of the board, "
            "board chair, daglig leder, styreleder, nestleder, styremedlem, varamedlem, "
            "innehaver, administrateur, administratrice, board member",
            f"{_GF}, {_GRUND}",
        ),
    ),
)

_ROLE_RE = _matcher(_ROLE_NOUNS, f"leiter, entwickler, berater, praktikant, {_GF}, {_GRUND}")

# Single words that never appear inside a person's name (used by name validation).
ROLE_WORDS: frozenset[str] = frozenset(
    w
    for group in (_INTERN, _OWNER, _FOUNDER, _VP, _C_SUITE, _PARTNER, _HEAD, _DIRECTOR, _MANAGER)
    + (_SENIOR, _ENTRY, _ROLE_NOUNS)
    for w in _words(group)
    if " " not in w
) | {"co-founder", "geschaftsfuhrer", "geschaftsfuhrerin", "grunder", "grunderin"}


def has_role_keyword(text: str) -> bool:
    """True when `text` contains a job-title keyword in any supported language."""
    t = fold(text)
    if _ROLE_RE.search(t):
        return True
    return any(p.search(t) for _, p in _SENIORITY_RULES) or any(
        p.search(t) for _, p in _DEPARTMENT_RULES
    )


def map_seniority(title: str | None) -> Seniority | None:
    """Seniority level for a free-text job title, or None when nothing matches."""
    if not title:
        return None
    t = fold(title)
    return next((level for level, p in _SENIORITY_RULES if p.search(t)), None)


def guess_department(title: str | None) -> str | None:
    """Best-guess department (sales, marketing, ..., executive) for a job title."""
    if not title:
        return None
    t = fold(title)
    return next((dept for dept, p in _DEPARTMENT_RULES if p.search(t)), None)
