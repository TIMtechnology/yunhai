# Changelog

## 2.2.0 — 2026-08-07

### Prediction quality

- Five-Woman Mountain now samples four reviewed low-terrain cells along the sunrise valley using the same D-1 ECMWF IFS HRES 00Z run as point inference and training. A clear summit above saturated, low-visibility valley cells is recognized as spatial-difference cloud sea evidence.
- Live inference and offline replay now share one canonical post-rule policy, eliminating drift in observational factors, plausibility caps, water context, and ML fusion.
- The promoted v2 point model uses 77 reconstructable labels and a fixed UI-aligned 0.50 threshold. On the latest untouched 15-day holdout it achieved 0.652 balanced accuracy, 0.875 recall, 0.737 F1, and 0.233 Brier score; all promotion gates passed.
- Spatial evidence improved the same holdout by 6.25 percentage points balanced accuracy, 12.5 points recall, and 0.031 Brier. The prior chronological window remained stable at 0.900 balanced accuracy and 1.000 recall.

### AI advisory

- DeepSeek receives server-owned GCJ-02 and WGS84 coordinates, viewpoint elevation, regional terrain, nearby named water bodies, summit-to-water height difference, and available spatial valley observations.
- The prompt requires an independent terrain-and-water assessment and an explicit agreement or disagreement with the platform verdict; merely restating rule or ML scores is prohibited.

### Operations

- Prediction metadata reports whether spatial meteorology is active or has fallen back to point-only input, including source, sample count, and available hours.
- Exact spatial forecasts are archived separately for reproducible retraining and historical replay. Fetch and archive failures produce bounded operational warnings without exposing coordinates or credentials.
- Baked terrain snapshots are now generated and resolved in WGS84, matching weather-provider coordinates.

## 2.1.1 — 2026-08-07

### Prediction reliability

- Five-Woman Mountain operational inference now uses the same ECMWF IFS HRES 00Z single run as the reviewed training archive for both the rule engine and ML input.
- Exact-run inputs are accepted only when all 12 precursor hours and required surface fields are present; otherwise prediction safely falls back to the rule engine without mixing forecast products.
- Prediction snapshots record the exact model run and input rows, and historical replay rejects archives whose source is not the expected ECMWF run.
- The five newly labelled positive days were evaluated with a chronological holdout. The 77-day candidate failed the production quality gate, so the reviewed 72-day model remains active.

## 2.1.0 — 2026-07-28

### Operational ML

- Five-Woman Mountain now uses a reviewed point model trained from exact ECMWF IFS HRES 00Z single runs that were available before the D-1 18:00 decision cutoff.
- The fixed 14-feature logistic model uses time-ordered out-of-fold Platt calibration. Its final 15-day holdout achieved 0.65 balanced accuracy, 1.00 recall, and 0.209 Brier score versus the rule baseline's 0.50, 0.60, and 0.250.
- Model artifacts are non-executable JSON and load only after strict schema, point identity, finite-number, source, chronological-evaluation, and quality-gate validation.
- Admin retraining produces a candidate only. Production activation requires review, CI, an immutable image, backup, and smoke tests.

### Data quality

- Forecast archive backfill now uses the Single Runs API and records the exact ECMWF run. The former Historical Forecast backfill could no longer be mislabeled as an as-issued forecast.
- Training uses only the surface fields consistently available across the long-term HRES archive; unavailable pressure-level values and constant point features are excluded.
- The Five-Woman Mountain terrain and sunrise-sector profile is baked into the image so rule comparison and production inference use the same auditable DEM and local-water context without a runtime DEM dependency.

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
