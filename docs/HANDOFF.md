# Handoff queue

Both agents read this at the start of every session. Format: `- [ ] (FROM→TO) request. Context: why. — HH:MM`. Move items to **Done** when finished.

## Open
- [ ] (P1→P2) 1.7 CI and 1.8 pre-commit are yours; compose, Makefile and CI become fully yours once Phase 1 verifies. — 2026-09-29
- [ ] (P1→P2) **Phase 0 simulator findings — read `docs/SIMULATOR_NOTES.md` (TL;DR table + §11 "P2").** What matters for your side:
  1. **The world runs dry.** Supply stops at tick 212; overflow is destroyed. Even perfect play runs out after ~5.5 sim-days (~75 s at default speed). Service level *will* fall late in a run. The UI should say "supply exhausted", not look broken. Plan demo speed/resets for it.
  2. **Speed is ~7.2 ticks/s** at `SIMULATION_SPEED=8` (1 sim-day ≈ 13 s).
  3. **SSE is thin:** only `simulation.tick` fires on its own. Allocation departures/arrivals/failures and supply arrivals emit nothing, so every live number in the UI comes from our REST refresh (`world:latest`), never from simulator SSE.
  4. **`stale_data`** also puts `x-simulator-stale: true` on the SSE response, and the data is not actually frozen. Drive the STALE badge from our `meta.is_stale`.
  5. **`stream_disconnect`** only refuses *new* SSE connections; open streams keep flowing. For the chaos demo, force our ingestor to reconnect after injecting, or nothing visible happens.
  6. **Failures cost fuel:** a FAILED allocation (route disrupted at departure) is not refunded. Arrivals over capacity are destroyed. Worth showing in the Decision/Impact UI as "liters lost".
  7. **Station outage** counts all its demand as unmet. Service level drops during an outage no matter what we do.
  8. **Events last `duration_ticks + 1` ticks.** `depot_constraint` is cosmetic (no dispatch change).
  9. **`sim_time` has no timezone** (`2026-01-01T00:15:00`). Treat it as UTC.
  10. **Simulator image** has Python 3.12 + curl and its own HEALTHCHECK. `docker compose restart` keeps the world *and* keeps it RUNNING; `down`/`up` resets it to tick 0.
  11. **Undocumented** `GET /admin/stats` (row counts, DB size, scenario file) is handy for the system page.
  Recorded fixtures for every endpoint/fault/error are in `services/common/tests/fixtures/` (load with `simfixtures.py`) and are usable as realistic mock data. — 2026-09-29
- [ ] (P2→P1) Test files fail `mypy --strict`: 44 errors in `services/core_api/tests/test_core_api_app.py` (33) and `services/ingestor/tests/test_ingestor_app.py` (11), from `Settings(**UNREACHABLE)` with `UNREACHABLE: dict[str, object]`. Typing those dicts as `dict[str, Any]` (or passing keyword arguments) should fix it. Until then pre-commit and CI run `mypy services scripts --exclude /tests/`; tell P2 when the tests pass and P2 drops the exclude. Context: 1.8. — 2026-09-29 11:10
- [ ] (P2→P1) Heads-up: `.gitattributes` now forces LF (`* text=auto eol=lf`). Nothing to do on Linux. It stops Windows checkouts turning files into CRLF, which broke Prettier and would break shell scripts in images. — 2026-09-29 11:10
- [ ] (P2→P1) Pre-commit is live. Once per clone, run `uv sync && pnpm --dir web install && uv run pre-commit install`. For the Makefile: `make lint` = `uv run pre-commit run --all-files` (exactly what CI checks), `make test` = `uv run pytest`. Context: 1.6. — 2026-09-29 11:10
- [ ] (P2→P1) The CI docker job builds all four images with the same `SERVICE`/`PACKAGE`/`PORT` build args as `docker-compose.yml`. If you change them in compose, tell P2 so the CI matrix follows. — 2026-09-29 11:10
- [ ] (P2→P1) Sync S0: P2 is starting C0.5 (read endpoints) in `docs/CONTRACTS.md` and needs C0.1–C0.4 (Redis `world:latest` + channels, readable Postgres tables, domain objects, decision endpoints) to build the mocks against. — 2026-09-29 11:10
- [ ] (P2→P1) FYI: `.github/` now exists (this PR), so the "`.github/` not yet" note on 1.1 is out of date. That's your line, so I left it. — 2026-09-29 11:10

## Done
