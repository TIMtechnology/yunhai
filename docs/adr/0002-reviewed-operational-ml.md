# ADR 0002: Reviewed operational point models

- Status: Accepted
- Date: 2026-07-28

## Context

The UI reported 73 training-eligible labels for Five-Woman Mountain, but production
correctly remained rule-only after the unsafe legacy Pickle models were disabled.
The former archive backfill used Open-Meteo Historical Forecast, then assigned an
arbitrary D-1 18:00 issue time. That stitched product does not prove which forecast
was available at the decision cutoff. The former LOOCV pipeline also selected
features, regularization, calibration, and thresholds using the same observations
reported as evaluation results.

## Decision

1. Reconstruct each label day from the ECMWF IFS HRES 00Z Single Run initialized on
   D-1. This run precedes the D-1 18:00 China Standard Time decision cutoff.
2. Use only the 14 surface and precursor features consistently available in the
   long-term archive. Do not impute missing pressure levels into the reviewed model.
3. Reserve the last 15 usable dates as an untouched chronological holdout.
   Hyperparameters are fixed. Platt calibration and the decision threshold use only
   time-ordered out-of-fold predictions inside the training period.
4. Compare the production ML/rule blend against both a majority prior and the rule
   engine using the same archived surface rows, baked DEM/viewing mode, and curated
   local-water context. Activation requires all explicit quality checks to pass.
5. Serialize only finite scaler and logistic coefficients to strict JSON. The loader
   verifies schema, point identity, features, source, protocol, metrics, and gate
   evidence. Legacy Pickle loading remains disabled.
6. Bake reviewed models into the read-only image. Admin may create a candidate in
   the data volume, but cannot hot-replace the active model.

## Evidence

The reviewed Five-Woman Mountain model uses 72 reconstructable dates (22 positive,
50 negative). The chronological holdout covers 2026-06-11 through 2026-07-03 with
5 positive and 10 negative dates. The rule baseline used the baked point terrain
snapshot (`valley_fill`, 5 km maximum elevation 753 m) and the curated Huanlong
Lake context.

| Metric | Reviewed blend | Rule baseline | Majority prior |
| --- | ---: | ---: | ---: |
| Balanced accuracy | 0.65 | 0.50 | 0.50 |
| Recall | 1.00 | 0.60 | 0.00 |
| F1 | 0.588 | 0.429 | 0.00 |
| Brier | 0.209 | 0.250 | 0.223 |

## Consequences

- Five-Woman Mountain can safely activate ML while other points remain rule-only
  until their own exact-run data and time holdout pass the same gate.
- Historical headline LOOCV values are retained only as historical context and are
  not used to justify production activation.
- A new candidate requires an image release and rollback point; online model hot
  patches are intentionally unsupported.
