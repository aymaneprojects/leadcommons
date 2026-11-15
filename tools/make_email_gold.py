"""Generate the synthetic email gold set used by `leadcommons eval emails` and the tests.

Everything here is fake: people are random combinations of common given and family names,
companies live under the reserved `.example` TLD (RFC 2606, e.g. `nordwind.de.example`, where
the middle label carries the country hint a real `.de` domain would give).

The generator renders true addresses with its own small renderer and domain "house rules"
(umlaut spelling, hyphens, particles, compound surnames, transliteration, name order); it never
imports the finder's name or pattern code, so the evaluation does not grade the model with its
own logic. Its parameters are assumptions written down below, not measurements; build a real
gold set with `leadcommons eval build-gold` to measure on real companies.

    python tools/make_email_gold.py --seed 11 --domains 300 --out tests/fixtures/email_gold/dev
    python tools/make_email_gold.py --seed 97 --domains 150 --out tests/fixtures/email_gold/test

Each run writes `<out>.csv` (full_name, domain, true_address) and `<out>.world.json` (per
domain: public site names and addresses, MX hosts, simulated mail-server behaviour, existing
mailboxes). The evaluator removes each person's own address from the site evidence
(leave-one-out), so the task is "find the address of a person whose address is not published".
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------- name pools


def _w(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


EN_GIVEN = _w(
    "James, Robert, Michael, William, David, Richard, Joseph, Thomas, Christopher, Daniel, "
    "Matthew, Anthony, Andrew, Joshua, Benjamin, Samuel, Nicholas, Jonathan, Mary, Patricia, "
    "Jennifer, Linda, Elizabeth, Susan, Jessica, Sarah, Karen, Emily, Rebecca, Katherine, "
    "Laura, Rachel, Megan, Olivia, Hannah, Grace, Chloe, Emma, Alexander, Margaret, Ryan, "
    "Lucy, Oliver, Harry, Charlotte, Amelia, George, Jack, Sophie, Ethan"
)
EN_FAMILY = _w(
    "Smith, Johnson, Williams, Brown, Jones, Miller, Davis, Wilson, Anderson, Taylor, Moore, "
    "Jackson, Thompson, White, Harris, Clark, Lewis, Robinson, Walker, Young, Allen, King, "
    "Wright, Scott, Green, Baker, Adams, Nelson, Hill, Campbell, Mitchell, Carter, Roberts, "
    "O'Brien, O'Connor, McDonald, MacLeod, Fitzgerald, Hughes, Edwards, Collins, Stewart, "
    "Morris, Murphy, Cook, Rogers, Morgan, Cooper, Peterson, Bailey, Reed, Kelly, Howard, "
    "Smith-Jones, Parker-Bowles, Evans, Turner, Phillips, Ward, Watson, Brooks, Bennett"
)
EN_MIDDLE = _w("Anne, Marie, Lee, James, John, Louise, Elizabeth, Michael, Rose, Paul, Jane")
NICKNAMES = {
    "Robert": "Bob",
    "William": "Bill",
    "Katherine": "Kate",
    "Elizabeth": "Liz",
    "Michael": "Mike",
    "Alexander": "Alex",
    "Christopher": "Chris",
    "Jennifer": "Jen",
    "Margaret": "Maggie",
    "Thomas": "Tom",
    "Daniel": "Dan",
    "Benjamin": "Ben",
    "Samuel": "Sam",
    "Nicholas": "Nick",
    "Jonathan": "Jon",
    "Matthew": "Matt",
    "Andrew": "Andy",
    "Joseph": "Joe",
    "Anthony": "Tony",
    "Richard": "Rick",
    "James": "Jim",
    "Patricia": "Pat",
    "Susan": "Sue",
    "Rebecca": "Becky",
    "Joshua": "Josh",
}
FR_GIVEN = _w(
    "Jean, Pierre, Marie, Sophie, Camille, Julien, Nicolas, Thomas, Antoine, Mathieu, Céline, "
    "Élodie, Hélène, Isabelle, Nathalie, Aurélie, Françoise, François, Jérôme, Stéphane, "
    "Sébastien, Frédéric, Benoît, Gaëlle, Anaïs, Chloé, Léa, Manon, Inès, Jean-Pierre, "
    "Jean-Marc, Marie-Claire, Anne-Sophie, Pierre-Yves, Jean-Baptiste, Marie-Hélène, Loïc, "
    "Maëlle, Noémie, Clément"
)
FR_FAMILY = _w(
    "Martin, Bernard, Dubois, Robert, Richard, Petit, Durand, Leroy, Moreau, Simon, Laurent, "
    "Lefèvre, Michel, Garcia, Bertrand, Roux, Vincent, Fournier, Morel, Girard, André, "
    "Lefebvre, Mercier, Dupont, Lambert, Bonnet, Gauthier, Chevalier, Faure, Rousseau, Blanc, "
    "Guérin, Henry, Roussel, Perrin, Morin, Clément, Gaillard, Lemaire, de la Fontaine, "
    "de Villiers, du Pont, Le Gall, Le Goff, N'Diaye, Béranger, Brûlé, Côté, d'Aubigné"
)
DE_GIVEN = _w(
    "Jürgen, Jörg, Günter, Uwe, Klaus, Stefan, Andreas, Michael, Thomas, Matthias, Tobias, "
    "Lukas, Jonas, Felix, Maximilian, Sebastian, Florian, Anna, Katharina, Julia, Laura, Lena, "
    "Sarah, Sabine, Ute, Birgit, Jutta, Bärbel, Dörte, Sören, Björn, Jens, Kai, Hans-Peter, "
    "Karl-Heinz, Anna-Lena, Lea, Moritz, Henrik, Theresa"
)
DE_FAMILY = _w(
    "Müller, Schmidt, Schneider, Fischer, Weber, Meyer, Wagner, Becker, Schulz, Hoffmann, "
    "Schäfer, Koch, Bauer, Richter, Klein, Wolf, Schröder, Neumann, Schwarz, Zimmermann, "
    "Braun, Krüger, Hartmann, Lange, Werner, Krause, Lehmann, Köhler, Herrmann, König, Walter, "
    "Huber, Kaiser, Fuchs, Möller, Weiß, Jung, Hahn, Vogel, Keller, Günther, Böhm, Krämer, "
    "Jäger, Groß, Kühn, Seidel, Brandt, von Arnim, von der Heide, Schulte-Döinghaus, Pfeiffer"
)
ES_GIVEN = _w(
    "José, Antonio, Manuel, Francisco, Juan, David, Javier, Daniel, Carlos, Jesús, Alejandro, "
    "Miguel, Rafael, Pablo, Sergio, Fernando, Jorge, Luis, Alberto, Álvaro, María, Carmen, Ana, "
    "Isabel, Laura, Lucía, Cristina, Marta, Elena, Sofía, Paula, Raquel, Beatriz, Nuria, Inés, "
    "Pilar, María José, José Luis, Juan Carlos, María Ángeles"
)
ES_FAMILY = _w(
    "García, Rodríguez, González, Fernández, López, Martínez, Sánchez, Pérez, Gómez, Martín, "
    "Jiménez, Ruiz, Hernández, Díaz, Moreno, Muñoz, Álvarez, Romero, Alonso, Gutiérrez, "
    "Navarro, Torres, Domínguez, Vázquez, Ramos, Gil, Ramírez, Serrano, Blanco, Molina, "
    "Morales, Suárez, Ortega, Delgado, Castro, Ortiz, Rubio, Marín, Sanz, Núñez, Iglesias, "
    "Medina, Garrido, Cortés, Castillo, Santos, Lozano, Guerrero, Cano, Prieto, Méndez, Peña"
)
NL_GIVEN = _w(
    "Jan, Pieter, Kees, Willem, Hendrik, Joost, Bram, Daan, Sem, Lars, Femke, Sanne, Lotte, "
    "Anouk, Eva, Lieke, Marieke, Ingrid, Thijs, Ruben, Niels, Floor, Jeroen, Maarten"
)
NL_FAMILY = _w(
    "de Vries, van den Berg, van Dijk, Bakker, Janssen, Visser, Smit, Meijer, de Boer, Mulder, "
    "de Groot, Bos, Vos, Peters, Hendriks, van Leeuwen, Dekker, Brouwer, de Wit, Dijkstra, "
    "Smits, de Graaf, van der Meer, van der Linden, Kok, Jacobs, de Haan, Vermeulen, "
    "van den Heuvel, van der Veen, van den Broek, de Bruijn, de Jong, van Beek"
)
NO_GIVEN = _w(
    "Lars, Anders, Per, Erik, Johan, Mikael, Søren, Rasmus, Mads, Henrik, Bjørn, Øystein, "
    "Åsa, Björn, Sofie, Ingrid, Kari, Sigrid, Maja, Freja, Linnéa, Märta, Jørgen, Åse"
)
NO_FAMILY = _w(
    "Hansen, Johansen, Olsen, Larsen, Andersen, Pedersen, Nielsen, Kristiansen, Jensen, "
    "Karlsen, Johansson, Andersson, Karlsson, Nilsson, Eriksson, Larsson, Persson, Svensson, "
    "Østergaard, Søndergaard, Mørk, Bjørnstad, Åberg, Lindqvist, Sjöberg, Ström, Holmström, "
    "Nyström, Lundström, Ekström, Bergström, Dæhli, Løvås"
)
AR_GIVEN = _w(
    "Mohammed, Muhammad, Mohamed, Ahmed, Ali, Omar, Youssef, Khalid, Hassan, Hussein, Ibrahim, "
    "Abdullah, Abdulrahman, Tariq, Karim, Samir, Nadia, Fatima, Aisha, Layla, Mariam, Noor, "
    "Sara, Huda, Rania, Yasmin, Salma, Amira, Fatima-Zahra, Hamza"
)
AR_FAMILY = _w(
    "Al-Rashid, Al-Harbi, Al-Qahtani, Al-Mansouri, Al Nuaimi, El Amrani, El Idrissi, Benali, "
    "Bennani, Haddad, Khoury, Nasser, Hamdan, Saleh, Mansour, Farouk, Aziz, Rahman, Abdelaziz, "
    "Ben Youssef, El-Sayed, Mahmoud, Mostafa, Al-Shamsi, Al Zaabi, Tazi, Alaoui, Chraibi"
)
AR_FATHER = _w("Saeed, Khalid, Ahmed, Rashid, Hamad, Sultan, Faisal, Nasser")
CN_GIVEN = _w(
    "Wei, Fang, Jing, Lei, Min, Jun, Yan, Hui, Xiaoming, Xiaoyu, Jian, Ming, Na, Ying, Hao, Yu, "
    "Zhiwei, Jiahui, Yifan, Zihan, Haoran, Yuxuan, Xinyi, Qiang, Tao, Lan"
)
CN_FAMILY = _w(
    "Wang, Li, Zhang, Liu, Chen, Yang, Huang, Zhao, Wu, Zhou, Xu, Sun, Ma, Zhu, Hu, Guo, He, "
    "Lin, Gao, Luo, Zheng, Liang, Xie, Tang, Han, Feng, Deng, Cao, Peng, Zeng, Xiao, Tian"
)
JP_GIVEN = _w(
    "Hiroshi, Takashi, Kenji, Yuki, Haruto, Sota, Ren, Yuto, Daiki, Akira, Satoshi, Naoki, "
    "Yumi, Aiko, Haruka, Yui, Sakura, Mei, Rin, Emi, Keiko, Naomi, Ayaka, Kaori"
)
JP_FAMILY = _w(
    "Sato, Suzuki, Takahashi, Tanaka, Watanabe, Ito, Yamamoto, Nakamura, Kobayashi, Kato, "
    "Yoshida, Yamada, Sasaki, Yamaguchi, Matsumoto, Inoue, Kimura, Hayashi, Shimizu, Mori"
)
KR_GIVEN = _w(
    "Min-jun, Seo-yeon, Ji-ho, Ha-eun, Do-yun, Ji-woo, Seo-jun, Su-bin, Hyun-woo, Ji-min, "
    "Eun-ji, Young-ho, Sung-min, Hye-jin"
)
KR_FAMILY = _w("Kim, Lee, Park, Choi, Jung, Kang, Cho, Yoon, Jang, Lim, Han, Oh, Seo, Shin, Kwon")
IN_GIVEN = _w("Priya, Rahul, Arjun, Ananya, Vikram, Deepa, Sanjay, Kavya, Rohan, Neha, Amit, Pooja")
IN_FAMILY = _w("Patel, Sharma, Gupta, Singh, Kumar, Reddy, Iyer, Nair, Mehta, Shah, Rao, Joshi")
RU_GIVEN = _w(
    "Александр, Дмитрий, Сергей, Андрей, Алексей, Иван, Михаил, Николай, Елена, Ольга, "
    "Наталья, Юлия, Мария, Анна, Татьяна, Екатерина"
)
RU_FAMILY_M = _w(
    "Иванов, Смирнов, Кузнецов, Попов, Васильев, Петров, Соколов, Михайлов, Новиков, "
    "Фёдоров, Морозов, Волков, Алексеев, Лебедев, Семёнов, Егоров, Павлов, Козлов"
)
RU_FEMALE = {"Елена", "Ольга", "Наталья", "Юлия", "Мария", "Анна", "Татьяна", "Екатерина"}

# Cyrillic to Latin: the everyday spelling most people pick for an email, and the ICAO passport
# spelling some IT departments copy.
_CYR_COMMON = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip
_CYR_ICAO = _CYR_COMMON | {"й": "i", "ю": "iu", "я": "ia", "х": "kh", "ц": "ts", "ъ": "ie"}

ROLES = _w(
    "info, contact, hello, sales, press, jobs, support, office, marketing, careers, kontakt, "
    "bonjour, admin, rh, presse, ventas, vertrieb, team"
)
PARTICLES = {"de", "du", "des", "la", "le", "van", "von", "der", "den", "da", "dos", "del", "d'"}

# ----------------------------------------------------------------------------- cultures

# Pattern shares per culture (assumptions from commonly published breakdowns, not measured).
P = {
    "fl": "{first}.{last}",
    "fL": "{f}{last}",
    "F": "{first}",
    "FL": "{first}{last}",
    "f.l": "{f}.{last}",
    "Fl": "{first}{l}",
    "L": "{last}",
    "F_L": "{first}_{last}",
    "F-L": "{first}-{last}",
    "Lf": "{last}{f}",
    "l.f": "{last}.{first}",
    "LF": "{last}{first}",
}


@dataclass
class Culture:
    tlds: list[str]
    given: list[str]
    family: list[str]
    patterns: dict[str, float]
    providers: dict[str, float]
    mix: float = 0.0  # share of staff drawn from other pools (international teams)


CULTURES: dict[str, Culture] = {
    "us": Culture(
        ["com", "io", "co"],
        EN_GIVEN,
        EN_FAMILY,
        {
            "fl": 34,
            "fL": 24,
            "F": 14,
            "FL": 7,
            "f.l": 5,
            "Fl": 5,
            "L": 3,
            "F_L": 3,
            "Lf": 2,
            "l.f": 1,
            "F-L": 2,
        },
        {"google": 40, "microsoft": 40, "gateway": 11, "other": 9},
        mix=0.3,
    ),
    "uk": Culture(
        ["co.uk", "uk"],
        EN_GIVEN,
        EN_FAMILY,
        {
            "fl": 50,
            "fL": 12,
            "F": 10,
            "f.l": 10,
            "FL": 6,
            "Fl": 3,
            "L": 3,
            "F_L": 3,
            "l.f": 1,
            "F-L": 2,
        },
        {"microsoft": 45, "google": 30, "gateway": 15, "other": 10},
        mix=0.25,
    ),
    "fr": Culture(
        ["fr"],
        FR_GIVEN,
        FR_FAMILY,
        {"fl": 62, "f.l": 10, "fL": 10, "F": 8, "FL": 4, "L": 3, "F-L": 2, "F_L": 1},
        {"microsoft": 30, "google": 25, "ovh": 22, "gandi": 6, "ionos": 5, "other": 12},
        mix=0.1,
    ),
    "de": Culture(
        ["de", "at", "ch"],
        DE_GIVEN,
        DE_FAMILY,
        {"fl": 55, "f.l": 15, "fL": 8, "L": 7, "F": 6, "FL": 4, "F_L": 3, "F-L": 2},
        {"microsoft": 35, "ionos": 20, "google": 15, "other": 22, "gateway": 8},
        mix=0.1,
    ),
    "es": Culture(
        ["es", "com.mx", "com.ar", "cl"],
        ES_GIVEN,
        ES_FAMILY,
        {"fl": 45, "fL": 18, "F": 12, "f.l": 8, "FL": 8, "Fl": 4, "L": 3, "F_L": 2},
        {"microsoft": 38, "google": 35, "zoho": 7, "other": 20},
        mix=0.05,
    ),
    "nl": Culture(
        ["nl", "be"],
        NL_GIVEN,
        NL_FAMILY,
        {"fl": 40, "f.l": 22, "fL": 12, "F": 12, "FL": 6, "L": 4, "F_L": 2, "F-L": 2},
        {"microsoft": 45, "google": 25, "other": 20, "gateway": 10},
        mix=0.15,
    ),
    "nordic": Culture(
        ["se", "no", "dk", "fi"],
        NO_GIVEN,
        NO_FAMILY,
        {"fl": 60, "F": 10, "fL": 8, "f.l": 6, "Fl": 5, "FL": 5, "L": 4, "F_L": 2},
        {"microsoft": 50, "google": 25, "proton": 5, "other": 20},
        mix=0.1,
    ),
    "arabic": Culture(
        ["ae", "sa", "ma", "com.eg"],
        AR_GIVEN,
        AR_FAMILY,
        {"fl": 48, "f.l": 15, "fL": 15, "F": 12, "FL": 5, "L": 3, "F_L": 2},
        {"microsoft": 50, "google": 30, "zoho": 8, "other": 12},
        mix=0.15,
    ),
    "cn": Culture(
        ["cn", "com.cn", "sg", "com.hk"],
        CN_GIVEN,
        CN_FAMILY,
        {"fl": 30, "LF": 20, "fL": 15, "l.f": 15, "F": 10, "FL": 5, "L": 5},
        {"microsoft": 35, "google": 15, "zoho": 10, "other": 40},
        mix=0.05,
    ),
    "jp": Culture(
        ["jp", "co.jp"],
        JP_GIVEN,
        JP_FAMILY,
        {"fl": 30, "l.f": 20, "f.l": 15, "fL": 10, "L": 10, "Lf": 5, "F": 5, "LF": 5},
        {"microsoft": 45, "google": 30, "other": 25},
    ),
    "kr": Culture(
        ["kr", "co.kr"],
        KR_GIVEN,
        KR_FAMILY,
        {"fl": 40, "fL": 15, "l.f": 10, "F": 15, "FL": 10, "f.l": 10},
        {"microsoft": 40, "google": 35, "other": 25},
    ),
    "ru": Culture(
        ["ru", "by", "kz"],
        RU_GIVEN,
        RU_FAMILY_M,
        {"fl": 45, "f.l": 20, "fL": 15, "F": 10, "L": 5, "FL": 5},
        {"other": 55, "google": 15, "microsoft": 20, "zoho": 10},
    ),
}
CULTURE_WEIGHTS = {
    "us": 20, "uk": 8, "fr": 14, "de": 14, "es": 9, "nl": 6, "nordic": 6, "arabic": 7,
    "cn": 6, "jp": 4, "kr": 3, "ru": 3,
}  # fmt: skip
MIX_POOLS = [
    (IN_GIVEN, IN_FAMILY, "in"),
    (CN_GIVEN, CN_FAMILY, "cn-west"),
    (ES_GIVEN[:20], ES_FAMILY, "es-west"),
    (AR_GIVEN, AR_FAMILY, "arabic"),
    (FR_GIVEN, FR_FAMILY, "fr"),
    (DE_GIVEN, DE_FAMILY, "de"),
]


# Mail servers: MX hosts and how they answer RCPT TO (assumed shares, see docs/email-quality.md).
def mx_hosts(provider: str, d: str, rng: random.Random) -> list[str]:
    n = rng.randint(1000, 9999)
    return {
        "google": ["aspmx.l.google.com", "alt1.aspmx.l.google.com"],
        "microsoft": [d.replace(".", "-") + ".mail.protection.outlook.com"],
        "gateway": rng.choice(
            [
                ["us-smtp-inbound-1.mimecast.com", "us-smtp-inbound-2.mimecast.com"],
                [f"mx0a-00{n}.pphosted.com", f"mx0b-00{n}.pphosted.com"],
                [f"d{n}a.ess.barracudanetworks.com", f"d{n}b.ess.barracudanetworks.com"],
                [f"mx1.hc{n}-{n % 97}.iphmx.com", f"mx2.hc{n}-{n % 97}.iphmx.com"],
            ]
        ),
        "zoho": ["mx.zoho.eu", "mx2.zoho.eu"],
        "proton": ["mail.protonmail.ch", "mailsec.protonmail.ch"],
        "ovh": ["mx1.mail.ovh.net", "mx2.mail.ovh.net"],
        "ionos": ["mx00.ionos.de", "mx01.ionos.de"],
        "gandi": ["spool.mail.gandi.net", "fb.mail.gandi.net"],
        "other": [f"mail.{d}"],
    }[provider]


SMTP_MODES = {
    "google": {"reject_unknown": 80, "accept_all": 14, "throttle": 6},
    "microsoft": {"reject_unknown": 60, "accept_all": 24, "block": 8, "throttle": 8},
    "gateway": {"accept_all": 60, "reject_unknown": 30, "block": 10},
    "zoho": {"reject_unknown": 85, "accept_all": 15},
    "proton": {"reject_unknown": 85, "accept_all": 15},
    "gandi": {"reject_unknown": 80, "accept_all": 20},
    "ovh": {"reject_unknown": 60, "accept_all": 40},
    "ionos": {"reject_unknown": 55, "accept_all": 45},
    "other": {"reject_unknown": 60, "accept_all": 22, "greylist": 18},
}
ALIAS_RATE = {"google": 0.15, "microsoft": 0.2}  # extra addresses per person (proxy addresses)

COMPANY_WORDS = _w(
    "nordwind, kestrel, atelier, brucke, lumen, vireo, solano, quercus, halden, marlin, "
    "orchid, basalt, cobalt, ember, fjord, granite, harbor, iris, juniper, kiln, larch, "
    "meridian, nimbus, ostrava, pinnacle, quarry, rowan, sable, tundra, umber, vantage, "
    "willow, yarrow, zephyr, alder, birch, cedar, delta, ebony, falcon, garnet, heron"
)
COMPANY_SUFFIX = _w("data, systems, labs, technik, conseil, studio, logistics, group, works, bio")

# ----------------------------------------------------------------------------- people


@dataclass
class Human:
    given: list[str]
    family: list[str]
    culture: str  # naming culture, may differ from the company's
    middle: list[str] = field(default_factory=list)
    father: str = ""  # Gulf patronymic ("bin Saeed"), shown on some sites, never in emails
    display_order: str = "given_first"  # or "family_first" (East Asian convention)


@dataclass
class Rules:
    """A domain's house rules for turning names into addresses."""

    pattern: str
    second: str | None
    second_share: float
    expand_umlauts: bool
    hyphen_given: str  # keep | drop | first
    multi_given: str  # first | joined
    particle: str  # keep | drop
    hyphen_family: str  # keep | drop
    compound: str  # first | joined | last
    cyrillic: str  # common | icao
    irregular: float


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def translit(text: str, rules: Rules) -> str:
    """Lowercase ASCII spelling of a name part under the domain's rules."""
    text = text.lower()
    if any("Ѐ" <= c <= "ӿ" for c in text):
        table = _CYR_ICAO if rules.cyrillic == "icao" else _CYR_COMMON
        if rules.cyrillic == "common":
            text = re.sub(r"ий\b", "y", text).replace("кс", "x")
        return "".join(table.get(c, c) for c in text)
    if rules.expand_umlauts:
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ø", "oe"), ("å", "aa")):
            text = text.replace(a, b)
    for a, b in (("ß", "ss"), ("æ", "ae"), ("ø", "o"), ("œ", "oe"), ("ł", "l"), ("đ", "d")):
        text = text.replace(a, b)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9-]", "", text)


