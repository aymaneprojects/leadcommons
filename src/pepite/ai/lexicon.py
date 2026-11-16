"""Vocabulary for the rules-based query parser (EN + FR, some ES/DE).

All phrases are written in plain lowercase; the parser accent-folds and tokenises both the
query and these phrases the same way, so "Allemagne", "allemagne" and "ALLEMAGNE" all match.
Ambiguous words are deliberately left out (Jordan, Georgia, Chad, "us" as a lowercase word, ...).
"""

from __future__ import annotations

# ISO alpha-2 -> comma separated names, demonyms (incl. FR feminine/plural), local names.
_COUNTRIES = """
FR: france, french, francais, francaise, francaises, francia, frankreich, franzosisch
GB: united kingdom, great britain, britain, british, england, angleterre, \
royaume uni, britannique, britanniques, reino unido, grossbritannien, vereinigtes konigreich, \
scotland, ecosse
US: united states, united states of america, america, american, americans, americain, \
americaine, americains, americaines, etats unis, estados unidos, vereinigte staaten
DE: germany, german, germans, allemagne, allemand, allemande, allemands, allemandes, \
alemania, alemana, aleman, deutschland, deutsch, deutsche
ES: spain, spanish, espagne, espagnol, espagnole, espagnols, espagnoles, espana, espanol, \
espanola, espanolas, espanoles, spanien
IT: italy, italian, italie, italien, italienne, italiens, italiennes, italia, italiano, \
italiana, italienisch
PT: portugal, portuguese, portugais, portugaise, portugais, portuguesa, portugues
NL: netherlands, dutch, holland, pays bas, neerlandais, neerlandaise, hollandais, \
hollandaise, paises bajos, holandes, niederlande, nederland
BE: belgium, belgian, belgique, belge, belges, belgica, belga, belgien
CH: switzerland, swiss, suisse, suisses, suiza, schweiz, schweizer
AT: austria, austrian, autriche, autrichien, autrichienne, osterreich
IE: ireland, irish, irlande, irlandais, irlandaise, irlanda, irland
LU: luxembourg, luxembourgeois, luxembourgeoise
SE: sweden, swedish, suede, suedois, suedoise, suecia, schweden
DK: denmark, danish, danemark, danois, danoise, dinamarca, danemark
NO: norway, norwegian, norvege, norvegien, norvegienne, noruega, norwegen
FI: finland, finnish, finlande, finlandais, finlandaise, finlandia, finnland
PL: poland, polish, pologne, polonais, polonaise, polonia, polen
CZ: czech republic, czechia, czech, tchequie, republique tcheque, tcheque, tchecoslovaquie
GR: greece, greek, grece, grec, grecque, grecs, grecia, griechenland
HU: hungary, hungarian, hongrie, hongrois, hongroise, hungria, ungarn
RO: romania, romanian, roumanie, roumain, roumaine, rumania, rumanien
BG: bulgaria, bulgarian, bulgarie, bulgare
HR: croatia, croatian, croatie, croate
RS: serbia, serbian, serbie, serbe
UA: ukraine, ukrainian, ukrainien, ukrainienne
TR: turkey, turkish, turquie, turc, turque, turquia, turkei
RU: russia, russian, russie, russe, rusia
CA: canada, canadian, canadien, canadienne, canadiens, canadiennes
MX: mexico, mexican, mexique, mexicain, mexicaine, mexiko
BR: brazil, brazilian, bresil, bresilien, bresilienne, brasil, brasilien
AR: argentina, argentinian, argentine, argentin, argentina
CL: chile, chilean, chili, chilien, chilienne
CO: colombia, colombian, colombie, colombien, colombienne
PE: peru, peruvian, perou, peruvien, peruvienne
AU: australia, australian, australie, australien, australienne, avustralya
NZ: new zealand, nouvelle zelande, neozelandais, neo zelandais, nueva zelanda
JP: japan, japanese, japon, japonais, japonaise, japonesa, japan
CN: china, chinese, chine, chinois, chinoise, chinas
IN: india, indian, inde, indien, indienne, indias
SG: singapore, singapour, singaporean, singapourien
KR: south korea, korea, korean, coree du sud, coree, coreen, coreenne
IL: israel, israeli, israelien, israelienne
AE: united arab emirates, uae, emirates, emirats arabes unis, emirats, dubai, abu dhabi
SA: saudi arabia, saudi, arabie saoudite, saoudien
EG: egypt, egyptian, egypte, egyptien, egyptienne, egipto
MA: morocco, moroccan, maroc, marocain, marocaine, marocains, marocaines, marruecos, marokko
TN: tunisia, tunisian, tunisie, tunisien, tunisienne, tunez
DZ: algeria, algerian, algerie, algerien, algerienne, argelia
SN: senegal, senegalese, senegalais, senegalaise
CI: ivory coast, cote d ivoire, ivorian, ivoirien, ivoirienne
NG: nigeria, nigerian, nigerien, nigeriane
ZA: south africa, south african, afrique du sud, sud africain, sudafrica
KE: kenya, kenyan, kenyane
CM: cameroon, cameroun, cameroonian, camerounais, camerounaise
IS: iceland, icelandic, islande, islandais, islandia
EE: estonia, estonian, estonie, estonien
LV: latvia, latvian, lettonie, letton
LT: lithuania, lithuanian, lituanie, lituanien
SK: slovakia, slovak, slovaquie, slovaque
SI: slovenia, slovenian, slovenie, slovene
MT: malta, maltese, malte, maltais
CY: cyprus, cypriot, chypre, chypriote
VN: vietnam, vietnamese, vietnamien
TH: thailand, thai, thailande, thailandais
ID: indonesia, indonesian, indonesie, indonesien
MY: malaysia, malaysian, malaisie, malaisien
PH: philippines, filipino, philippin
PK: pakistan, pakistani, pakistanais
BD: bangladesh, bangladeshi
"""

