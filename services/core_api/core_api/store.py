"""Decision history and system alerts in Postgres, with a degraded mode when it is down.

Writes that fail while the database is unavailable go to an in-memory buffer and are
flushed on reconnect (brief §11: dependency unavailable -> retry / cached state / degraded
mode). Reads serve an in-memory mirror of the latest rows, so the operator UI keeps working.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import UTC, datetime
from typing import Any

import asyncpg
import structlog
from prometheus_client import Counter, Gauge

log = structlog.get_logger()

DB_AVAILABLE = Gauge("jalani_database_available", "1 if core-api's last database call worked.")
DB_BUFFERED = Gauge("jalani_database_buffered_writes", "Writes waiting for the database.")
ALERTS_RAISED = Counter(
    "jalani_system_alerts_total",
    "System alerts raised, by kind and severity.",
    ["kind", "severity"],
)
DECISIONS = Counter("jalani_decisions_total", "Operator decisions recorded, by result.", ["result"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id BIGSERIAL PRIMARY KEY,
    decided_at TIMESTAMPTZ NOT NULL,
    sim_tick INT NOT NULL,
    recommendation_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    fuel TEXT NOT NULL,
    depot_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    policy TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    review_required BOOLEAN NOT NULL,
    reviewed BOOLEAN NOT NULL,
    decided_by TEXT NOT NULL,
    role TEXT NOT NULL,
    risk_before JSONB,
    risk_after JSONB,
    result TEXT NOT NULL,
    simulator_status INT,
    simulator_detail JSONB,
    allocation_id INT,
    outcome TEXT,
    outcome_tick INT,
    outcome_updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS decisions_allocation ON decisions (allocation_id);
CREATE TABLE IF NOT EXISTS system_alerts (
    id BIGSERIAL PRIMARY KEY,
    raised_at TIMESTAMPTZ NOT NULL,
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    resolved_at TIMESTAMPTZ
);
"""
FINAL_OUTCOMES = {"ARRIVED", "FAILED", "CANCELLED"}


def _now() -> datetime:
    return datetime.now(UTC)


