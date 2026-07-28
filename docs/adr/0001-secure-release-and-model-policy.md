# ADR 0001: Secure release and model promotion policy

- Status: Accepted
- Date: 2026-07-28

## Context

The public repository contained production-derived SQLite snapshots and executable pickle model artifacts. The release script retrained oracle-mode models during deployment, used a mutable image tag, and had no mandatory dependency audit, backup, or automatic rollback. Existing point models also had too little temporally independent data to justify production activation.

## Decision

1. Production uses the deterministic rule engine until a model passes a separate promotion review.
2. A model candidate must be trained from operationally available forecast inputs, evaluated with time-ordered/out-of-period validation, report calibration and monthly stability, and have a defensible sample-to-feature ratio.
3. Database snapshots and model binaries are ignored by Git and Docker build context. Legacy pickle loading is off unless an operator explicitly enables both ML and the legacy unsafe-format switch.
4. Releases run only from a clean `main`, using locked dependencies and mandatory tests, vulnerability audits, type checks, and bundle budgets.
5. Each image receives a Git-derived immutable tag. Deployment backs up the compose configuration, restricted secret files, databases, and previous image before restart; a failed health check restores the previous image automatically.
6. The application process runs as UID/GID 10001 after the entrypoint prepares writable volumes. The production compose template binds only to loopback and uses a read-only root filesystem.

## Consequences

- ML predictions are temporarily unavailable in production, while the rule engine remains fully functional.
- Old anonymous contributor local IDs are not accepted as authentication; browsers receive a new signed session.
- Historical database/model removal requires a coordinated public-history rewrite and force push after production secrets are rotated.
- Model promotion becomes a deliberate release artifact rather than a side effect of deployment.

## Rollback

The deployment script stores the prior image and database copies in `/opt/yunhai/backups/<timestamp>`. On health-check failure it retags the prior image as `yunhai:latest` and recreates the application service.
