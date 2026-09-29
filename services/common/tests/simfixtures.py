"""Loader for the simulator contract fixtures in ./fixtures (see fixtures/README.md).

A recorded fixture (fixtures/<name>.json, from `make sim-probe`) always wins over
a doc-derived one (fixtures/doc_derived/<name>.json). Doc-derived fixtures only
remain for cases a live simulator cannot produce.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DOC_DERIVED_DIR = FIXTURES_DIR / "doc_derived"


def names() -> list[str]:
    """Every fixture name, recorded and doc-derived, without the .json suffix."""
    found = {p.stem for p in FIXTURES_DIR.glob("*.json")}
    found |= {p.stem for p in DOC_DERIVED_DIR.glob("*.json")}
    return sorted(found)


@cache
def _load(name: str) -> dict[str, Any]:
    for path in (FIXTURES_DIR / f"{name}.json", DOC_DERIVED_DIR / f"{name}.json"):
        if path.exists():
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return data
    raise FileNotFoundError(f"no fixture named {name!r} in {FIXTURES_DIR}")


def load(name: str) -> dict[str, Any]:
    """The whole fixture: {"_meta", "request", "response"} (or {"_meta", "event", "data"})."""
    return _load(name)


def response(name: str) -> dict[str, Any]:
    resp: dict[str, Any] = _load(name)["response"]
    return resp


def body(name: str) -> Any:
    """The response JSON body of a fixture."""
    return response(name)["json"]


def source(name: str) -> str:
    return str(_load(name)["_meta"]["source"])
