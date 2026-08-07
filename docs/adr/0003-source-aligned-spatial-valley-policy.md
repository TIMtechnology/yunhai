# ADR 0003: Source-aligned spatial valley policy

- Status: Accepted
- Date: 2026-08-07
- Supersedes: the calibration and point-only replay details in ADR 0002

## Context

Five-Woman Mountain is a valley-fill viewpoint: the summit may be clear while fog or
low cloud occupies lower terrain near Huanlong Lake. A single summit model cell can
therefore report low cloud near zero and still miss an observable cloud sea. The
72-day reviewed model aligned the point forecast source, but training replay and
live post-processing still duplicated policy code, and neither used exact-run
weather from lower valley cells.

Recent labels exposed this failure mode on 2026-08-07. Summit conditions were
clear enough to observe the valley, while lower cells were saturated, nearly fully
covered by low cloud, and had roughly 60–140 m visibility.

## Decision

1. For explicitly reviewed valley-fill points, sample four low-terrain cells at
   approximately 2.5, 5, 7.5, and 10 km along the sunrise profile. Other points are
   unchanged until they receive their own review.
2. Use the same ECMWF IFS HRES D-1 00Z Single Run for summit features, valley cells,
   training, replay, and live inference. Archive spatial input separately with its
   source and issue time; never mix it with Open-Meteo best-match input for the
   reviewed point model.
3. Treat a sufficiently clear summit above saturated, low-visibility, high-low-cloud
   valley cells as spatial-difference evidence. This evidence can establish the
   reviewed Type-A cloud-sea floor, while the existing plausibility limits and ML
   fusion still apply.
4. Share one canonical post-rule policy between replay and production, including
   observability, water signal, archetype, plausibility cap, and ML fusion.
5. Use an uncalibrated fixed-C logistic model with decision threshold 0.50. The
   former small-sample Platt mapping placed its reviewed threshold away from the UI's
   fixed 50% decision boundary and produced a training-to-product decision mismatch.
6. Promote only when the latest chronological holdout beats the rule and majority
   baselines, contains both classes, meets minimum balanced accuracy and recall, and
   remains stable on the preceding chronological window.
7. Report `active` versus `fallback_point_only` spatial status in prediction metadata.
   External spatial failure must preserve point-only availability and emit a bounded
   warning without coordinates or credentials.

## Evidence

The v2 artifact uses 77 reconstructable labels (27 positive, 50 negative). Its last
15 dates, 2026-06-23 through 2026-08-07, remain untouched by final fitting and contain
8 positive and 7 negative observations.

| Metric | v2 production blend | Same policy without spatial input | Rule baseline |
| --- | ---: | ---: | ---: |
| Balanced accuracy | 0.652 | 0.589 | 0.518 |
| Recall | 0.875 | 0.750 | 0.750 |
| F1 | 0.737 | 0.667 | 0.632 |
| Brier (lower is better) | 0.233 | 0.265 | 0.246 |

The spatial ablation changes one holdout decision, 2026-08-07, and improves balanced
accuracy by 6.25 percentage points, recall by 12.5 points, and Brier by 0.031. The
preceding independent 15-date window (2026-06-01 through 2026-06-22) achieves 0.900
balanced accuracy, 1.000 recall, and 0.140 Brier, satisfying the rolling-stability
check. These results support a targeted spatial correction, not a general upward
score adjustment.

## Alternatives considered

- Keep point-only inference: simpler, but it preserves the observed summit/valley
  representation error.
- Return to Open-Meteo best-match inference: useful for the general five-day UI,
  but it recreates the reviewed model's training–inference source mismatch.
- Query arbitrary nearby grids for every site: broader coverage, but unsafe without
  site-specific terrain direction, water context, and validation.
- Let DeepSeek override numerical predictions: rejected because the LLM is not a
  calibrated forecast model. It instead receives trusted geography and must produce
  an explicit independent qualitative assessment alongside the platform result.

## Consequences

- Five-Woman Mountain can detect a clear-summit/cloudy-valley pattern that point-only
  input misses, while all other sites retain existing behavior.
- Spatial API availability adds latency and an external dependency; inference safely
  falls back to point-only mode and exposes that state to operators and clients.
- Future retraining requires exact spatial archives in addition to point archives.
- DeepSeek receives richer server-owned geography, but the prompt prohibits invented
  water bodies and continues to present AI output as advisory rather than a promise.

## Rollback

Deploy the previous immutable image/model artifact. The new archive table is additive
and can remain in the database; disabling `spatial_valley_meteo` for the curated point
also restores point-only behavior without deleting data.
