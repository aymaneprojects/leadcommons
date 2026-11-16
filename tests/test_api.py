from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pepite import enrich
from pepite.config import Settings
from pepite.web import api
from pepite.web.auth import install_auth
from pepite.web.jobs import JobRunner
from tests.fakes import (
    FakeJobs,
    FakeSearch,
    FakeStore,
    sample_companies,
    sample_email,
    sample_person,
)


def make_settings(tmp_path: Path, **kw: Any) -> Settings:
    kw.setdefault("mode", "local")
    kw.setdefault("password", None)
    kw.setdefault("password_hash", None)
    return Settings(home=tmp_path, data_dir=tmp_path / "packs", database_url="sqlite://", **kw)


def build_app(settings: Settings, *, jobs: Any = None, store: Any = None, search: Any = None):
    app = FastAPI()
    app.state.settings = settings
    app.state.search = search or FakeSearch()
    app.state.store = store or FakeStore()
    app.state.jobs = jobs or FakeJobs()
    install_auth(app, settings)
    app.include_router(api.router, prefix="/api")

    @app.get("/")
    def index() -> dict[str, str]:
        return {"page": "home"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def ctx(tmp_path: Path):
    settings = make_settings(tmp_path)
    app = build_app(settings)
    return TestClient(app), app


def test_companies_filters_and_pagination(ctx) -> None:
    client, _ = ctx
    r = client.get("/api/companies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 5
    r = client.get("/api/companies", params={"country": ["DE"]})
    assert r.json()["total"] == 2
    r = client.get("/api/companies", params={"country": ["fr", "gb"], "size": "1-10"})
    assert [c["name"] for c in r.json()["items"]] == ["Moss and Pine"]
    r = client.get("/api/companies", params={"q": "nord", "per_page": 1})
    assert r.json()["items"][0]["name"] == "Nordwind Data"
    r = client.get("/api/companies", params={"per_page": 2, "page": 3})
    body = r.json()
    assert body["page"] == 3 and len(body["items"]) == 1 and body["total"] == 5
    assert client.get("/api/companies", params={"domain": "kestrel.example"}).json()["total"] == 1


def test_companies_validation(ctx) -> None:
    client, _ = ctx
    assert client.get("/api/companies", params={"per_page": 101}).status_code == 422
    assert client.get("/api/companies", params={"page": 0}).status_code == 422


def test_company_detail_and_404(ctx) -> None:
    client, app = ctx
    c = sample_companies()[0]
    r = client.get(f"/api/companies/{c.id}")
    assert r.status_code == 200
    assert r.json()["domain"] == c.domain
    assert r.json()["sources"][0]["fetched_at"]  # ISO string
    assert client.get("/api/companies/nope").status_code == 404
    assert client.post("/api/companies/nope/find-people").status_code == 404


def test_company_find_people_submits_job(ctx) -> None:
    client, app = ctx
    c = sample_companies()[1]
    r = client.post(f"/api/companies/{c.id}/find-people")
    assert r.status_code == 200
    job = app.state.jobs.get(r.json()["job_id"])
    assert job.kind == "find_people"
    assert job.payload == {"domain": c.domain, "company_id": c.id}


def test_company_without_domain_400(tmp_path: Path) -> None:
    comp = sample_companies()[0]
    comp.domain = None
    app = build_app(make_settings(tmp_path), search=FakeSearch([comp]))
    r = TestClient(app).post(f"/api/companies/{comp.id}/find-people")
    assert r.status_code == 400


def test_people_endpoints(ctx) -> None:
    client, app = ctx
    store: FakeStore = app.state.store
    c = sample_companies()[0]
    p1 = sample_person(c)
    p2 = sample_person(c, "Paul Durand", "CTO")
    store.save_person(p1)
    store.save_person(p2)
    store.save_email(p1.id, sample_email("claire.martin@ateliernuage.example"))
    body = client.get("/api/people").json()
    assert body["total"] == 2
    by_name = {p["full_name"]: p for p in body["items"]}
    assert by_name["Claire Martin"]["email"]["address"] == "claire.martin@ateliernuage.example"
    assert by_name["Claire Martin"]["email"]["status"] == "valid"
    assert by_name["Paul Durand"]["email"] is None
    assert client.get("/api/people", params={"q": "cto"}).json()["total"] == 1
    assert client.get("/api/people", params={"email_status": "valid"}).json()["total"] == 1
    assert client.get("/api/people", params={"email_status": "bogus"}).status_code == 422
    assert client.get("/api/people", params={"seniority": "bogus"}).status_code == 422
    detail = client.get(f"/api/people/{p1.id}").json()
    assert detail["emails"][0]["address"].startswith("claire")
    assert client.get("/api/people/missing").status_code == 404


def test_person_find_email(ctx) -> None:
    client, app = ctx
    c = sample_companies()[0]
    p = sample_person(c)
    app.state.store.save_person(p)
    r = client.post(f"/api/people/{p.id}/find-email")
    assert r.status_code == 200
    assert app.state.jobs.get(r.json()["job_id"]).payload == {"person_id": p.id}
    assert client.post("/api/people/zzz/find-email").status_code == 404
    p2 = sample_person(c, "No Domain")
    p2.company_domain = None
    app.state.store.save_person(p2)
    assert client.post(f"/api/people/{p2.id}/find-email").status_code == 400


def test_jobs_endpoints(ctx) -> None:
    client, app = ctx
    jid = app.state.jobs.submit("find_people", {"domain": "x.example"}, "Find")
    body = client.get(f"/api/jobs/{jid}").json()
    assert body["status"] == "running" and body["label"] == "Find"
    assert {"result", "error", "created_at", "finished_at"} <= body.keys()
    assert body["created_at"].count("T") == 1
    assert client.get("/api/jobs").json()["items"][0]["id"] == jid
    assert client.get("/api/jobs/nope").status_code == 404


def test_upload_validation(ctx) -> None:
    client, _ = ctx
    r = client.post("/api/enrich", files={"file": ("x.txt", b"a,b\n1,2\n", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/enrich", files={"file": ("x.csv", b"", "text/csv")})
    assert r.status_code == 400
    big = b"a" * (5 * 1024 * 1024 + 1)
    r = client.post("/api/enrich", files={"file": ("x.csv", big, "text/csv")})
    assert r.status_code == 413
    assert client.post("/api/enrich").status_code == 422


def test_upload_and_result_download_states(ctx, tmp_path: Path) -> None:
    client, app = ctx
    r = client.post(
        "/api/enrich", files={"file": ("p.csv", b"name,domain\nA B,x.example\n", "text/csv")}
    )
    assert r.status_code == 200
    jid = r.json()["job_id"]
    job = app.state.jobs.get(jid)
    assert Path(job.payload["input"]).parent == tmp_path / "uploads"
    assert Path(job.payload["input"]).read_bytes().startswith(b"name,domain")
    assert Path(job.payload["output"]).parent == tmp_path / "exports"
    assert job.payload["output"].endswith("-enriched.csv")
    # running -> 409
    assert client.get(f"/api/enrich/{jid}/result.csv").status_code == 409
    # unknown -> 404
    assert client.get("/api/enrich/nope/result.csv").status_code == 404
    # done -> file
    Path(job.payload["output"]).write_text("a,b\n1,2\n")
    job.status = "done"
    job.result = {"output": job.payload["output"]}
    r = client.get(f"/api/enrich/{jid}/result.csv")
    assert r.status_code == 200 and r.text == "a,b\n1,2\n"
    assert r.headers["content-type"].startswith("text/csv")
    # failed -> 409
    job.status = "failed"
    job.error = "boom"
    assert client.get(f"/api/enrich/{jid}/result.csv").status_code == 409
    # non-enrich job -> 404
    other = app.state.jobs.submit("find_people", {"domain": "z"}, "x")
    assert client.get(f"/api/enrich/{other}/result.csv").status_code == 404


def test_lists_flow(ctx) -> None:
    client, app = ctx
    assert client.post("/api/lists", json={"name": "  ", "kind": "people"}).status_code == 422
    assert client.post("/api/lists", json={"name": "x", "kind": "weird"}).status_code == 422
    r = client.post("/api/lists", json={"name": "Targets", "kind": "people"})
    assert r.status_code == 201
    lid = r.json()["id"]
    c = sample_companies()[0]
    p = sample_person(c)
    app.state.store.save_person(p)
    r = client.post(f"/api/lists/{lid}/items", json={"record_ids": [p.id]})
    assert r.status_code == 200 and r.json() == {"added": 1}
    lists = client.get("/api/lists").json()["items"]
    assert lists[0]["name"] == "Targets" and lists[0]["size"] == 1
    assert lists[0]["created_at"]
    items = client.get(f"/api/lists/{lid}/items").json()
    assert items["record_ids"] == [p.id]
    assert items["items"][0]["full_name"] == "Claire Martin"
    assert client.post(f"/api/lists/{lid}/items", json={"record_ids": []}).status_code == 422
    assert client.post("/api/lists/nope/items", json={"record_ids": ["a"]}).status_code == 404
    assert client.get("/api/lists/nope/items").status_code == 404
    cl = client.post("/api/lists", json={"name": "Cos", "kind": "companies"}).json()["id"]
    client.post(f"/api/lists/{cl}/items", json={"record_ids": [c.id, "unknown"]})
    items = client.get(f"/api/lists/{cl}/items").json()["items"]
    assert [i["name"] for i in items] == [c.name]


def test_suppression_and_packs(ctx) -> None:
    client, app = ctx
    assert client.post("/api/suppression", json={"value": "Nope@Example.com"}).status_code == 201
    assert app.state.store.is_suppressed("nope@example.com")
    assert client.post("/api/suppression", json={"value": ""}).status_code == 422
    assert client.post("/api/suppression", json={"value": "a b"}).status_code == 422
    assert client.get("/api/packs").json()["items"][0]["file"] == "fake.parquet"


# -- real JobRunner end to end ------------------------------------------------------------


def _wait(client: TestClient, jid: str) -> dict[str, Any]:
    for _ in range(200):
        body = client.get(f"/api/jobs/{jid}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_end_to_end_with_real_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    store, search = FakeStore(), FakeSearch()
    runner = JobRunner(store, search, settings)
    client = TestClient(build_app(settings, jobs=runner, store=store, search=search))
    c = sample_companies()[0]
    person = sample_person(c)
    monkeypatch.setattr(enrich, "find_people", lambda domain, **kw: [person])
    jid = client.post(f"/api/companies/{c.id}/find-people").json()["job_id"]
    body = _wait(client, jid)
    assert body["status"] == "done" and body["result"] == {"people": 1}
    assert store.people[person.id].company_id == c.id
    runner.shutdown()