# Uppercase-only codes accepted as-is (case matters: "IT" is information technology, "US" is
# a country but "us" is a pronoun). UK is mapped to GB.
UPPER_CODES: dict[str, str] = {
    "FR": "FR", "UK": "GB", "GB": "GB", "US": "US", "USA": "US", "NL": "NL", "CH": "CH",
    "PL": "PL", "PT": "PT", "SE": "SE", "DK": "DK", "FI": "FI", "IE": "IE", "LU": "LU",
    "CZ": "CZ", "SG": "SG", "AU": "AU", "BR": "BR", "MX": "MX", "JP": "JP", "CN": "CN",
    "AE": "AE", "TN": "TN", "SN": "SN", "UAE": "AE",
}  # fmt: skip

# Major cities -> country (the filters have no city field, so the country is used).
_CITIES = """
FR: paris, lyon, marseille, toulouse, bordeaux, lille, nantes, strasbourg, montpellier, \
rennes, nice cote d azur, grenoble, sophia antipolis
GB: london, londres, manchester, birmingham, leeds, edinburgh, glasgow, bristol
DE: berlin, munich, munchen, hamburg, frankfurt, cologne, koln, stuttgart, dusseldorf
ES: madrid, barcelona, barcelone, sevilla, seville
NL: amsterdam, rotterdam
BE: brussels, bruxelles, antwerp, anvers
CH: zurich, geneva, geneve
AT: vienna, vienne, wien
IT: milan, milano, rome, roma
PT: lisbon, lisbonne, lisboa
IE: dublin
SE: stockholm
DK: copenhagen, copenhague
FI: helsinki
PL: warsaw, varsovie
CZ: prague
MA: casablanca, rabat, marrakech
TN: tunis
DZ: alger
SN: dakar
CI: abidjan
US: new york, san francisco, los angeles, chicago, boston, seattle, silicon valley
CA: toronto, montreal
AU: sydney, melbourne
JP: tokyo
"""

# Company-size vocabulary -> SIZE_RANGES buckets (documented choices):
#   micro / startup / small      -> 1-10, 11-50
#   SME ("PME", "SMB")            -> 11-50, 51-200, 201-500   (EU SME definition: < 250 staff)
#   mid-size ("ETI", mid-market)  -> 201-500, 501-1000, 1001-5000
#   large / enterprise / groups   -> 1001-5000, 5001-10000, 10001+
_S_SMALL = ("1-10", "11-50")
_S_SME = ("11-50", "51-200", "201-500")
_S_MID = ("201-500", "501-1000", "1001-5000")
_S_LARGE = ("1001-5000", "5001-10000", "10001+")