def _hyphen(part: str, how: str) -> str:
    if "-" not in part:
        return part
    return {"keep": part, "drop": part.replace("-", ""), "first": part.split("-")[0]}[how]


def email_names(h: Human, rules: Rules) -> tuple[str, str]:
    """(first, last) as this domain's IT department would spell them."""
    given = [translit(g, rules) for g in h.given]
    first = given[0] if (len(given) == 1 or rules.multi_given == "first") else "".join(given)
    first = _hyphen(first, rules.hyphen_given)
    fam = list(h.family)
    lead: list[str] = []
    while len(fam) > 1 and (fam[0].lower() in PARTICLES or fam[0].lower() in {"al", "el"}):
        lead.append(fam.pop(0))
    if len(fam) == 2 and h.culture.startswith("es") and h.culture != "es-west":
        fam = {"first": fam[:1], "joined": fam, "last": fam[1:]}[rules.compound]
    core = [translit(t, rules) for t in fam]
    hyph = [t for t in core if "-" in t]
    if hyph and re.match(r"^(al|el)-", hyph[0]) and rules.particle == "drop":
        core = [re.sub(r"^(al|el)-", "", t) for t in core]
    core = [_hyphen(t, rules.hyphen_family) for t in core]
    pre = [translit(t, rules).replace("'", "") for t in lead] if rules.particle == "keep" else []
    last = "".join(pre + core).replace("'", "")
    return first.replace("'", ""), last


