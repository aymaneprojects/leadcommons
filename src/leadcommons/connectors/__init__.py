"""Open-data connectors."""

from __future__ import annotations

from leadcommons.connectors.base import USER_AGENT, Connector
from leadcommons.connectors.gleif import GleifConnector
from leadcommons.connectors.wikidata import WikidataConnector

CONNECTORS: dict[str, type] = {"wikidata": WikidataConnector, "gleif": GleifConnector}

__all__ = ["CONNECTORS", "USER_AGENT", "Connector", "GleifConnector", "WikidataConnector"]