SIZE_WORDS: dict[str, tuple[str, ...]] = {}
for _phrases, _sizes in (
    (
        "startup, startups, start up, start ups, small, small business, small businesses, "
        "small companies, tiny, micro, micro enterprise, micro entreprises, tpe, "
        "petite entreprise, petites entreprises, petite structure, petites structures, "
        "kleine unternehmen, pequena empresa, pequenas empresas",
        _S_SMALL,
    ),
    (
        "pme, smb, smbs, sme, smes, small and medium, small and medium sized, medium sized, "
        "petites et moyennes entreprises, mittelstand, pyme, pymes",
        _S_SME,
    ),
    (
        "eti, mid size, midsize, mid sized, midsized, mid market, midmarket, mid cap, "
        "entreprise de taille intermediaire, entreprises de taille intermediaire, "
        "taille intermediaire",
        _S_MID,
    ),
    (
        "large, large companies, large enterprises, big companies, enterprise, enterprises, "
        "corporate, corporates, large corporations, grande entreprise, grandes entreprises, "
        "grand groupe, grands groupes, grosse entreprise, grosses entreprises, grandes empresas, "
        "grossunternehmen, konzern, konzerne",
        _S_LARGE,
    ),
):
    for _p in _phrases.split(","):
        SIZE_WORDS[_p.strip()] = _sizes

# ISIC Rev.4 section letters. Ambiguous generic words ("industry", "services") are omitted.
_INDUSTRIES = """
K: fintech, fintechs, finance, financial, financial services, banking, bank, banks, banque, \
banques, bancaire, bancaires, insurance, insurances, assurance, assurances, assureur, \
assureurs, insurtech, investment, asset management, gestion d actifs, private equity, \
venture capital, capital risque, seguros, banca, versicherung, versicherungen, banken
J: software, saas, logiciel, logiciels, software publisher, editeur de logiciels, \
editeurs de logiciels, it services, informatique, ssii, esn, tech, technology, technologie, \
technologies, telecom, telecoms, telecommunications, telecommunication, internet, cloud, \
cybersecurity, cybersecurite, media, medias, publishing, edition, video games, video game, \
jeux video, numerique, edtech, healthtech
Q: hospital, hospitals, hopital, hopitaux, clinic, clinics, clinique, cliniques, health, \
healthcare, health care, sante, medical, medicale, pharma, pharmaceutical, pharmaceuticals, \
pharmaceutique, biotech, biotechnology, krankenhaus, krankenhauser, hospitales
I: restaurant, restaurants, restauration, hotel, hotels, hotellerie, hospitality, catering, \
traiteur, traiteurs, gastronomie, gastronomy, hoteles, gastronomie
G: retail, retailer, retailers, e commerce, ecommerce, commerce de detail, wholesale, \
wholesaler, wholesalers, grossiste, grossistes, distribution, supermarket, supermarkets, \
supermarche, supermarches, magasin, magasins, comercio minorista, einzelhandel
C: manufacturing, manufacturer, manufacturers, industrie, industriel, industriels, \
industrielle, industrielles, fabricant, fabricants, fabrication, usine, usines, factory, \
factories, fabrica, fabricas, produktion, hersteller, automotive, automobile
F: construction, btp, batiment, batiments, building contractors, travaux publics, genie civil, \
construccion, bau, baugewerbe
D: energy, energie, energies, energia, electricity, electricite, utilities, utility, \
renewable, renewables, renouvelable, renouvelables, solar, solaire, photovoltaique, \
energiewirtschaft
H: logistics, logistique, transport, transports, transportation, shipping, freight, \
airline, airlines, compagnie aerienne, compagnies aeriennes, logistica, spedition
L: real estate, immobilier, immobiliere, immobilieres, proptech, realty, inmobiliaria, \
immobilien
M: consulting, consultancy, consultancies, conseil, cabinet de conseil, cabinets de conseil, \
law firm, law firms, cabinet d avocats, cabinets d avocats, juridique, legal services, \
marketing agency, marketing agencies, agence marketing, agences marketing, \
advertising agency, advertising agencies, agence de publicite, agences de publicite, \
agence de communication, agences de communication, publicite, advertising, accounting firm, \
accounting firms, cabinet comptable, cabinets comptables, expertise comptable, \
consultoria, unternehmensberatung, beratung
P: education, educational, school, schools, ecole, ecoles, university, universities, \
universite, universites, enseignement, educacion, bildung, schule, schulen
N: staffing, interim, agence d interim, agences d interim, temp agency, temp agencies, \
travel agency, travel agencies, agence de voyage, agences de voyage
R: sport, sports, museum, museums, musee, musees, entertainment, divertissement, loisirs, \
leisure
A: agriculture, agricultural, farming, farm, farms, agricole, agricoles, agriculteur, \
agriculteurs, viticulture, vignoble, vignobles, elevage, landwirtschaft
B: mining, mines, minier, miniere, oil and gas, oil gas, petrole, petroleum
E: waste, recycling, dechets, recyclage, abfall
O: government, public sector, secteur public, administration publique, gouvernement
"""