def render(pattern: str, first: str, last: str) -> str:
    return pattern.format(first=first, last=last, f=first[:1], l=last[:1])


def display_name(h: Human, rng: random.Random) -> str:
    """How the person's name appears on the company site."""
    given, family = " ".join(h.given), " ".join(h.family)
    if h.culture == "ru":
        return f"{given} {family}"
    if h.display_order == "family_first":
        fam = family.upper() if rng.random() < 0.35 else family
        return f"{fam} {given}"
    if h.culture == "es" and len(h.family) == 2 and rng.random() < 0.3:
        family = h.family[0]  # only the first surname on the page
    middle = ""
    if h.middle and rng.random() < 0.3:
        middle = " " + (h.middle[0] if rng.random() < 0.5 else h.middle[0][0] + ".")
    if h.father and rng.random() < 0.4:
        middle = f" bin {h.father}"
    if h.culture in {"fr", "de"} and rng.random() < 0.12:
        family = family.upper()
    name = f"{given}{middle} {family}"
    roll = rng.random()
    if roll < 0.05:
        name = "Dr. " + name
    elif roll < 0.08:
        name += ", PhD"
    return name


def make_human(rng: random.Random, culture: str, c: Culture, family_first: bool) -> Human:
    pools = (c.given, c.family, culture)
    if c.mix and rng.random() < c.mix:
        pools = rng.choice(MIX_POOLS)
    given_pool, family_pool, naming = pools
    given = rng.choice(given_pool)
    if naming == "ru":
        fam = rng.choice(family_pool)
        if given in RU_FEMALE:
            fam += "а"
        return Human([given], [fam], "ru")
    if naming == "es":
        family = [rng.choice(family_pool), rng.choice(family_pool)]
        if rng.random() < 0.08:
            family = rng.choice(family_pool).split()
        return Human(given.split(), family, "es")
    family = rng.choice(family_pool).split()
    middle = [rng.choice(EN_MIDDLE)] if culture in {"us", "uk"} and rng.random() < 0.3 else []
    h = Human(given.split(), family, naming, middle=middle)
    if naming == "arabic" and culture == "arabic" and rng.random() < 0.3:
        h.father = rng.choice(AR_FATHER)
    if naming in {"cn", "jp", "kr"} and family_first:
        h.display_order = "family_first"
    return h


