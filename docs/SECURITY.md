# OpsPilot local security model

## Trust boundaries

The demo services, Prometheus, Loki, Tempo, runbooks, incident records, alert annotations, and chat text can all contain attacker-controlled data. The investigation tools treat those values as data. The alert description and summary enter the runbook and similar-incident search query, with whitespace normalized and input capped at 500 characters; they are not executed as commands. Metric and log queries are assembled from enum-validated templates; Tempo searches use an allow-listed service, three trace details at most, and a span-attribute allow-list. Model text cannot supply arbitrary PromQL, LogQL, TraceQL, SQL, URLs, or shell commands. MCP still exposes only six read methods; the built-in graph additionally reads bounded anomaly and Tempo summaries. Its ticket tool creates only a pending local note and cannot apply it.

The complete evidence remains in graph state, while the model prompt receives a bounded projection with distinct runbooks and service-scoped backup jobs. This reduces irrelevant prompt material but is not a prompt-injection defense. The final claim validator checks that every source ID exists in gathered context. That catches fabricated IDs, but it **does not prove semantic support**. A blinded human review packet (`eval/citation_review/`, rubric in `docs/CITATION_RUBRIC.md`) exists to measure support; it has no human labels yet. The sealed key must stay away from labellers until both have finished. A malicious log line or runbook could still influence an LLM. Before production use, add source-level trust labels, semantic entailment checks, adversarial prompt-injection tests, and human review of all recommendations.

## Writes and approval

Every ticket update or change request is first stored as a `PendingAction`. A separate API request with `X-Approval-Token` applies or rejects it. With no `OPSPILOT_APPROVAL_TOKEN`, approval returns HTTP 503. An already decided action returns HTTP 409, avoiding duplicate application. This shared local token is a demo control: real deployment needs authenticated identities, role-based access, audit records, CSRF protection where browser credentials apply, and enterprise change-system authorization.

Change review `APPROVE` is **advice about policy evidence**, never a deployment command or a human authorization. The freeze calendar is a local fixture file. If missing, the review returns `NEEDS INFO`; a frozen window, open SEV1/2 incident, failed deploy, or invalid window returns `REJECT`. Recording a sign-off uses the same local approval token. Production would require a signer to authenticate in the role claimed and a trusted calendar API.

## Network and secrets

Compose maps all service ports to `127.0.0.1`. PostgreSQL credentials in `compose.yaml` are local demo credentials, not production secrets. `.env` and model caches are ignored by Git; `.env.example` has no keys. Gemini is disabled unless explicitly selected; when enabled, the synthetic alert, runbook, metric, log, incident and trace summaries sent to the model leave the laptop. Use no customer data in this demo, and review the account's quota and billing tier before a full hosted evaluation. The API key is sent in an HTTP header, never a URL or logged output. Slack Bolt verifies signed inbound Slack events; the Teams SDK validates inbound tokens except in explicit loopback-only playground mode. Neither bot has an approval-token route. Real channel delivery remains unverified. Never paste live customer data into the synthetic fixture corpus.

The Helm chart's default PostgreSQL password is also a disposable-demo value in `values.yaml`. Kubernetes `Secret` does not make a checked-in value private. Override it from a managed secret source before any nonlocal deployment. The chart exposes only ClusterIP services. GHCR image publication requires a manual workflow dispatch with `publish=true`; no image has been published.

## Residual risks

The alert webhook currently has no signature or authentication because Alertmanager and OpsPilot share an isolated local Compose network. A hostile local process could inject alerts. Raw webhook deliveries are also kept only in process memory for debugging while deduplicated alert state is persisted. Database setup uses `create_all`, which is suitable for this demo but does not migrate an existing production schema. The retrieval development set was revised during implementation and does not estimate performance on unseen incidents.
