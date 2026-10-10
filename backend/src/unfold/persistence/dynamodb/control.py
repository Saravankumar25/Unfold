"""`ControlStore` on one DynamoDB table (ADR 0012; key design in `docs/infra/architecture-decision.md` §4.4.1).

Every record is a document in attribute `d` next to a monotonically increasing `ver`. A mutation is read, applied
by the caller's function, and written back under the condition `ver = <seen>`; on a lost race it is retried, so
the claim, heartbeat, stage and section updates keep the mutual exclusion the Postgres row locks gave. Job
counters (active per session, queued globally) change in the same transaction as the job they describe, so caps
are exact and a crash cannot leak them.

Partition `P#<project_id>` holds everything of a project, so cleanup is one partition delete. TTL attributes are a
storage backstop only: reads compare `expires_at` and the sweeper deletes explicitly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from unfold.core.errors import Conflict, NotFound, RateLimited, StoreUnavailable
from unfold.domain.records import DiagramRec, JobRec, ProjectRec, SectionRec, SessionRec, SummaryRec
from unfold.persistence.dynamodb.table import ConditionFailed, DynamoTable
from unfold.ports import ControlStore, Mutator

_DT_FIELDS = {"created_at", "last_active_at", "expires_at", "started_at", "completed_at", "heartbeat_at"}
_RETRIES = 8
_TTL_GRACE = timedelta(days=1)
_JOB = "JOB#"


def ts(dt: datetime) -> str:
    """Fixed-width UTC timestamp: sorts correctly as a string (index sort keys, `ver`-free comparisons)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def encode(rec: Any) -> dict[str, Any]:
    out = dataclasses.asdict(rec)
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = ts(v)
    return out


def decode[T](cls: type[T], d: dict[str, Any]) -> T:
    names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    kwargs = {k: v for k, v in d.items() if k in names}
    for k in _DT_FIELDS & kwargs.keys():
        if isinstance(kwargs[k], str):
            kwargs[k] = parse_ts(kwargs[k])
    return cls(**kwargs)


def _epoch(dt: datetime) -> int:
    return int((dt + _TTL_GRACE).timestamp())


def _set_expr(
    sets: dict[str, Any], removes: Sequence[str] = ()
) -> tuple[str, dict[str, str], dict[str, Any]]:
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    parts: list[str] = []
    for i, (attr, value) in enumerate(sets.items()):
        names[f"#s{i}"] = attr
        values[f":s{i}"] = value
        parts.append(f"#s{i} = :s{i}")
    expr = "SET " + ", ".join(parts)
    if removes:
        for i, attr in enumerate(removes):
            names[f"#r{i}"] = attr
        expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(removes)))
    return expr, names, values


def _project_attrs(rec: ProjectRec) -> dict[str, Any]:
    return {
        "GSI1PK": f"S#{rec.session_id}",
        "GSI1SK": ts(rec.created_at),
        "GSI3PK": "PROJECTS",
        "GSI3SK": ts(rec.expires_at),
        "ttl": _epoch(rec.expires_at),
    }


def _job_item(rec: JobRec) -> dict[str, Any]:
    item: dict[str, Any] = {"PK": f"P#{rec.project_id}", "SK": job_sk(rec), "d": encode(rec), "ver": 1}
    item.update(_job_index(rec))
    return item


def job_sk(rec: JobRec) -> str:
    return f"{_JOB}{ts(rec.created_at)}#{rec.id}"


def _job_index(rec: JobRec) -> dict[str, Any]:
    """Sparse GSI2: only non-terminal jobs are indexed, by status and last sign of life."""
    if rec.terminal:
        return {}
    return {"GSI2PK": f"JOB#{rec.status}", "GSI2SK": ts(rec.heartbeat_at or rec.created_at)}


def _counter_delta(old: JobRec | None, new: JobRec) -> dict[str, int]:
    """Change of (active per session, queued global) caused by moving a job from `old` to `new`."""
    was_active = old is not None and not old.terminal
    is_active = not new.terminal
    was_queued = old is not None and old.status == "queued"
    return {
        "active": int(is_active) - int(was_active),
        "queued": int(new.status == "queued") - int(was_queued),
    }