# ----------------------------------------------------------------------------- domains


def make_rules(rng: random.Random, culture: str, c: Culture) -> Rules:
    pattern = P[_pick(rng, c.patterns)]
    second = None
    share = 0.0
    if rng.random() < 0.18:  # mixed formats: a legacy or founders' pattern next to the main one
        second = P[_pick(rng, {k: v for k, v in c.patterns.items() if P[k] != pattern})]
        share = rng.uniform(0.15, 0.4)
    expand = {"de": 0.8, "nordic": 0.3}.get(culture, 0.15)
    return Rules(
        pattern=pattern,
        second=second,
        second_share=share,
        expand_umlauts=rng.random() < expand,
        hyphen_given=_pick(rng, {"keep": 50, "drop": 43, "first": 7}),
        multi_given=_pick(rng, {"first": 60, "joined": 40}),
        particle=_pick(rng, {"keep": 65, "drop": 35}),
        hyphen_family=_pick(rng, {"keep": 35, "drop": 65}),
        compound=_pick(rng, {"first": 70, "joined": 20, "last": 10}),
        cyrillic=_pick(rng, {"common": 60, "icao": 40}),
        irregular=min(0.2, rng.betavariate(1.6, 20)),
    )


def irregular_local(rng: random.Random, h: Human, rules: Rules, first: str, last: str) -> str:
    """An address no pattern predicts: nickname, middle name, another format, legacy handle."""
    kind = rng.choices(["nick", "middle", "format", "handle"], [25, 15, 30, 30])[0]
    if kind == "nick" and h.given[0] in NICKNAMES:
        return render(rules.pattern, NICKNAMES[h.given[0]].lower(), last)
    if kind == "middle" and h.middle:
        m = translit(h.middle[0], rules)
        return rng.choice([f"{first}.{m[0]}.{last}", f"{first[0]}{m[0]}{last}"])
    if kind in {"format", "middle", "nick"}:
        return render(rng.choice([p for p in P.values() if p != rules.pattern]), first, last)
    return rng.choice(
        [
            f"{first[:2]}{last[:3]}",
            f"{first[0]}{last[0]}{rng.randint(1, 99)}",
            f"{first}{rng.randint(1, 99)}",
            f"{first}.{translit(rng.choice(EN_FAMILY + FR_FAMILY), rules)}",  # previous surname
        ]
    )


