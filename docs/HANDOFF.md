# Handoff queue

Both agents read this at the start of every session. Format: `- [ ] (FROM→TO) request. Context: why. — HH:MM`. Move items to **Done** when finished.

> **Closed 2026-09-29 (v0.5.0): solo mode.** Person 1 owns every task; HANDOFF requests no longer apply. The Person 2 handover's open items now live as notes on the matching tasks in `project.md` (1.7, 1.8, C0.6, Phase 10 version footer, Phase 11 README, 11.3 offline fonts, tooling line). This file is kept as history.

## Open
_(none — closed)_

## Done
- [x] (P1→P2) Phase 0 simulator findings shared. Closed with solo mode; the findings live in `docs/SIMULATOR_NOTES.md` (TL;DR + §11). — 2026-09-29
- [x] (P2→P1) Person 2 handover received and folded into `project.md`. Summary: 1.8 pre-commit, `.gitattributes` and web script fixes done; 1.7 CI written, awaiting a green run; C0.5–C0.7, Phase 3, Phase 6 and the P2 rows of 7–11 not started; known bugs: `next/font/google` needs internet at build, `web/README.md` is boilerplate. — 2026-09-29
- [x] (P2→P1) mypy on test files: `UNREACHABLE` typed as `dict[str, Any]`; `--exclude /tests/` removed from `.pre-commit-config.yaml` and `ci.yml`. `mypy services scripts` is clean (39 files). — 2026-09-29
- [x] (P1) Phase 1 verified with `make up` (all containers healthy, observability wired). New: `.env.example` + `make .env`, full Makefile, `docker-compose.override.demo.yml` (`make demo`). Compose fixes: `tempo-init` on busybox, Tempo not published on the host (Windows reserves 3188-3287), core-api host port via `CORE_API_PORT` (default 8080). Windows clone line endings renormalized per the handover. — 2026-09-29
- [x] (P1→P2) 1.7 CI and 1.8 pre-commit are yours; compose, Makefile and CI become fully yours once Phase 1 verifies. — 2026-09-29 → 1.8 done. 1.7 written and merged but not yet green on GitHub (see Person 2 handover). Compose, Makefile and `.env.example` were never handed over, so they stay with P1.
