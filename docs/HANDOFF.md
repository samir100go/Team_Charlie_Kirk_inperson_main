# Progress

**2026-09-29 · Items 2-4 + 7 merged (resilience, status, operator UI, security).** All four brief §11 rows implemented, visible (Resilience panel, alerts, banners) and triggerable (admin buttons); System Status panel (§15); console adds network map, supply, disruptions, alerts, decision history; Magic UI + GSAP polish.
Security: operator/admin login (env passwords, httpOnly cookie), approvals need operator, failure injection needs admin, validated inputs; 95 tests, `make up` healthy.

**2026-09-29 · Item 1 Intelligence merged.** Prediction service (structural prior + online level + regime/spike detection, event-aware, P10/P50/P90, stockout probability/time, confidence); backtest on recorded simulator data: MAPE 4.5–5.7 % in every period (vs 67 % profile-only during a ×3 spike), spike detected on its first tick.
Decision engine: every recommendation shows why, signals, constraints (binding marked), impact before→after (incl. unmet liters), confidence, alternatives; confidence < 60 % requires human review (UI + API enforced).

**2026-09-29 · Demo fixes merged to main.** "3× rates" was a leftover ×3 test spike (rates match the documented profile within noise on a clean world); recommendations now fit each depot's per-tick dispatch capacity (most urgent first, rest "next tick"); cover math shown in each reason.
Grafana "JALANI Overview" provisioned as home, anonymous viewer; new core-api metrics (sim calls/failures, availability, data age, tick, service level, unmet liters). Makefile: sim-status, demo-spike, demo-fault, demo-clear.
Verified: 70 tests, web lint/typecheck/build, `make up` all healthy, Playwright: every approve accepted, fault banner + recovery, Grafana 11 panels with data.

**2026-09-29 · Thin demo slice on real data (30-min deadline), merged to main.** core-api polls the simulator (timeout + 1 retry, last-good cache), recommends refills by hours-to-stockout; web page shows KPIs, risk table, Approve → `/v1/allocations`, cached-data banner.
Verified: `make up` healthy, 65 tests, Playwright: approve → PENDING → IN_TRANSIT, error_rate fault → banner, clear → LIVE.
Next: `docs/DEMO_SCRIPT.md`; rule doesn't yet account for per-tick depot dispatch cap (second approve on the same depot can get 409 DISPATCH_CAPACITY_EXCEEDED, shown in the UI).

**2026-09-29 · Phase 1 done (v0.5.0 + 857ff74).** Stack verified with `make up`; CI green on GitHub; fonts self-hosted.
Plan switched to solo mode: C0 lean (C0.6 skipped, C0.7 → Phase 10); build order C0 → 2 → 3 (+ first live Command Center) → 4 → 5 → 6 → 7–11.
Next: waiting for the hours-left number to fix the cut line, then C0 contracts and Phase 2.

---

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
