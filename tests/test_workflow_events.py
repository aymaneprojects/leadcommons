"""Core events reach workflows.emit: person_found, email_found, added_to_list."""

from __future__ import annotations

from pathlib import Path

import pytest

from leadcommons import workflows
from leadcommons.config import Settings
from leadcommons.store import Store
from leadcommons.web.jobs import JobRunner
from tests.fakes import FakeSearch, sample_companies, sample_email, sample_person


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(workflows, "emit", lambda event, **p: seen.append((event, p)))
    return seen


def _runner(tmp_path: Path) -> tuple[JobRunner, Store]:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "p", database_url=f"sqlite:///{tmp_path}/t.db"
    )
    settings.sync_jobs = True
    store = Store(settings.database_url)
    return JobRunner(store, FakeSearch(), settings), store


def test_find_people_and_email_emit(tmp_path: Path, events, monkeypatch) -> None:
    from leadcommons import enrich

    company = sample_companies()[0]
    person = sample_person(company)
    monkeypatch.setattr(enrich, "find_people", lambda domain: [person])
    monkeypatch.setattr(
        enrich, "find_email", lambda p, d, smtp_probe=False: sample_email(f"claire@{d}")
    )
    runner, _ = _runner(tmp_path)
    runner.submit("find_people", {"domain": company.domain, "company_id": company.id}, "x")
    runner.submit("find_email", {"person_id": person.id}, "y")
    kinds = [e for e, _ in events]
    assert kinds == ["person_found", "email_found"]
    assert events[0][1] == {
        "person_id": person.id,
        "company_id": company.id,
        "domain": company.domain,
    }
    assert (
        events[1][1]["address"] == f"claire@{company.domain}" and events[1][1]["status"] == "valid"
    )
    runner.shutdown()


def test_add_to_list_emits_only_new_members(tmp_path: Path, events) -> None:
    store = Store(f"sqlite:///{tmp_path}/t.db")
    lid = store.create_list("L", "people")
    store.add_to_list(lid, ["a", "b"])
    store.add_to_list(lid, ["b", "c"])
    store.add_to_list(lid, ["c"])  # nothing new: no event
    assert events == [
        ("added_to_list", {"list_id": lid, "record_ids": ["a", "b"], "kind": "people"}),
        ("added_to_list", {"list_id": lid, "record_ids": ["c"], "kind": "people"}),
    ]
