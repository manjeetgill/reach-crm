# Local end-to-end verification

Run on 5 October 2026 against an isolated SQLite database, a real FastAPI process, the outbound worker, a synthetic local gateway, and a separate Next.js browser session. The user's local CRM database and browser tab were left untouched.

## Results

- `python scripts/run_e2e.py`: **30/30 checks passed**. The runner creates a disposable database, applies all Alembic migrations, starts the API and local gateway on temporary loopback ports, exercises HTTP and worker workflows, and shuts them down.
- `cd apps/api && ../../.venv/bin/pytest -q`: **16 passed**. This includes a fresh install migration test and a check that Alembic sees no schema drift.
- `cd apps/web && npm run lint && npm run build`: **passed**.
- Browser session against an isolated web/API copy: workspace discovery and sign-in, overview, cited knowledge answer, conversation reply suggestion, campaign draft builder, survey builder and report, survey response, contact profile and consent history, follow-ups, analytics answer, data hygiene, usage, workspace page, and manual escalation through resolution all rendered and worked.

The HTTP suite covers tenant isolation, RBAC, contact CSV import and search, consent, approval-gated knowledge retrieval and abstention, web chat, signed inbound WhatsApp and voice callbacks, outbound queue and delivery status, opt-out cancellation, campaign review and consent filtering, survey aggregation, follow-up scanning, data hygiene review, controlled analytics, usage metering, audit logs, and agent runs.

## Fixes found during verification

- Froze the Phase 1 Alembic schema in `0001_initial.py`; fresh databases now migrate cleanly through the later revisions.
- Added a stable key to the sign-in/create-workspace form to avoid React's uncontrolled input warning when changing modes.
- Made the sidebar scroll independently so navigation remains accessible at shorter window heights.

## Scope limits

- The gateway was a local fake. This run did not verify a real WhatsApp provider, voice call engine, or external delivery network.
- Retrieval was tested in local extractive mode. The NVIDIA Nemotron Ultra endpoint was not called, because external model access has not been enabled for CRM data.
- The suite used a small synthetic dataset. It did not load test the 100,000-contact target or prove production performance, operational security, or deployment readiness.
- Billing and real payments are outside the implemented local CRM scope.

To rerun the synthetic HTTP suite from the repository root, install API dependencies in `.venv` and run `.venv/bin/python scripts/run_e2e.py`. It needs permission to bind temporary `127.0.0.1` ports. It does not use the local preview database or any external service.
