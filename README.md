# Reach CRM

A tenant-aware engagement CRM foundation for organizations that need to manage contacts and conversations across channels.

## Implemented

- Workspace creation, login, JWT authentication, roles, and tenant-scoped queries
- Contacts, CSV imports (10,000 rows per batch), search, profiles, and interaction history
- Consent history with outbound opt-in enforcement
- Conversations, messages, signed WhatsApp and voice inbound/delivery webhooks, and idempotent event handling
- Durable outbound queue with retrying worker and consent recheck before dispatch
- Tenant-scoped knowledge documents, approval workflow, cited retrieval answers, and knowledge-agent run history
- Reviewed campaigns with recipient consent checks, survey builder and reports, follow-up tasks, template content drafts, and controlled analytics answers
- Reviewable data hygiene findings, automatic and manual human escalations, 14-day dashboard trends, workspace discovery, and usage metering
- Audit logs and overview metrics
- Responsive Next.js dashboard

The outbound worker calls a configured engine's `POST /messages` with a bearer token and idempotency key. Jobs retry and eventually fail if the engine is unavailable. See [the architecture notes](docs/architecture.md) for the provisional integration contract and remaining production work. WhatsApp campaigns require a configured engine; web campaigns write to web conversations after an owner/admin explicitly starts an approved campaign.

## Start locally

For the existing Mac preview, run `.venv/bin/python scripts/start_local.py` from the repository root and keep the terminal open. Open `http://localhost:13000/`. This starts the web app, API, and worker with the existing `/private/tmp/reach-crm-seed.db` database and the matching API address. The local preview keeps external model and channel engines disabled. Restart this command after closing the terminal or restarting the computer.

For a new Docker installation:

1. Copy `.env.example` to `.env` and change all secrets and the seed password.
2. Run `docker compose up --build`.
3. Open `http://localhost:3000`. API docs are at `http://localhost:8000/docs`.

The first user creates a workspace in the UI. Keep its workspace ID for subsequent sign-ins; it appears in the sidebar and is stored in the browser on that device.

## Knowledge and RAG

Open **Knowledge** in the dashboard, paste text or upload a PDF, DOCX, TXT, Markdown, or HTML file, approve it as an owner/admin, then ask a question. Drafts and archived documents are excluded from retrieval. Answers include document/version citations, and updating a document requires reapproval. Each query is recorded as a knowledge-agent run. Uploads are limited to 5 MB and 200,000 extracted characters.

Without a model endpoint, the local app returns a cited source excerpt (`extractive` mode). To generate new wording from retrieved passages, configure `RAG_MODEL_BASE_URL`, `RAG_MODEL_NAME`, and either `RAG_MODEL_API_KEY` or `RAG_MODEL_KEY_FILE` in the API environment. The endpoint must implement OpenAI-compatible `POST /chat/completions`. `RAG_MODEL_KEY_FILE` reads the `NVIDIA_API_KEY` field from a private JSON config on the local host; for Docker, mount that file into the API container or use the private `.env` key field. The model must return JSON claims with passage IDs; unknown citations or weakly grounded claims are rejected. The API key is server-side only.

In **Conversations**, open a thread with an incoming message and select **Suggest reply from knowledge**. The cited result stays a draft until an operator selects **Use as draft** and sends it. Nemotron Ultra uses `nvidia/nemotron-3-ultra-550b-a55b` and NVIDIA's OpenAI-compatible endpoint when configured.

Retrieval currently uses tenant-filtered lexical matching. Embeddings, reranking, upload malware scanning, OCR, and a large-scale knowledge index remain future work. Scanned PDFs without extractable text are rejected. No claim of semantic vector search or production RAG quality is made yet.

## Phase 4 workflows

Use **Campaigns** to create a draft for selected contacts, submit it for review, and have an owner/admin approve and start it. Only contacts with current opt-in for the selected channel receive a message. Content drafts use a deterministic template and remain editable; they do not call an external AI model. WhatsApp dispatch still depends on a separately configured engine.

