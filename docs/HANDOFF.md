# Handoff queue

Both agents read this at the start of every session. Format: `- [ ] (FROM→TO) request. Context: why. — HH:MM`. Move items to **Done** when finished.

## Open
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

## Person 2 handover — 2026-09-29

Person 2 has stopped work. **Person 1 owns everything from here, including every [P2] task in project.md.** P2's earlier requests to P1 are folded into this section. Everything below is merged into `jalani-dev` (merge commit `154c6c8`).

### Finished
- **1.8 Pre-commit** (`.pre-commit-config.yaml`): check-merge-conflict, check-yaml, check-toml, check-added-large-files (1 MB, `docs/brief/` exempt), gitleaks 8.30.1, ruff check + format, mypy --strict, eslint, prettier. Python hooks run through `uv run --frozen` and web hooks through `pnpm --dir web`, so versions come from `uv.lock` and `web/pnpm-lock.yaml`. Verified on the merged tree: every hook passes on all files, and gitleaks catches a planted token.
- **`.gitattributes`** forces LF for every text file. It stops Windows checkouts turning files into CRLF, which broke Prettier. Nothing to do on Linux.
- **Web script fixes** (`web/package.json`): `typecheck` is `next typegen && tsc --noEmit`; plain `tsc` fails on the generated `LayoutProps` global in Next 16. `lint` is `eslint --max-warnings=0`.

### Half-done
- **1.7 CI** (`.github/workflows/ci.yml`): written and merged, but P2 never saw it run on GitHub (private repo, no `gh`). It runs on push/PR to `main` and `jalani-dev` (plus manual dispatch). Jobs: `python` (uv sync --locked, ruff, mypy, pytest), `web` (eslint, next typegen + tsc, prettier), `secrets` (checksum-verified gitleaks over the full history), and `docker` (buildx build of core-api, ingestor, intelligence and web with a per-image GHA cache, no push). Every non-Docker step passes locally.
  **Left:** (1) open the Actions run started by the push of this handover to `jalani-dev`. (2) The `docker` jobs are the untested part: those images have never been built anywhere. If `web` fails, suspect `next/font/google` (see Known bugs). (3) Tick 1.7 once the run is green. (4) The matrix copies the `SERVICE`/`PACKAGE`/`PORT` build args from `docker-compose.yml`, so change both together. Phase 10 extends this workflow (compose + simulator, Playwright, k6, Trivy).
- **mypy on test files:** the hook and CI run `mypy services scripts --exclude /tests/`. Including tests gives 44 errors, all in `services/core_api/tests/test_core_api_app.py` (33) and `services/ingestor/tests/test_ingestor_app.py` (11), from `Settings(**UNREACHABLE)` with `UNREACHABLE: dict[str, object]`. Your new test code type-checks cleanly.
  **Left:** type those dicts as `dict[str, Any]` (or pass keyword arguments), then delete `--exclude /tests/` in both `.pre-commit-config.yaml` (mypy hook `entry`) and `.github/workflows/ci.yml` (mypy step).

### Mocked data still to switch to real
- **None.** P2 built no mocks and no data-driven UI. C0.6 (mock layer) was not started. The web app is still the static placeholder in `web/src/app/page.tsx`: a "SIMULATED ENVIRONMENT" badge and a title, with no numbers. `web/src/app/api/version/route.ts` reports `unknown`/`dev` unless `GIT_SHA`, `BUILD_TIME` and `IMAGE_TAG` are set (Docker build args); those are defaults, not fake data. For future mocks, your recorded fixtures in `services/common/tests/fixtures/` (via `simfixtures.py`) are the realistic source.

### Not started (P2 tasks now yours)
- **C0.5** read-endpoint contracts in `docs/CONTRACTS.md` (the file doesn't exist yet), **C0.6** mock layer (MSW or `MOCK_MODE=true` in core-api), **C0.7** `openapi-typescript` type generation in CI.
- Phase 3 (read API, WebSocket, auth), Phase 6 (6.1 design system, top bar, degraded banner, Command Center and the other pages), the [P2] rows of Phases 7–11, and §11.3 backup plan.

### Known bugs and gotchas
- **Web build needs internet.** `web/src/app/layout.tsx` loads Geist through `next/font/google`, which downloads fonts at build time. That breaks the offline-demo rule (project.md §11.3) and can fail the CI/Docker web build if Google Fonts is unreachable. Fix: self-host the font (`next/font/local`, or the `geist` npm package).
- **`web/README.md`** is still the create-next-app boilerplate.
- **Windows clones made before `.gitattributes`** keep CRLF files, and Prettier's check fails on them locally until they are re-checked out. After committing your work: `git rm --cached -r -q . && git reset --hard`. Linux and CI are unaffected.
- To skip one hook in an emergency, use `SKIP=mypy git commit …`, not `--no-verify`, which skips gitleaks too.

### Setup on your machine
- **Toolchain:** Node 24 LTS, pnpm 10.34.6 (`packageManager` in `web/package.json`; `corepack enable pnpm` or `npm i -g pnpm@10.34.6`), and uv (Python 3.12 via your `.python-version`).
- **Once per clone:** `uv sync && pnpm --dir web install && uv run pre-commit install`. The first hook run builds gitleaks with Go (pre-commit downloads Go itself), which takes about a minute.
- **Run what CI runs:** `uv run pre-commit run --all-files && uv run pytest`. These would make good `make lint` and `make test` targets (Phase 1.6).
- **Web scripts** (`pnpm --dir web <script>`): `dev` (http://localhost:3000), `build`, `start`, `lint`, `typecheck`, `format` (writes), `format:check`.
- **Env vars:** P2 added none. The web app reads only `GIT_SHA`, `BUILD_TIME`, `IMAGE_TAG` (optional, for `/api/version`). **No mock-mode flag exists yet** (C0.6 not started).
- **CI** needs no secrets; it only uses the built-in `GITHUB_TOKEN` (GHA cache, gitleaks download).

## Done
- [x] (P2→P1) mypy on test files: `UNREACHABLE` typed as `dict[str, Any]`; `--exclude /tests/` removed from `.pre-commit-config.yaml` and `ci.yml`. `mypy services scripts` is clean (39 files). — 2026-09-29
- [x] (P1) Phase 1 verified with `make up` (all containers healthy, observability wired). New: `.env.example` + `make .env`, full Makefile, `docker-compose.override.demo.yml` (`make demo`). Compose fixes: `tempo-init` on busybox, Tempo not published on the host (Windows reserves 3188-3287), core-api host port via `CORE_API_PORT` (default 8080). Windows clone line endings renormalized per the handover. — 2026-09-29
- [x] (P1→P2) 1.7 CI and 1.8 pre-commit are yours; compose, Makefile and CI become fully yours once Phase 1 verifies. — 2026-09-29 → 1.8 done. 1.7 written and merged but not yet green on GitHub (see Person 2 handover). Compose, Makefile and `.env.example` were never handed over, so they stay with P1.
