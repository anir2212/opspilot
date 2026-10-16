# OpsPilot local security model

## Trust boundaries

The demo services, Prometheus, Loki, runbooks, incident records, and chat text can all contain attacker-controlled data. The investigation tools treat those values as data. Metric and log queries are assembled from enum-validated templates; model text cannot supply arbitrary PromQL, LogQL, SQL, URLs, or shell commands. MCP exposes only six read methods. The seventh agent tool creates a pending local ticket note; it cannot apply the note.

The final claim validator checks that every source ID exists in gathered context. That catches fabricated IDs, but it **does not prove semantic support**. A malicious log line or runbook could still influence an LLM. Before production use, add source-level trust labels, semantic entailment checks, adversarial prompt-injection tests, and human review of all recommendations.

## Writes and approval

Every ticket update or change request is first stored as a `PendingAction`. A separate API request with `X-Approval-Token` applies or rejects it. With no `OPSPILOT_APPROVAL_TOKEN`, approval returns HTTP 503. An already decided action returns HTTP 409, avoiding duplicate application. This shared local token is a demo control: real deployment needs authenticated identities, role-based access, audit records, CSRF protection where browser credentials apply, and enterprise change-system authorization.

Change review `APPROVE` is **advice about policy evidence**, never a deployment command or a human authorization. The freeze calendar is a local fixture file. If missing, the review returns `NEEDS INFO`; a frozen window, open SEV1/2 incident, failed deploy, or invalid window returns `REJECT`. Recording a sign-off uses the same local approval token. Production would require a signer to authenticate in the role claimed and a trusted calendar API.

## Network and secrets

Compose maps all service ports to `127.0.0.1`. PostgreSQL credentials in `compose.yaml` are local demo credentials, not production secrets. `.env` and model caches are ignored by Git; `.env.example` has no keys. Slack Bolt verifies signed inbound Slack events; the Teams SDK validates inbound tokens except in explicit loopback-only playground mode. Neither bot has an approval-token route. Real channel delivery remains unverified. Never paste live customer data into the synthetic fixture corpus.

The Helm chart's default PostgreSQL password is also a disposable-demo value in `values.yaml`. Kubernetes `Secret` does not make a checked-in value private. Override it from a managed secret source before any nonlocal deployment. The chart exposes only ClusterIP services. GHCR push requires a manual workflow dispatch with `publish=true`; the local implementation made no external publication.

## Residual risks

The alert webhook currently has no signature or authentication because Alertmanager and OpsPilot share an isolated local Compose network. A hostile local process could inject alerts. Raw webhook deliveries are also kept only in process memory for debugging while deduplicated alert state is persisted. Database setup uses `create_all`, which is suitable for this demo but does not migrate an existing production schema. The retrieval development set was revised during implementation and does not estimate performance on unseen incidents.
