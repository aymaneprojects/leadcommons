"""Background jobs (find people, find email, enrich CSV) run in-process on a thread pool.

SHARED CONTRACT (signatures). Owner: app-core agent.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from leadcommons import workflows
from leadcommons.models import utcnow

log = logging.getLogger(__name__)

JobKind = Literal["find_people", "find_email", "enrich_csv", "company_insights"]
JobStatus = Literal["queued", "running", "done", "failed"]

MAX_JOBS = 200
DEMO_DISABLED = (
    "Disabled in the public demo. Install LeadCommons on your computer or server "
    "to find people and emails."
)


@dataclass
class Job:
    id: str
    kind: JobKind
    label: str  # human text for the UI, e.g. "Find people at example.com"
    payload: dict[str, Any]
    status: JobStatus = "queued"
    result: dict[str, Any] | None = None  # e.g. {"people": 4} or {"output": "/path.csv"}
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


class JobRunner:
    """payload by kind:
    find_people: {"domain": str, "company_id": str | None}
    find_email:  {"person_id": str}
    enrich_csv:  {"input": str (path), "output": str (path)}
    company_insights: {"company_id": str, "domain": str | None}
    """

    def __init__(self, store: Any, search: Any, settings: Any) -> None:
        self.store = store
        self.search = search
        self.settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(settings.max_workers)), thread_name_prefix="lc-job"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(kind: str, payload: dict[str, Any]) -> str:
        return kind + json.dumps(payload, sort_keys=True, default=str)

    def submit(self, kind: JobKind, payload: dict[str, Any], label: str) -> str:
        key = self._key(kind, payload)
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("queued", "running") and self._key(job.kind, job.payload) == key:
                    return job.id
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, payload=dict(payload))
            self._jobs[job.id] = job
            self._trim()
        if not getattr(self.settings, "enrichment_enabled", True):
            self._finish(job.id, "failed", error=DEMO_DISABLED)
            return job.id
        if getattr(self.settings, "sync_jobs", False):
            self._run(job.id)  # serverless: threads are frozen once the response is sent
            return job.id
        try:
            self._pool.submit(self._run, job.id)
        except RuntimeError:  # pool shut down
            self._finish(job.id, "failed", error="Job runner is shut down")
        return job.id

    def _trim(self) -> None:
        # called with lock held; drop oldest finished jobs beyond MAX_JOBS
        excess = len(self._jobs) - MAX_JOBS
        if excess <= 0:
            return
        for jid in [j.id for j in self._jobs.values() if j.status in ("done", "failed")][:excess]:
            del self._jobs[jid]
        while len(self._jobs) > MAX_JOBS:
            del self._jobs[next(iter(self._jobs))]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))[:limit]

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals -------------------------------------------------------------------------

    def _finish(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            if result is not None:
                job.result = result
            job.error = error
            job.finished_at = utcnow()

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
        handler: Callable[[Job], dict[str, Any]] = getattr(self, f"_do_{job.kind}", self._unknown)
        try:
            result = handler(job)
        except Exception as exc:
            log.exception("job %s (%s) failed", job_id, job.kind)
            msg = f"{type(exc).__name__}: {exc}".strip()
            self._finish(job_id, "failed", error=msg[:200])
        else:
            self._finish(job_id, "done", result=result)

    def _unknown(self, job: Job) -> dict[str, Any]:
        raise ValueError(f"Unknown job kind {job.kind!r}")

    def _do_find_people(self, job: Job) -> dict[str, Any]:
        from leadcommons import enrich

        domain = job.payload["domain"]
        company_id = job.payload.get("company_id")
        people = enrich.find_people(domain)
        for p in people:
            if company_id:
                p.company_id = company_id
            self.store.save_person(p)
            workflows.emit("person_found", person_id=p.id, company_id=company_id, domain=domain)
        return {"people": len(people)}

    def _do_find_email(self, job: Job) -> dict[str, Any]:
        from leadcommons import enrich

        person = self.store.get_person(job.payload["person_id"])
        if person is None:
            raise ValueError("Person not found")
        domain = person.company_domain
        if not domain:
            raise ValueError("Person has no company domain")
        cand = enrich.find_email(person, domain, smtp_probe=self.settings.smtp_probe)
        if cand is None:
            return {"email": None}
        if self.store.is_suppressed(cand.address):
            return {"suppressed": True}
        self.store.save_email(person.id, cand)
        status = getattr(cand.status, "value", cand.status)
        workflows.emit("email_found", person_id=person.id, address=cand.address, status=str(status))
        return {"email": cand.address, "status": str(status), "confidence": cand.confidence}

    def _do_enrich_csv(self, job: Job) -> dict[str, Any]:
        from leadcommons import pipeline

        def progress(i: int, total: int) -> None:
            with self._lock:
                job.result = {"progress": i, "total": total}

        res = pipeline.enrich_csv(
            job.payload["input"],
            job.payload["output"],
            store=self.store,
            search=self.search,
            settings=self.settings,
            progress=progress,
        )
        return {"output": res["output"], "rows": res["rows"], "found": res["found"]}

    def _do_company_insights(self, job: Job) -> dict[str, Any]:
        from leadcommons import insights
        from leadcommons.models import Company

        company_id = job.payload["company_id"]
        company = self.search.get_company(company_id)
        if company is None and hasattr(self.store, "get_company"):
            company = self.store.get_company(company_id)
        if company is None:
            company = Company(id=company_id, name=job.payload.get("domain") or company_id)
        if not company.domain and job.payload.get("domain"):
            company.domain = job.payload["domain"]
        res = insights.refresh(self.store, company, settings=self.settings)
        return {
            "technologies": len(res.technologies),
            "jobs": len(res.jobs.postings),
            "phones": len(res.contact.phones),
        }
