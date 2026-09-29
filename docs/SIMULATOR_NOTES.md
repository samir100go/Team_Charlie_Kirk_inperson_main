# Simulator Notes

What we have **confirmed by experiment** about the BUP Fuel Supply Simulator
(`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`), where it differs from the
integration guide, and what that means for our design.

## Tooling status (checked 2026-09-29)

| MCP / tool | Status | How checked |
|---|---|---|
| context7 | live | resolved `/encode/httpx`, queried streaming/timeout docs |
| gsap | live | `get_gsap_api_expert("gsap.to")` |
| magicui | live | `listRegistryItems("number-ticker")` |
| playwright | live | navigated to `about:blank` |
| GitHub MCP | not connected | `gh` CLI not installed either; using the existing `origin` repo |
| claude.ai Canva | needs auth | not needed for this project |

Local environment: Docker 28.3.3 is installed, but the daemon is stopped, the
`docker compose` / `buildx` plugins are missing, and the dev user is not in the
`docker` group. This blocks Phase 0.3–0.5 until fixed with `sudo`.

## Confirmed behaviour

_Pending: Phase 0.3–0.5 (needs a running simulator)._