Use **Surveys** to create text, choice, and rating questions, publish the survey, record one response per contact, and see aggregate counts. **Follow-ups** lists tasks for inbound messages left unanswered for 24 hours; the worker scans every minute, and the page has a manual scan button. **Analytics** answers supported count questions through controlled queries, with no model-generated SQL. These workflows are recorded as agent runs where applicable.

## Phase 5 workflows

**Data hygiene** scans for duplicate email/phone details, invalid phone lengths, and contacts not updated for over a year. Findings are suggestions; the scan does not merge or delete contacts. Staff can dismiss findings, and a later scan resolves findings that no longer apply.

**Escalations** creates a human task when an incoming message explicitly requests a person or uses an urgent safety term. Staff can also escalate a conversation manually, open the conversation from the escalation queue, and resolve the task. Classification is rule based; it does not make a safety decision for staff.

The **Overview** shows 14-day interaction trends, channel distribution, open tasks, escalations, and delivery counts. **Analytics** accepts supported natural-language questions about these controlled metrics; it never runs generated SQL. **Usage** shows measured events, messages, agent runs, and knowledge queries. Billing, prices, invoices, and payments are not configured. At sign-in, **Find my workspaces** verifies the entered email and password and lists matching organizations; each workspace remains isolated.

## Development

Backend: `cd apps/api && python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --reload`

Worker: from `apps/api`, run `.venv/bin/python -m app.worker` with the same `DATABASE_URL` as the API. The worker is started automatically by Compose.

Frontend: `cd apps/web && npm install && npm run dev`

Checks: `cd apps/api && .venv/bin/pytest -q`; `cd apps/web && npm run lint && npm run build`.

Synthetic end-to-end check: from the repository root, run `.venv/bin/python scripts/run_e2e.py`. It migrates a disposable SQLite database, starts an isolated API and fake channel gateway on temporary loopback ports, exercises HTTP and worker flows, then cleans up. See [the verification report](docs/e2e-results.md) for results and scope limits.

Demo data: `docker compose exec api python -m app.seed` creates an idempotent demo organization with 1,000 synthetic contacts, sample consent, and conversations. Set `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` in `.env` first. The command prints the workspace ID for login.

For local Python development without PostgreSQL, set `DATABASE_URL=sqlite:///./crm.db` before migrating. Production should use PostgreSQL and a secret manager. Do not use the development JWT default for deployment.

## WhatsApp agent platform

Open **WhatsApp agent** for platform status, approved-knowledge reply preview, and channel preparation/activation. The integration reuses the AI Agent Platform already used by Sahayak. [Setup and verification](docs/whatsapp-agent-platform.md) explains the separate Reach identity, signed callbacks, and live connection requirements.

## Platform-owner dashboard

Open `/platform` for the separate platform sign-in and cross-workspace dashboard. Customer accounts remain at `/`. Platform access is explicitly granted to an existing account; customer `owner` and `admin` roles do not grant platform access.

```sh
DATABASE_URL=sqlite:////path/to/crm.db .venv/bin/python scripts/grant_platform_owner.py --organization-id WORKSPACE_ID --email OWNER_EMAIL
```

Apply migrations through `0008_platform_dashboard` before using it. The grant command is intended for the trusted server operator and records an audit event. Sign in with that account's existing email/password; no workspace ID is needed on the platform sign-in page. Platform tokens use a separate scope and a two-hour session stored in the platform tab. Grant revocation or disabling the underlying account blocks existing platform sessions on their next request.

The dashboard includes global workspace/member/contact totals, search by name/owner email/workspace ID, onboarding filters, a WhatsApp setup queue, paginated customer listings, and workspace management. Platform owners can rename workspaces, record onboarding stages, and maintain private platform notes. Changes are audited. Customer APIs do not expose those notes. Onboarding stage is operational tracking, not account suspension or agent activation. The WhatsApp indicator reports Reach's saved request/callback state, not live provider delivery. The dashboard does not impersonate customer accounts or expose their message contents, secrets, or contact records.

Local preview: `http://localhost:13000/platform`. Platform access has been explicitly granted to the original local `demo@example.com` account, while the sample-client account remains a customer-only owner.