# Words that only count as an industry when written in capitals (IT = information technology).
UPPER_INDUSTRY_WORDS: dict[str, str] = {"IT": "J", "ESN": "J", "BTP": "F", "SSII": "J"}


def _parse(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    flat = spec.replace("\\\n", " ")
    for line in flat.strip().splitlines():
        key, _, names = line.partition(":")
        for name in names.split(","):
            name = name.strip()
            if name:
                out.setdefault(name, key.strip())
    return out


COUNTRY_WORDS: dict[str, str] = _parse(_COUNTRIES)
CITY_WORDS: dict[str, str] = _parse(_CITIES)
INDUSTRY_WORDS: dict[str, str] = _parse(_INDUSTRIES)

COUNTRY_NAMES: dict[str, str] = {
    "FR": "France", "GB": "United Kingdom", "US": "United States", "DE": "Germany",
    "ES": "Spain", "IT": "Italy", "PT": "Portugal", "NL": "Netherlands", "BE": "Belgium",
    "CH": "Switzerland", "AT": "Austria", "IE": "Ireland", "LU": "Luxembourg", "SE": "Sweden",
    "DK": "Denmark", "NO": "Norway", "FI": "Finland", "PL": "Poland", "CZ": "Czechia",
    "GR": "Greece", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "HR": "Croatia",
    "RS": "Serbia", "UA": "Ukraine", "TR": "Turkey", "RU": "Russia", "CA": "Canada",
    "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "AU": "Australia", "NZ": "New Zealand", "JP": "Japan", "CN": "China",
    "IN": "India", "SG": "Singapore", "KR": "South Korea", "IL": "Israel",
    "AE": "United Arab Emirates", "SA": "Saudi Arabia", "EG": "Egypt", "MA": "Morocco",
    "TN": "Tunisia", "DZ": "Algeria", "SN": "Senegal", "CI": "Ivory Coast", "NG": "Nigeria",
    "ZA": "South Africa", "KE": "Kenya", "CM": "Cameroon", "IS": "Iceland", "EE": "Estonia",
    "LV": "Latvia", "LT": "Lithuania", "SK": "Slovakia", "SI": "Slovenia", "MT": "Malta",
    "CY": "Cyprus", "VN": "Vietnam", "TH": "Thailand", "ID": "Indonesia", "MY": "Malaysia",
    "PH": "Philippines", "PK": "Pakistan", "BD": "Bangladesh",
}  # fmt: skip

# Words that never become keywords (EN, FR, ES, DE) plus generic "search" vocabulary.
STOP_WORDS: frozenset[str] = frozenset(
    """
a an the and or of in on at to for from with by as is are be been being it its this that these
those who which what where when how all any some every each both only also just very more most
find show list give get me my our your their us we they them i you he she looking look search
searching want need needs please
companies company firms firm businesses business organisations organizations organisation
organization enterprises enterprise startups corporations people person contacts contact leads
lead prospects prospect employees employee staff professionals professional persons
le la les l un une des du de d et ou en au aux dans sur sous par pour avec sans chez qui que
quoi dont ce cet cette ces mes mon ma nos notre vos votre leur leurs je tu il elle nous vous ils
elles trouve trouver trouvez cherche chercher cherchez recherche recherches liste donne donnez
montre montrer veux voudrais besoin entreprises entreprise societe societes boite boites
organisation organisations personnes gens contacts salaries salarie employes employe
collaborateurs between zwischen a ainsi aussi tres plus moins entre
el los las una unos unas y o en con sin por para de del empresas empresa personas busco buscar
der die das ein eine und oder in im mit ohne fur von zu bei unternehmen firmen leute suche
suchen
""".split()
)

NEGATIONS: frozenset[str] = frozenset(
    (
        "not no except excluding exclude without hors sauf sans excluant ni ohne ausser sin excepto"
    ).split()
)

# "founded after 2015" style qualifiers: not expressible as filters -> reported as unparsed.
UNSUPPORTED_MARKERS: frozenset[str] = frozenset(
    """
founded created established fondee fondees creee creees depuis since after before apres avant
revenue turnover ca chiffre funding raised levee growth hiring recruiting
""".split()
)
