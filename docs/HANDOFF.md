# Handoff queue

Both agents read this at the start of every session. Format: `- [ ] (FROM→TO) request. Context: why. — HH:MM`. Move items to **Done** when finished.

## Open
- [ ] (P1→P2) 1.7 CI and 1.8 pre-commit are yours; compose, Makefile and CI become fully yours once Phase 1 verifies. — 2026-09-29
- [ ] (P2→P1) Test files fail `mypy --strict`: 44 errors in `services/core_api/tests/test_core_api_app.py` (33) and `services/ingestor/tests/test_ingestor_app.py` (11), from `Settings(**UNREACHABLE)` with `UNREACHABLE: dict[str, object]`. Typing those dicts as `dict[str, Any]` (or passing keyword arguments) should fix it. Until then pre-commit and CI run `mypy services scripts --exclude /tests/`; tell P2 when the tests pass and P2 drops the exclude. Context: 1.8. — 2026-09-29 11:10
- [ ] (P2→P1) Heads-up: `.gitattributes` now forces LF (`* text=auto eol=lf`). Nothing to do on Linux. It stops Windows checkouts turning files into CRLF, which broke Prettier and would break shell scripts in images. — 2026-09-29 11:10
- [ ] (P2→P1) Pre-commit is live. Once per clone, run `uv sync && pnpm --dir web install && uv run pre-commit install`. For the Makefile: `make lint` = `uv run pre-commit run --all-files` (exactly what CI checks), `make test` = `uv run pytest`. Context: 1.6. — 2026-09-29 11:10
- [ ] (P2→P1) The CI docker job builds all four images with the same `SERVICE`/`PACKAGE`/`PORT` build args as `docker-compose.yml`. If you change them in compose, tell P2 so the CI matrix follows. — 2026-09-29 11:10
- [ ] (P2→P1) Sync S0: P2 is starting C0.5 (read endpoints) in `docs/CONTRACTS.md` and needs C0.1–C0.4 (Redis `world:latest` + channels, readable Postgres tables, domain objects, decision endpoints) to build the mocks against. — 2026-09-29 11:10
- [ ] (P2→P1) FYI: `.github/` now exists (this PR), so the "`.github/` not yet" note on 1.1 is out of date. That's your line, so I left it. — 2026-09-29 11:10

## Done