def make_domain(rng: random.Random, culture: str, used: set[str]) -> dict:
    c = CULTURES[culture]
    while True:
        name = f"{rng.choice(COMPANY_WORDS)}-{rng.choice(COMPANY_SUFFIX)}"
        domain = f"{name}.{rng.choice(c.tlds)}.example"
        if domain not in used:
            used.add(domain)
            break
    rules = make_rules(rng, culture, c)
    family_first = rng.random() < {"cn": 0.65, "jp": 0.4, "kr": 0.5}.get(culture, 0.0)
    provider = _pick(rng, c.providers)
    mx = mx_hosts(provider, domain.removesuffix(".example"), rng)
    if provider == "other" and rng.random() < 0.05:
        mx = []  # no MX record: mail goes to the A record
    smtp = _pick(rng, SMTP_MODES[provider])

    staff_n = int(rng.choice([6, 9, 14, 20, 30, 45, 70, 110, 160]) * rng.uniform(0.8, 1.2))
    taken: dict[str, int] = {}
    staff: list[tuple[Human, str]] = []
    for _ in range(staff_n):
        h = make_human(rng, culture, c, family_first)
        first, last = email_names(h, rules)
        if not first or not last:
            continue
        pattern = rules.pattern
        if rules.second and rng.random() < rules.second_share:
            pattern = rules.second
        local = render(pattern, first, last)
        if rng.random() < rules.irregular:
            local = irregular_local(rng, h, rules, first, last)
        local = re.sub(r"[^a-z0-9._-]", "", local).strip(".-_")
        if local in taken:  # namesake: IT adds a digit
            taken[local] += 1
            local = f"{local}{taken[local]}"
        taken[local] = 1
        staff.append((h, local))

    n_site = min(len(staff), rng.choice([1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10]))
    site = staff[:n_site]
    published = [s for s in site if rng.random() < 0.85] or site[:1]
    nameless = [loc for _, loc in staff[n_site : n_site + rng.choice([0, 0, 0, 1, 1, 2])]]
    roles = rng.sample(ROLES, rng.choice([1, 1, 2, 3]))
    site_names = [display_name(h, rng) for h, _ in site]
    names_by_local = {loc: nm for (_, loc), nm in zip(site, site_names, strict=True)}

    aliases: dict[str, list[str]] = {}
    alias_rate = ALIAS_RATE.get(provider, 0.05)
    for h, loc in staff:
        if rng.random() < alias_rate:
            first, last = email_names(h, rules)
            alt = render(rng.choice(["{f}{last}", "{first}", "{first}.{last}"]), first, last)
            if alt != loc and alt not in taken:
                taken[alt] = 1
                aliases[loc] = [alt]
    for _ in range(int(staff_n * 0.05)):  # mailboxes of former staff, kept as shared mailboxes
        first, last = email_names(make_human(rng, culture, c, family_first), rules)
        loc = render(rules.pattern, first, last)
        if first and last and loc not in taken:
            taken[loc] = 1

    # Keep only mailboxes a guess for a gold person could hit: any guess contains part of the
    # person's name, so an address sharing no name token with them can never collide.
    tokens: set[str] = set()
    for h, _ in published:
        for variant in (
            rules,
            Rules(
                **{
                    **rules.__dict__,
                    "expand_umlauts": not rules.expand_umlauts,
                    "cyrillic": "icao" if rules.cyrillic == "common" else "common",
                }
            ),
        ):
            for part in [*h.given, *h.family, *h.middle]:
                tokens.update(t for t in re.split(r"[-' ]", translit(part, variant)) if len(t) >= 2)
    relevant = {loc for loc in taken if any(t in loc for t in tokens)}
    mailboxes = sorted(relevant | set(roles) | {"postmaster", "info"})
    gold = [
        {"full_name": names_by_local[loc], "true_address": f"{loc}@{domain}"}
        for _, loc in published
    ]
    entry = {
        "segment": culture,
        "provider": provider,
        "mx": mx,
        "smtp": smtp,
        "site_names": site_names,
        "site_emails": [f"{x}@{domain}" for x in [loc for _, loc in published] + nameless + roles],
        "mailboxes": mailboxes,
        "aliases": {
            f"{loc}@{domain}": [f"{a}@{domain}" for a in aliases[loc]]
            for _, loc in published
            if loc in aliases
        },
        "rules": {
            "pattern": rules.pattern,
            "second": rules.second,
            "irregular": round(rules.irregular, 3),
        },
    }
    return {"domain": domain, "entry": entry, "gold": gold}


