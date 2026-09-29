# Load tests

k6 scripts for `make loadtest` (Phase 9). The `k6` compose service (profile `loadtest`) mounts `loadtest/k6` at `/scripts` and writes results to Prometheus.
