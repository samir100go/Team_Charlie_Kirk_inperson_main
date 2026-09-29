"""Shared helpers for the Phase 0 simulator scripts.

Deliberately dependency-light (stdlib + httpx) and synchronous: these scripts
exist to *observe* the simulator exactly as it behaves, so they record raw
status codes, headers and bodies instead of interpreting them.

Fixture file format (shared with the doc-derived fixtures and the contract tests):

    {
      "_meta": {"source": "recorded" | "doc-derived", "endpoint": "<logical name>", ...},
      "request": {"method": "GET", "path": "/v1/depots", "params": null, "json": null},
      "response": {"status": 200, "headers": {...}, "json": <body>}     # or "text": "<body>"
    }
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("SIMULATOR_URL", "http://localhost:8000")
SIMULATOR_IMAGE = os.environ.get(
    "SIMULATOR_IMAGE", "asifmahmoud414/bup-fuel-supply-simulator:1.0.0"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "services" / "common" / "tests" / "fixtures"

# Headers that change on every request and would make fixture diffs noisy.
VOLATILE_HEADERS = {"date", "content-length"}

FUELS = ("DIESEL", "PETROL", "OCTANE")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Rec:
    """One recorded HTTP exchange."""

    method: str
    path: str
    params: dict[str, Any] | None
    body: Any
    status: int
    headers: dict[str, str]
    json: Any = None
    text: str | None = None
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> str | None:
        """Error code from any of the simulator's envelopes, if present."""
        body = self.json
        if isinstance(body, dict):
            for key in ("detail", "error"):
                inner = body.get(key)
                if isinstance(inner, dict) and "code" in inner:
                    return str(inner["code"])
            if isinstance(body.get("detail"), list):
                return "VALIDATION_ERROR"
        return None

    def to_fixture(
        self, endpoint: str, *, note: str | None = None, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response: dict[str, Any] = {"status": self.status, "headers": self.headers}
        if self.json is not None:
            response["json"] = self.json
        else:
            response["text"] = self.text
        meta: dict[str, Any] = {
            "source": "recorded",
            "endpoint": endpoint,
            "recorded_at": now_iso(),
            "simulator_image": SIMULATOR_IMAGE,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }
        if note:
            meta["note"] = note
        if extra:
            meta.update(extra)
        return {
            "_meta": meta,
            "request": {
                "method": self.method,
                "path": self.path,
                "params": self.params,
                "json": self.body,
            },
            "response": response,
        }


def _clean_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if k.lower() not in VOLATILE_HEADERS}


