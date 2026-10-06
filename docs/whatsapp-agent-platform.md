# WhatsApp agent through the existing AI Agent Platform

Reach now has a **WhatsApp agent** navigation item, connection diagnostics, a knowledge reply preview, verified-number preparation, and enable/pause controls for owners and admins.

The platform source was found at `/Users/manjeet/Documents/ChatGPT/Algo/AI-Agent-Platform`. Sahayak points at port 4065, which was offline during inspection; the same platform's local console was responding on port 4061. Reach uses a separate application identity and never imports Sahayak's channel credentials.

## Integration contract

The adapter uses the platform's existing `/v1/whatsapp` API and signed `SAHAYAK` workflow transport. These names are compatibility details: the wire field `clinic_id` maps to the Reach organization ID. No clinic workflow or Sahayak data is used.

`POST /integrations/agent-platform/whatsapp` checks the application, channel, organization, timestamp and HMAC signature. It records a durable receipt before processing. Exact completed retries return the stored reply; changed content and uncertain outcomes require review. Replies are stored as `handed_to_platform`, not delivered, and never enter Reach's generic outbound queue. The platform is the sole dispatcher for these replies.

New contacts get a START consent prompt; STOP records opt-out. Existing opt-outs receive no automatic replies. Human handoff pauses local automation. Questions use only approved workspace knowledge, with citations retained in the run trace; unsupported answers become escalation tasks. The current local launcher retains extractive RAG and does not enable external NVIDIA calls.

## Reach-only connection setup

All integration code and configuration changes belong to Reach CRM. The connector does not read or write the platform database, modify its source/configuration, or provision applications or credentials. The previously proposed provisioning step was removed before it was executed.

Use an existing platform application credential authorized for Reach, with channel read/write permissions and its matching external-user identity. Do not reuse Sahayak's identity, which would point Reach at the clinic's resources.

```sh
.venv/bin/python scripts/connect_agent_platform.py YOUR_REACH_WORKSPACE_ID --url http://127.0.0.1:4061 --external-user-id EXISTING_PLATFORM_USER_ID
```

The command asks for the existing service key privately, verifies access through the existing setup API, and writes only Reach's ignored `.local/agent-platform.json` with mode 0600. It creates no platform application, credential, number or channel. Reach's setup and activation controls use the platform's normal existing APIs when the operator requests those actions.

Live replies require a verified number accessible to that application and a Reach callback origin already allowed by the platform. The connector cannot grant those permissions. If the existing platform configuration does not allow the callback, that is a connection prerequisite; Reach must show a setup failure instead of changing the platform. No live delivery has been verified.

## Verification

- 19 backend tests passed, including the installed platform's real signed callback client against a synthetic Reach endpoint.
- Tests cover timestamp/signature rejection, tenant binding, consent, STOP, duplicate events, changed retries, no duplicate outbound queue, human handoff, role checks and setup retry.
- Fresh Alembic migration/schema check, frontend type check and production build passed.
- Browser verified the WhatsApp agent page and reply preview on the local CRM.

Proactive template campaigns and operator WhatsApp replies still use separate transport paths and are not enabled by this inbound agent integration. Real provider delivery and final delivery callbacks for these immediate replies remain unverified; the platform inbox holds provider delivery state. Both systems must resume a handed-off conversation explicitly before automation can continue.

## Reach readiness update (6 October 2026)

The WhatsApp agent page now shows the intended business number, approved-document count, and separate checks for the Reach credential, API availability, signed callback, Meta webhook and worker. The local preview is configured with `REACH_WHATSAPP_BUSINESS_NUMBER=+918219613387`. This is a display preference, not registration or a verified mapping to a Meta ID.

Number selection starts empty and requires the operator to confirm the ID belongs to the intended number. Activation is checked again on the server and blocked for missing webhook verification, offline workers, missing number authorization, changed application identities, or missing/changed channels. Pausing does not require activation readiness. The platform remains responsible for testing callback reachability during activation.

The adapter follows channel pagination and creates a new conversation when a previously closed conversation receives another message. Existing receipt deduplication, consent and handoff rules continue to apply.

### Handoff to the platform maintainer

Provide these connection details when the platform is ready:

1. Platform API base URL reachable by the Reach API (the inspected console was on `http://127.0.0.1:4061`).
2. Existing application service credential authorized specifically for Reach, with `channels:read` and `channels:write`, plus its external-user ID. Enter the credential only into the private connector prompt.
3. The Meta phone-number ID confirmed to represent **+91 82196 13387**, accessible to that same application and user.
4. Allow the Reach callback origin `http://127.0.0.1:18000` for a shared-host deployment. Full callback: `http://127.0.0.1:18000/integrations/agent-platform/whatsapp`. If the platform runs in a container or on another machine, supply a reachable Reach URL instead and pass it with `--bridge-url`; loopback would refer to the platform's own machine/container.
5. Complete Meta's public HTTPS webhook and start the platform worker. Meta's callback goes to the platform; the signed application callback goes from the platform to Reach.

For the existing local demo workspace, run from the Reach repository:

```sh
.venv/bin/python scripts/connect_agent_platform.py b2bbe17e-fa97-4a8c-be92-01318d7b62cc --url http://127.0.0.1:4061 --external-user-id REACH_EXTERNAL_USER_ID
```

Then use **WhatsApp agent → Refresh status → select and confirm the Meta ID → Prepare WhatsApp agent → Enable automatic replies**. Preparation creates/configures a channel through the platform's existing API; enabling remains an explicit operator action. The script only saves Reach's existing credential locally. No platform source or configuration was edited for this update.

Validation: 22 backend tests passed, including the installed platform's signed callback client, activation readiness, pause while unready, identity mismatch, outage/malformed responses, pagination, and messages after conversation closure. Frontend TypeScript check passed. Browser verified the local intended number and a cited answer from approved synthetic knowledge. No live WhatsApp message was sent, and production delivery remains pending platform readiness and the Reach credential.

## Number-only customer setup

Following Sahayak's number-first setup pattern, Reach now offers **Connect your business WhatsApp → Business WhatsApp number → Save business number**. Owners/admins can save or update the number before a platform channel exists; the number is stored per workspace in `whatsapp_setup_requests` (migration `0007_whatsapp_setup_request`). `POST /api/v1/whatsapp-agent/details` takes `{ "phone": "+918219613387" }` and never calls the platform. The status endpoint exposes `saved_number` and `number_editable`. A prepared channel locks number changes for setup-team review.

Customer-facing setup requires only the number; approved Reach Knowledge documents supply answers. Platform credentials, webhook details, and channel preparation are inside the collapsed **Advanced connection settings · setup team** section. Saving is not ownership verification, onboarding authorization, or activation. Provider onboarding remains managed by the platform team.

Verified 23 backend tests, frontend type checking, local migration with backup, and browser save/reopen persistence. The selected number +918219613387 is saved in the local demo workspace.
