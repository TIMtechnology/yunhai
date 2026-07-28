# Changelog

## 2.0.0 — 2026-07-28

### Security

- Anonymous contribution ownership now uses a server-signed HttpOnly session cookie; forged contributor headers are ignored.
- Curated location fields are canonicalized from server data, request bodies and expensive endpoints are bounded, and public history no longer exposes internal notes or triggers reconciliation writes.
- Share pages are server-rendered with escaped content; CORS, trusted hosts, security headers, proxy-aware IP handling, rate limits, and pseudonymous analytics IPs are enforced.
- Production secrets are required at deploy time. Database snapshots and pickle model files are no longer tracked or copied into images.
- Python and npm dependencies are upgraded, locked, audited, and checked in CI.

### Reliability

- Open-Meteo races both forecast sources without cancelling a viable fallback and uses stale cache only after both fail.
- Failed elevation lookups no longer poison the cache with `0 m`.
- Past sunrise-window probabilities persist across requests instead of being recalculated from later observations.
- Whole-response prediction caching was removed so access snapshots are not skipped.

### Operations

- Production images run the web process as an unprivileged user with a read-only root filesystem template.
- Releases require a clean `main`, execute tests/audits/build budgets, create an immutable commit tag, back up data and the previous image, and automatically roll back failed health checks.
- Unvalidated ML artifacts are disabled in production; legacy pickle loading requires a separate explicit opt-in.

### Performance

- ECharts uses only required chart/components, reducing the shared chart bundle from 744.39 kB to 576.22 kB gzip.
- Bundle budgets are enforced during every frontend build.