def generate(seed: int, n_domains: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    used: set[str] = set()
    rows: list[dict] = []
    world: dict = {
        "version": 1,
        "note": "Synthetic: fake people and companies under .example, see tools/make_email_gold.py",
        "seed": seed,
        "domains": {},
    }
    cultures = list(CULTURE_WEIGHTS)
    for _ in range(n_domains):
        culture = rng.choices(cultures, [CULTURE_WEIGHTS[k] for k in cultures])[0]
        made = make_domain(rng, culture, used)
        world["domains"][made["domain"]] = made["entry"]
        rows.extend({"domain": made["domain"], **g} for g in made["gold"])
    return rows, world


def dump_world(world: dict) -> str:
    """JSON with one domain per line: small, diff-friendly fixtures."""
    head = {k: v for k, v in world.items() if k != "domains"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "domains": {']
    items = list(world["domains"].items())
    for i, (domain, entry) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"{json.dumps(domain)}: {json.dumps(entry, ensure_ascii=False)}{comma}")
    lines.append("}}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--domains", type=int, default=90)
    ap.add_argument("--out", type=Path, required=True, help="path prefix, e.g. .../dev")
    args = ap.parse_args()
    rows, world = generate(args.seed, args.domains)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["full_name", "domain", "true_address"])
        writer.writeheader()
        writer.writerows(
            {"full_name": r["full_name"], "domain": r["domain"], "true_address": r["true_address"]}
            for r in rows
        )
    path = args.out.with_suffix(".world.json")
    path.write_text(dump_world(world), encoding="utf-8")
    print(f"{len(rows)} gold rows over {len(world['domains'])} domains -> {args.out}.csv")


if __name__ == "__main__":
    main()