class DynamoControlStore(ControlStore):
    def __init__(self, table: DynamoTable) -> None:
        self._t = table
        self._job_loc: dict[str, tuple[str, str]] = {}  # job id -> (project partition, sort key), immutable

    async def ping(self) -> None:
        await self._t.get("PING", "PING", consistent=False)

    # --- optimistic read-modify-write -------------------------------------------------------------------

    async def _mutate[T](
        self,
        pk: str,
        sk: str,
        cls: type[T],
        fn: Mutator,
        *,
        extra: Any = None,
        label: str,
    ) -> T:
        """`extra(old, new)` -> (sets, removes, counter_ops) merged into the same conditional write."""
        for _ in range(_RETRIES):
            item = await self._t.get(pk, sk)
            if item is None:
                raise NotFound(f"{label} not found.")
            rec = decode(cls, item["d"])
            old = decode(cls, item["d"]) if extra is not None else None
            if fn(rec) is False:
                return rec
            sets: dict[str, Any] = {"d": encode(rec), "ver": int(item["ver"]) + 1}
            removes: list[str] = []
            ops: list[dict[str, Any]] = []
            if extra is not None:
                more_sets, removes, ops = extra(old, rec)
                sets.update(more_sets)
            expr, names, values = _set_expr(sets, removes)
            values[":seen"] = int(item["ver"])
            names["#ver"] = "ver"
            try:
                if ops:
                    await self._t.transact(
                        [
                            {
                                "update": {
                                    "pk": pk,
                                    "sk": sk,
                                    "expression": expr,
                                    "names": names,
                                    "values": values,
                                    "condition": "#ver = :seen",
                                }
                            },
                            *ops,
                        ]
                    )
                else:
                    await self._t.update(pk, sk, expr, names=names, values=values, condition="#ver = :seen")
            except ConditionFailed:
                continue
            return rec
        raise StoreUnavailable(f"{label} is being updated too often; try again.")

    # --- sessions ---------------------------------------------------------------------------------------

    async def create_session(self, token_hash: str, now: datetime, expires_at: datetime) -> SessionRec:
        import uuid

        rec = SessionRec(
            id=str(uuid.uuid4()),
            token_hash=token_hash,
            created_at=now,
            last_active_at=now,
            expires_at=expires_at,
        )
        await self._t.put(
            {
                "PK": f"S#{token_hash}",
                "SK": "META",
                "d": encode(rec),
                "ver": 1,
                "GSI3PK": "SESSIONS",
                "GSI3SK": ts(expires_at),
                "ttl": _epoch(expires_at),
            },
            condition="attribute_not_exists(PK)",
        )
        return rec

    async def session_by_token(self, token_hash: str) -> SessionRec | None:
        item = await self._t.get(f"S#{token_hash}", "META", consistent=False)
        return None if item is None else decode(SessionRec, item["d"])

    async def update_session(self, token_hash: str, fn: Mutator) -> SessionRec:
        def index(
            _old: SessionRec | None, new: SessionRec
        ) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
            return {"GSI3SK": ts(new.expires_at), "ttl": _epoch(new.expires_at)}, [], []

        return await self._mutate(f"S#{token_hash}", "META", SessionRec, fn, extra=index, label="Session")

    async def expired_sessions(self, now: datetime, limit: int) -> list[SessionRec]:
        keys = await self._t.query("SESSIONS", sk_lt=ts(now), index="GSI3", limit=limit)
        out: list[SessionRec] = []
        for k in keys:
            item = await self._t.get(k["PK"], "META")
            if item is not None:
                out.append(decode(SessionRec, item["d"]))
        return out

    async def delete_session(self, token_hash: str) -> None:
        await self._t.delete(f"S#{token_hash}", "META")

    # --- projects and jobs ------------------------------------------------------------------------------

    async def _write_new_job(
        self, job: JobRec, ops: list[dict[str, Any]], max_active: int, max_queued: int
    ) -> None:
        """Create the job item, its pointer and the cap counters in one transaction (after `ops`)."""
        item = _job_item(job)
        pointer = {"PK": f"J#{job.id}", "SK": "META", "project_id": job.project_id, "sk": item["SK"]}
        batch = [
            *ops,
            {"put": {"item": item, "condition": "attribute_not_exists(PK)"}},
            {"put": {"item": pointer}},
            *self._cap_ops(job.session_id, max_active, max_queued),
        ]
        try:
            await self._t.transact(batch)
        except ConditionFailed as exc:
            if max_active <= 0 and max_queued <= 0:
                raise Conflict("That record already exists.") from exc
            # A counter at its cap may have drifted above the truth: recount from the job records (this also
            # repairs it), reject only when a cap really is reached, otherwise try once more.
            await self._verify_caps(job.session_id, max_active, max_queued, exc)
            try:
                await self._t.transact(batch)
            except ConditionFailed as again:
                raise RateLimited("The service is busy; try again in a moment.") from again
        self._job_loc[job.id] = (f"P#{job.project_id}", item["SK"])

    def _cap_ops(self, session_id: str, max_active: int, max_queued: int) -> list[dict[str, Any]]:
        ops: list[dict[str, Any]] = []
        for key, cap in ((f"active#{session_id}", max_active), ("queued", max_queued)):
            spec: dict[str, Any] = {
                "pk": f"C#{key}",
                "sk": "C",
                "expression": "ADD #n :one",
                "names": {"#n": "n"},
                "values": {":one": 1},
            }
            if cap > 0:
                spec["condition"] = "attribute_not_exists(#n) OR #n < :cap"
                spec["values"][":cap"] = cap
            ops.append({"update": spec})
        return ops

    async def _verify_caps(
        self, session_id: str, max_active: int, max_queued: int, exc: ConditionFailed
    ) -> None:
        if max_active > 0:
            active = await self._recount_active(session_id)
            await self._set_counter(f"active#{session_id}", active)
            if active >= max_active:
                raise RateLimited(
                    f"At most {max_active} analyses can be active per session; wait for one to finish."
                ) from exc
        if max_queued > 0:
            queued = len(await self._t.query("JOB#queued", index="GSI2"))
            await self._set_counter("queued", queued)
            if queued >= max_queued:
                raise RateLimited("The analysis queue is full; try again in a few minutes.") from exc

    async def _recount_active(self, session_id: str) -> int:
        projects = await self._t.query(f"S#{session_id}", index="GSI1")
        active = 0
        for p in projects:
            latest = await self._t.query(p["PK"], sk_prefix=_JOB, forward=False, limit=1, consistent=True)
            if latest and not decode(JobRec, latest[0]["d"]).terminal:
                active += 1
        return active

    async def _set_counter(self, key: str, value: int) -> None:
        await self._t.update(f"C#{key}", "C", "SET #n = :n", names={"#n": "n"}, values={":n": value})

    async def create_project(
        self,
        project: ProjectRec,
        job: JobRec | None,
        *,
        max_active_per_session: int = 0,
        max_queued_global: int = 0,
    ) -> None:
        item = {
            "PK": f"P#{project.id}",
            "SK": "META",
            "d": encode(project),
            "ver": 1,
            "evt_seq": 0,
            **_project_attrs(project),
        }
        put = {"put": {"item": item, "condition": "attribute_not_exists(PK)"}}
        if job is None:
            await self._t.transact([put])
            return
        job.session_id = project.session_id
        await self._write_new_job(job, [put], max_active_per_session, max_queued_global)

    async def add_job(
        self, job: JobRec, *, max_active_per_session: int = 0, max_queued_global: int = 0
    ) -> None:
        project = await self.get_project(job.project_id)
        if project is None:
            raise NotFound("Project not found.")
        job.session_id = project.session_id
        await self._write_new_job(job, [], max_active_per_session, max_queued_global)

    async def get_project(self, project_id: str) -> ProjectRec | None:
        item = await self._t.get(f"P#{project_id}", "META")
        return None if item is None else decode(ProjectRec, item["d"])

    async def update_project(self, project_id: str, fn: Mutator) -> ProjectRec:
        def index(
            _old: ProjectRec | None, new: ProjectRec
        ) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
            return _project_attrs(new), [], []

        return await self._mutate(f"P#{project_id}", "META", ProjectRec, fn, extra=index, label="Project")

    async def projects_of_session(self, session_id: str) -> list[tuple[ProjectRec, JobRec | None]]:
        items = await self._t.query(f"S#{session_id}", index="GSI1", forward=False)
        out: list[tuple[ProjectRec, JobRec | None]] = []
        for it in items:
            project = decode(ProjectRec, it["d"])
            out.append((project, await self.latest_job(project.id)))
        return out

    async def expired_projects(self, now: datetime, limit: int) -> list[ProjectRec]:
        keys = await self._t.query("PROJECTS", sk_lt=ts(now), index="GSI3", limit=limit)
        out: list[ProjectRec] = []
        for k in keys:
            item = await self._t.get(k["PK"], "META")
            if item is not None:
                out.append(decode(ProjectRec, item["d"]))
        return out

    async def delete_project_data(self, project_id: str) -> int:
        pk = f"P#{project_id}"
        items = await self._t.query(pk, consistent=True)
        jobs = [decode(JobRec, i["d"]) for i in items if i["SK"].startswith(_JOB)]
        for job in jobs:
            if not job.terminal:  # release the caps the job still holds
                delta = _counter_delta(job, dataclasses.replace(job, status="failed"))
                await self._bump_counters(job.session_id, delta)
        await self._t.batch_write(deletes=[(f"J#{j.id}", "META") for j in jobs])
        # progress events belong to the ProgressStore; META goes last so a crash leaves the project findable
        doomed = [(i["PK"], i["SK"]) for i in items if i["SK"] != "META" and not i["SK"].startswith("EVT#")]
        await self._t.batch_write(deletes=doomed)
        await self._t.delete(pk, "META")
        for j in jobs:
            self._job_loc.pop(j.id, None)
        return len(doomed) + 1

    async def _bump_counters(self, session_id: str, delta: dict[str, int]) -> None:
        for key, amount in ((f"active#{session_id}", delta["active"]), ("queued", delta["queued"])):
            if amount:
                try:
                    values = {":d": amount, ":z": 0} if amount < 0 else {":d": amount}
                    await self._t.update(
                        f"C#{key}",
                        "C",
                        "ADD #n :d",
                        names={"#n": "n"},
                        values=values,
                        condition="#n > :z" if amount < 0 else None,
                    )
                except ConditionFailed:
                    pass  # already at zero: nothing to release

    # --- jobs -------------------------------------------------------------------------------------------

    async def _locate(self, job_id: str) -> tuple[str, str] | None:
        loc = self._job_loc.get(job_id)
        if loc is None:
            pointer = await self._t.get(f"J#{job_id}", "META")
            if pointer is None:
                return None
            loc = (f"P#{pointer['project_id']}", pointer["sk"])
            self._job_loc[job_id] = loc
        return loc

    async def get_job(self, job_id: str) -> JobRec | None:
        loc = await self._locate(job_id)
        item = await self._t.get(*loc) if loc else None
        return None if item is None else decode(JobRec, item["d"])

    async def latest_job(self, project_id: str) -> JobRec | None:
        items = await self._t.query(
            f"P#{project_id}", sk_prefix=_JOB, forward=False, limit=1, consistent=True
        )
        return decode(JobRec, items[0]["d"]) if items else None

    async def update_job(self, job_id: str, fn: Mutator) -> JobRec:
        loc = await self._locate(job_id)
        if loc is None:
            raise NotFound("Analysis job not found.")

        def extra(old: JobRec | None, new: JobRec) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
            assert old is not None
            index = _job_index(new)
            sets = dict(index)
            removes = [a for a in ("GSI2PK", "GSI2SK") if a not in index]
            ops: list[dict[str, Any]] = []
            delta = _counter_delta(old, new)
            for key, amount in ((f"active#{new.session_id}", delta["active"]), ("queued", delta["queued"])):
                if amount:
                    ops.append(
                        {
                            "update": {
                                "pk": f"C#{key}",
                                "sk": "C",
                                "expression": "ADD #n :d",
                                "names": {"#n": "n"},
                                "values": {":d": amount},
                            }
                        }
                    )
            return sets, removes, ops

        return await self._mutate(loc[0], loc[1], JobRec, fn, extra=extra, label="Analysis job")

    async def recoverable_jobs(
        self, *, queued_before: datetime, heartbeat_before: datetime, limit: int
    ) -> list[JobRec]:
        queued = await self._t.query("JOB#queued", sk_lt=ts(queued_before), index="GSI2", limit=limit)
        running = await self._t.query("JOB#running", sk_lt=ts(heartbeat_before), index="GSI2", limit=limit)
        return [decode(JobRec, i["d"]) for i in [*queued, *running]][:limit]

    async def active_job_counts(self) -> tuple[int, int]:
        queued = await self._t.query("JOB#queued", index="GSI2")
        running = await self._t.query("JOB#running", index="GSI2")
        await self._set_counter("queued", len(queued))
        return len(queued), len(running)

    async def checkpoints(self, job_id: str) -> set[str]:
        loc = await self._locate(job_id)
        if loc is None:
            return set()
        prefix = f"CKPT#{job_id}#"
        items = await self._t.query(loc[0], sk_prefix=prefix, consistent=True)
        return {i["SK"][len(prefix) :] for i in items}

    async def put_checkpoint(self, job_id: str, name: str, data: dict[str, Any]) -> None:
        loc = await self._locate(job_id)
        if loc is None:
            raise NotFound("Analysis job not found.")
        await self._t.put({"PK": loc[0], "SK": f"CKPT#{job_id}#{name}", "data": data})

    # --- guide content ----------------------------------------------------------------------------------

    async def _replace(self, project_id: str, prefix: str, keep: dict[str, dict[str, Any]]) -> None:
        """Write the new set, then delete what is no longer in it (readers see old, mixed, then new)."""
        pk = f"P#{project_id}"
        existing = await self._t.query(pk, sk_prefix=prefix, consistent=True)
        await self._t.batch_write(puts=list(keep.values()))
        await self._t.batch_write(deletes=[(pk, i["SK"]) for i in existing if i["SK"] not in keep])

    async def replace_summaries(self, project_id: str, rows: Sequence[SummaryRec]) -> None:
        pk = f"P#{project_id}"
        keep = {
            f"SUM#{r.level}#{r.key}": {"PK": pk, "SK": f"SUM#{r.level}#{r.key}", "d": encode(r)} for r in rows
        }
        await self._replace(project_id, "SUM#", keep)

    async def summaries(self, project_id: str, level: str | None = None) -> list[SummaryRec]:
        prefix = "SUM#" if level is None else f"SUM#{level}#"
        items = await self._t.query(f"P#{project_id}", sk_prefix=prefix, consistent=True)
        return [decode(SummaryRec, i["d"]) for i in items]

    async def replace_plan(
        self, project_id: str, plan: dict[str, Any], planner_version: str, sections: Sequence[SectionRec]
    ) -> None:
        pk = f"P#{project_id}"
        keep = {
            f"SEC#{s.section_key}": {"PK": pk, "SK": f"SEC#{s.section_key}", "d": encode(s), "ver": 1}
            for s in sections
        }
        await self._replace(project_id, "SEC#", keep)
        await self._t.put({"PK": pk, "SK": "PLAN", "plan": plan, "planner_version": planner_version})

    async def get_plan(self, project_id: str) -> dict[str, Any] | None:
        item = await self._t.get(f"P#{project_id}", "PLAN")
        return None if item is None else dict(item["plan"])

    async def sections(self, project_id: str) -> list[SectionRec]:
        items = await self._t.query(f"P#{project_id}", sk_prefix="SEC#", consistent=True)
        return sorted((decode(SectionRec, i["d"]) for i in items), key=lambda s: s.position)

    async def get_section(self, project_id: str, key: str) -> SectionRec | None:
        item = await self._t.get(f"P#{project_id}", f"SEC#{key}")
        return None if item is None else decode(SectionRec, item["d"])

    async def update_section(self, project_id: str, key: str, fn: Mutator) -> SectionRec:
        return await self._mutate(f"P#{project_id}", f"SEC#{key}", SectionRec, fn, label="Section")

    async def replace_diagrams(self, project_id: str, rows: Sequence[DiagramRec]) -> None:
        pk = f"P#{project_id}"
        keep = {
            f"DIAG#{r.id}": {"PK": pk, "SK": f"DIAG#{r.id}", "d": encode(r), "kind": r.kind} for r in rows
        }
        await self._replace(project_id, "DIAG#", keep)

    async def diagrams(self, project_id: str, kind: str | None = None) -> list[DiagramRec]:
        items = await self._t.query(f"P#{project_id}", sk_prefix="DIAG#", consistent=True)
        return [decode(DiagramRec, i["d"]) for i in items if kind is None or i.get("kind") == kind]

    async def get_diagram(self, project_id: str, diagram_id: str) -> DiagramRec | None:
        item = await self._t.get(f"P#{project_id}", f"DIAG#{diagram_id}")
        return None if item is None else decode(DiagramRec, item["d"])