class Sim:
    """Thin recording wrapper around the simulator's REST + admin API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    # -- raw requests ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Rec:
        started = time.perf_counter()
        resp = self.client.request(method, path, params=params, json=body)
        elapsed_ms = (time.perf_counter() - started) * 1000
        parsed: Any = None
        text: str | None = None
        if "json" in resp.headers.get("content-type", ""):
            try:
                parsed = resp.json()
            except ValueError:
                text = resp.text
        else:
            text = resp.text
        return Rec(
            method=method,
            path=path,
            params=params,
            body=body,
            status=resp.status_code,
            headers=_clean_headers(resp.headers),
            json=parsed,
            text=text,
            elapsed_ms=elapsed_ms,
        )

    def get(self, path: str, **params: Any) -> Rec:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None) -> Rec:
        return self.request("POST", path, body=body)

    # -- convenience ----------------------------------------------------------

    def get_json(self, path: str, **params: Any) -> Any:
        rec = self.get(path, **params)
        if not rec.ok:
            raise RuntimeError(f"GET {path} -> {rec.status}: {rec.json or rec.text}")
        return rec.json

    def wait_healthy(self, timeout_s: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                rec = self.get("/v1/health")
                if rec.ok:
                    return dict(rec.json)
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(0.5)
        raise SystemExit(
            f"Simulator at {self.base_url} is not healthy after {timeout_s}s ({last_error}). "
            "Start it with: make sim-up"
        )

    def instance(self) -> dict[str, Any]:
        return dict(self.get_json("/v1/instance"))

    def tick(self) -> int:
        return int(self.instance()["tick"])

    def reset(self) -> None:
        """Hard reset and leave the world PAUSED at tick 0."""
        self.clear_faults()
        self.post("/admin/reset")
        self.post("/admin/pause")

    def pause(self) -> Rec:
        return self.post("/admin/pause")

    def run(self) -> Rec:
        return self.post("/admin/run")

    def step(self, n: int = 1) -> int:
        tick = -1
        for _ in range(n):
            rec = self.post("/admin/step")
            if not rec.ok:
                raise RuntimeError(f"/admin/step -> {rec.status}: {rec.json or rec.text}")
            tick = int(rec.json["tick"])
        return tick

    def clear_faults(self) -> Rec:
        return self.post("/admin/faults/clear")

    def inject_event(
        self, type_: str, start_tick: int, duration_ticks: int, **parameters: Any
    ) -> Rec:
        return self.post(
            "/admin/events",
            {
                "type": type_,
                "start_tick": start_tick,
                "duration_ticks": duration_ticks,
                "parameters": parameters,
            },
        )

    def inject_fault(self, type_: str, duration_seconds: int, **parameters: Any) -> Rec:
        return self.post(
            "/admin/faults",
            {"type": type_, "duration_seconds": duration_seconds, "parameters": parameters},
        )

    def allocate(
        self,
        key: str,
        depot: str,
        station: str,
        route: str,
        fuel: str,
        quantity: float,
    ) -> Rec:
        return self.post(
            "/v1/allocations",
            {
                "idempotency_key": key,
                "source_depot_id": depot,
                "destination_station_id": station,
                "route_id": route,
                "fuel_type": fuel,
                "quantity": quantity,
            },
        )

    def allocation(self, allocation_id: int) -> dict[str, Any] | None:
        for item in self.get_json("/v1/allocations"):
            if item["id"] == allocation_id:
                return dict(item)
        return None

    def depot(self, depot_id: str) -> dict[str, Any]:
        return dict(self.get_json(f"/v1/depots/{depot_id}"))

    def station(self, station_id: str) -> dict[str, Any]:
        return dict(self.get_json(f"/v1/stations/{station_id}"))

    def route(self, route_id: str) -> dict[str, Any]:
        for item in self.get_json("/v1/routes"):
            if item["id"] == route_id:
                return dict(item)
        raise KeyError(route_id)

    def event(self, event_id: int) -> dict[str, Any] | None:
        for item in self.get_json("/v1/events"):
            if item["id"] == event_id:
                return dict(item)
        return None

    def demand_rows(self, station_id: str, limit: int = 2000) -> list[dict[str, Any]]:
        return list(self.get_json("/v1/demand-history", station_id=station_id, limit=limit))


# -- SSE capture ---------------------------------------------------------------


@dataclass
class SSEEvent:
    t: float  # seconds since capture start
    event: str
    data: Any


@dataclass
class SSECapture:
    """Reads /v1/stream on a daemon thread and keeps every raw line with a timestamp.

    The thread is abandoned (not joined) on stop: a blocked socket read only
    returns on the next event or the 15 s keepalive, and we do not want to wait.
    """

    base_url: str = DEFAULT_BASE_URL
    read_timeout: float | None = None
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error_body: str | None = None
    lines: list[tuple[float, str]] = field(default_factory=list)
    ended: str | None = None  # why the reader stopped, if it did
    _t0: float = 0.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _connected: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self, wait_s: float = 5.0) -> SSECapture:
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._connected.wait(wait_s)
        return self

    def _run(self) -> None:
        timeout = httpx.Timeout(10.0, read=self.read_timeout)
        try:
            with (
                httpx.Client(base_url=self.base_url, timeout=timeout) as client,
                client.stream("GET", "/v1/stream") as resp,
            ):
                self.status = resp.status_code
                self.headers = _clean_headers(resp.headers)
                if resp.status_code != 200:
                    self.error_body = resp.read().decode(errors="replace")
                    self.ended = f"http {resp.status_code}"
                    self._connected.set()
                    return
                self._connected.set()
                for line in resp.iter_lines():
                    self.lines.append((time.monotonic() - self._t0, line))
                    if self._stop.is_set():
                        self.ended = "stopped"
                        return
                self.ended = "server closed stream"
        except Exception as exc:  # noqa: BLE001 - we record whatever happens
            self.ended = f"{type(exc).__name__}: {exc}"
            self._connected.set()

    def stop(self, grace_s: float = 0.5) -> None:
        time.sleep(grace_s)
        self._stop.set()

    def snapshot(self) -> list[tuple[float, str]]:
        return list(self.lines)

    def events(self) -> list[SSEEvent]:
        """Parse the captured lines into SSE events (comments are skipped)."""
        out: list[SSEEvent] = []
        name: str | None = None
        data: list[str] = []
        t_first = 0.0
        for t, line in self.snapshot():
            if line == "":
                if data or name:
                    raw = "\n".join(data)
                    try:
                        payload: Any = json.loads(raw)
                    except ValueError:
                        payload = raw
                    out.append(SSEEvent(t_first, name or "message", payload))
                name, data = None, []
                continue
            if line.startswith(":"):
                continue
            field_name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if not name and not data:
                t_first = t
            if field_name == "event":
                name = value
            elif field_name == "data":
                data.append(value)
        return out

    def comments(self) -> list[tuple[float, str]]:
        return [(t, line) for t, line in self.snapshot() if line.startswith(":")]


# -- output helpers ------------------------------------------------------------


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n")
    return path


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]
