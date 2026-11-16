"""Open-data connectors."""

from __future__ import annotations

from pepite.connectors.base import USER_AGENT, Connector
from pepite.connectors.edgar import EdgarConnector
from pepite.connectors.gleif import GleifConnector
from pepite.connectors.sirene import SireneConnector
from pepite.connectors.wikidata import WikidataConnector

CONNECTORS: dict[str, type] = {
    "wikidata": WikidataConnector,
    "gleif": GleifConnector,
    "sirene": SireneConnector,
    "edgar": EdgarConnector,
}

__all__ = [
    "CONNECTORS",
    "USER_AGENT",
    "Connector",
    "EdgarConnector",
    "GleifConnector",
    "SireneConnector",
    "WikidataConnector",
]