async def _init(con: asyncpg.Connection) -> None:
    await con.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Store:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None
        self.available = False
        self.last_error: str | None = None
        self._pending: deque[tuple[str, tuple[Any, ...]]] = deque(maxlen=5000)
        self.decisions: deque[dict[str, Any]] = deque(maxlen=200)  # newest last
        self.alerts: deque[dict[str, Any]] = deque(maxlen=200)
        self._open: dict[tuple[str, str], dict[str, Any]] = {}  # (source, kind) -> alert
        self._next_id = -1  # local ids until the database assigns real ones
        self._lock = asyncio.Lock()
        self._schema_ready = False

    # -- connection -----------------------------------------------------------------------
    async def connect(self) -> bool:
        if self.pool is not None and self.available:
            return True
        try:
            if self.pool is None:
                self.pool = await asyncpg.create_pool(
                    self.dsn, min_size=1, max_size=5, init=_init, command_timeout=3, timeout=3
                )
            async with self.pool.acquire() as con:
                await con.execute(SCHEMA)
                if not self._schema_ready:
                    # Alerts left open by a previous run describe conditions this instance
                    # re-evaluates live, so close them instead of showing stale ones.
                    await con.execute(
                        "UPDATE system_alerts SET resolved_at = now() WHERE resolved_at IS NULL"
                    )
                    self._schema_ready = True
            await self._set_available(True)
            await self.flush()
            await self._load_recent()
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
            await self._set_available(False, exc)
        return self.available

    async def _set_available(self, ok: bool, exc: BaseException | None = None) -> None:
        if ok and not self.available:
            log.info("integration.database_recovered")
        if not ok and self.available:
            log.warning("integration.database_failed", error=repr(exc))
        self.available = ok
        self.last_error = None if ok else f"{type(exc).__name__}: {exc}"[:200]
        DB_AVAILABLE.set(1 if ok else 0)

    async def ping(self) -> bool:
        if self.pool is None or not self.available:
            return await self.connect()
        try:
            async with self.pool.acquire(timeout=2) as con:
                await con.fetchval("SELECT 1")
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
            await self._set_available(False, exc)
        return self.available

    async def _write(self, sql: str, *args: Any) -> Any:
        if self.available and self.pool is not None:
            try:
                async with self.pool.acquire(timeout=2) as con:
                    return await con.fetchval(sql, *args)
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
                await self._set_available(False, exc)
        self._pending.append((sql, args))
        DB_BUFFERED.set(len(self._pending))
        return None

    async def flush(self) -> None:
        async with self._lock:
            while self._pending and self.available and self.pool is not None:
                sql, args = self._pending[0]
                try:
                    async with self.pool.acquire(timeout=2) as con:
                        await con.fetchval(sql, *args)
                except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as e:
                    await self._set_available(False, e)
                    break
                self._pending.popleft()
            DB_BUFFERED.set(len(self._pending))

    async def _load_recent(self) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire(timeout=2) as con:
            rows = await con.fetch("SELECT * FROM decisions ORDER BY id DESC LIMIT 200")
            alerts = await con.fetch("SELECT * FROM system_alerts ORDER BY id DESC LIMIT 200")
        if rows and not self.decisions:
            self.decisions.extend(_row(r) for r in reversed(rows))
        if alerts and not self.alerts:
            for a in reversed(alerts):
                item = _row(a)
                self.alerts.append(item)
                if item["resolved_at"] is None:
                    self._open[(item["source"], item["kind"])] = item

    # -- decisions ------------------------------------------------------------------------
    async def record_decision(self, d: dict[str, Any]) -> dict[str, Any]:
        d = {**d, "decided_at": _now(), "outcome": d.get("outcome"), "outcome_tick": None}
        DECISIONS.labels(d["result"]).inc()
        new_id = await self._write(
            """INSERT INTO decisions (decided_at, sim_tick, recommendation_id, station_id, fuel,
               depot_id, route_id, quantity, policy, confidence, review_required, reviewed,
               decided_by, role, risk_before, risk_after, result, simulator_status,
               simulator_detail, allocation_id, outcome)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
               RETURNING id""",
            d["decided_at"], d["sim_tick"], d["recommendation_id"], d["station_id"], d["fuel"],
            d["depot_id"], d["route_id"], d["quantity"], d["policy"], d["confidence"],
            d["review_required"], d["reviewed"], d["decided_by"], d["role"], d["risk_before"],
            d["risk_after"], d["result"], d["simulator_status"], d["simulator_detail"],
            d["allocation_id"], d["outcome"],
        )  # fmt: skip
        d["id"] = new_id if new_id is not None else self._local_id()
        d["persisted"] = new_id is not None
        self.decisions.append(d)
        return d

    async def update_outcomes(self, allocations: list[dict[str, Any]], tick: int) -> None:
        """Reconciler: copy each allocation's lifecycle status onto its decision."""
        by_id = {a["id"]: a for a in allocations}
        for d in self.decisions:
            a = by_id.get(d.get("allocation_id"))
            if a is None or d.get("outcome") in FINAL_OUTCOMES or d.get("outcome") == a["status"]:
                continue
            d["outcome"] = a["status"]
            d["outcome_tick"] = a.get("actual_arrival_tick") or tick
            if a.get("failure_reason"):
                d["failure_reason"] = a["failure_reason"]
            await self._write(
                "UPDATE decisions SET outcome=$1, outcome_tick=$2, outcome_updated_at=$3 "
                "WHERE allocation_id=$4",
                d["outcome"], d["outcome_tick"], _now(), d["allocation_id"],
            )  # fmt: skip

    def recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [_public(d) for d in list(self.decisions)[-limit:][::-1]]

    # -- alerts ---------------------------------------------------------------------------
    async def raise_alert(
        self, source: str, kind: str, severity: str, message: str, details: Any = None
    ) -> None:
        key = (source, kind)
        if key in self._open:  # already open: just refresh the message
            self._open[key]["message"] = message
            self._open[key]["details"] = details
            return
        alert = {
            "id": self._local_id(),
            "raised_at": _now(),
            "severity": severity,
            "source": source,
            "kind": kind,
            "message": message,
            "details": details,
            "resolved_at": None,
        }
        self._open[key] = alert
        self.alerts.append(alert)
        ALERTS_RAISED.labels(kind, severity).inc()
        log.warning("alert.raised", source=source, kind=kind, severity=severity, message=message)
        new_id = await self._write(
            "INSERT INTO system_alerts (raised_at, severity, source, kind, message, details) "
            "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
            alert["raised_at"], severity, source, kind, message, details,
        )  # fmt: skip
        if new_id is not None:
            alert["id"] = new_id

    async def resolve_alert(self, source: str, kind: str) -> None:
        alert = self._open.pop((source, kind), None)
        if alert is None:
            return
        alert["resolved_at"] = _now()
        log.info("alert.resolved", source=source, kind=kind)
        await self._write(
            "UPDATE system_alerts SET resolved_at=$1 WHERE source=$2 AND kind=$3 "
            "AND resolved_at IS NULL",
            alert["resolved_at"], source, kind,
        )  # fmt: skip

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        return [_public(a) for a in list(self.alerts)[-limit:][::-1]]

    def open_alerts(self) -> list[dict[str, Any]]:
        return [_public(a) for a in self._open.values()]

    def _local_id(self) -> int:
        self._next_id -= 1
        return self._next_id

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self.last_error,
            "buffered_writes": len(self._pending),
        }

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()


def _row(r: asyncpg.Record) -> dict[str, Any]:
    return dict(r.items())


def _public(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out
