#!/usr/bin/env python3
"""Create .env from .env.example, filling every blank required secret with a random value.

Never overwrites an existing .env (delete it first to regenerate).

    python scripts/make_env.py
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = (
    "POSTGRES_PASSWORD",
    "JWT_SECRET",
    "GRAFANA_ADMIN_PASSWORD",
    "OPERATOR_PASSWORD",
    "ADMIN_PASSWORD",
)


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; leaving it alone")
        return 0
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SECRETS and not value:
            line = f"{key}={secrets.token_urlsafe(32)}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote .env with random {', '.join(SECRETS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
