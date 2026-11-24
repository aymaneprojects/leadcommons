from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from pepite import enrich
from pepite.models import EmailCandidate, EmailStatus
from pepite.web.jobs import JobRunner
from tests.fakes import FakeSearch, FakeStore, sample_companies, sample_person
from tests.test_api import make_settings


@pytest.fixture
def runner(tmp_path: Path):
    store = FakeStore()
    r = JobRunner(store, FakeSearch(), make_settings(tmp_path, max_workers=2))
    yield r, store
    r.shutdown()


def wait_done(runner: JobRunner, jid: str):
    for _ in range(300):
        job = runner.get(jid)
        assert job is not None
        if job.status in ("done", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("timeout")


def test_find_people_success(runner, monkeypatch) -> None:
    r, store = runner
    c = sample_companies()[0]
    p = sample_person(c)
    p.company_id = None
    monkeypatch.setattr(enrich, "find_people", lambda domain, **kw: [p])
    jid = r.submit("find_people", {"domain": c.domain, "company_id": c.id}, "Find")
    job = wait_done(r, jid)
    assert job.status == "done" and job.result == {"people": 1}
    assert job.finished_at is not None
    assert store.people[p.id].company_id == c.id
    assert r.running_count() == 0


def test_find_email_variants(runner, monkeypatch) -> None:
    r, store = runner
    p = sample_person(sample_companies()[0])
    store.save_person(p)
    cand = EmailCandidate("claire@x.example", EmailStatus.VALID, 80, "{first}")
    seen = {}

    def fake(person, domain, *, smtp_probe=False):
        seen.update(domain=domain, probe=smtp_probe)
        return cand

    monkeypatch.setattr(enrich, "find_email", fake)
    job = wait_done(r, r.submit("find_email", {"person_id": p.id}, "e"))
    assert job.result == {"email": "claire@x.example", "status": "valid", "confidence": 80}
    assert seen == {"domain": p.company_domain, "probe": False}
    assert store.best_email(p.id) is cand

    store.emails.clear()
    store.add_suppression("claire@x.example")
    job = wait_done(r, r.submit("find_email", {"person_id": p.id, "n": 2}, "e"))
    assert job.result == {"suppressed": True}
    assert store.best_email(p.id) is None

    monkeypatch.setattr(enrich, "find_email", lambda *a, **k: None)
    job = wait_done(r, r.submit("find_email", {"person_id": p.id, "n": 3}, "e"))
    assert job.result == {"email": None}


def test_failure_is_short_and_hides_traceback(runner, monkeypatch) -> None:
    r, _ = runner

    def boom(domain, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(enrich, "find_people", boom)
    job = wait_done(r, r.submit("find_people", {"domain": "a.example", "company_id": None}, "x"))
    assert job.status == "failed"
    assert job.error == "RuntimeError: network down"
    assert "Traceback" not in job.error
    assert job.finished_at is not None

    def bad_input(domain, **kw):
        raise ValueError("CSV needs a name column")

    monkeypatch.setattr(enrich, "find_people", bad_input)
    job = wait_done(r, r.submit("find_people", {"domain": "a.example", "company_id": None}, "x"))
    assert job.error == "CSV needs a name column"  # a message for the user, no exception name


def test_unknown_person_fails(runner) -> None:
    r, _ = runner
    job = wait_done(r, r.submit("find_email", {"person_id": "ghost"}, "x"))
    assert job.status == "failed" and "not found" in (job.error or "")


def test_dedupe_while_running_then_resubmit_after(runner, monkeypatch) -> None:
    r, _ = runner
    gate = threading.Event()

    def slow(domain, **kw):
        gate.wait(5)
        return []

    monkeypatch.setattr(enrich, "find_people", slow)
    payload = {"domain": "a.example", "company_id": None}
    j1 = r.submit("find_people", payload, "x")
    j2 = r.submit("find_people", dict(reversed(list(payload.items()))), "x")
    assert j1 == j2
    assert r.running_count() == 1
    assert r.submit("find_people", {"domain": "b.example", "company_id": None}, "x") != j1
    gate.set()
    wait_done(r, j1)
    assert r.submit("find_people", payload, "x") != j1


def test_enrich_csv_progress_and_result(runner, monkeypatch, tmp_path: Path) -> None:
    r, _ = runner
    calls = []

    def fake_enrich(inp, out, *, store, search, settings, progress=None):
        for i in range(1, 4):
            progress(i, 3)
            calls.append(r.recent(1)[0].result)
        return {"rows": 3, "found": 2, "output": out}

    from pepite import pipeline

    monkeypatch.setattr(pipeline, "enrich_csv", fake_enrich)
    jid = r.submit("enrich_csv", {"input": "i.csv", "output": "o.csv"}, "x")
    job = wait_done(r, jid)
    assert job.result == {"output": "o.csv", "rows": 3, "found": 2}
    assert calls[0] == {"progress": 1, "total": 3}


def test_keeps_latest_200(runner, monkeypatch) -> None:
    r, _ = runner
    monkeypatch.setattr(enrich, "find_people", lambda d, **kw: [])
    ids = [
        r.submit("find_people", {"domain": f"{i}.example", "company_id": None}, "x")
        for i in range(230)
    ]
    for _ in range(300):
        if r.running_count() == 0:
            break
        time.sleep(0.02)
    assert len(r.recent(1000)) <= 200
    assert r.get(ids[-1]) is not None
    assert r.recent(3)[0].id == ids[-1]


def test_submit_after_shutdown_fails(tmp_path: Path) -> None:
    r = JobRunner(FakeStore(), FakeSearch(), make_settings(tmp_path))
    r.shutdown()
    jid = r.submit("find_people", {"domain": "a", "company_id": None}, "x")
    assert r.get(jid).status == "failed"
