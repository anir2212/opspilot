"""Create deterministic local runbooks, labeled queries, and synthetic incident history."""

from __future__ import annotations

import json
from pathlib import Path

from opspilot.storage import Incident, init_database, make_engine, session_factory

ROOT = Path(__file__).resolve().parents[1]

# Each entry is original demo-domain material, not copied operational guidance.
# Two independently phrased search questions per runbook give 40 fixed eval cases.
RUNBOOKS = [
    (
        "RB-01",
        "Payment error spike",
        "payments",
        "HTTP 5xx, rejected charges and failed checkout",
        "Payment handler returns exceptions or upstream processor rejects requests",
        "Inspect payment error ratio and gateway 5xx; compare processor responses; pause retries if they amplify errors; escalate to payment owner",
        "Error ratio returns below baseline and test checkout succeeds",
        "Why are charges failing with 5xx?",
        "How should I triage an increase in payment errors?",
    ),
    (
        "RB-02",
        "Gateway latency",
        "gateway",
        "p95 request latency above two seconds with low throughput",
        "Slow downstream requests or an overloaded gateway worker",
        "Inspect gateway p50 and p95; compare payment latency; sample traces; limit expensive retries; ask owner before changing capacity",
        "p95 falls below alert threshold for ten minutes",
        "Checkout is slow but not failing; what should I inspect?",
        "How to investigate gateway p95 latency?",
    ),
    (
        "RB-03",
        "Gateway memory growth",
        "gateway",
        "RSS rises steadily and process restarts",
        "Unbounded cache or retained request objects",
        "Inspect resident memory slope and restart count; compare release versions; capture heap profile in a safe replica; draft rollback if growth began after deployment",
        "Memory stabilizes across two traffic cycles",
        "Gateway memory keeps climbing and restarts",
        "What do I do for a possible memory leak in gateway?",
    ),
    (
        "RB-04",
        "Gateway CPU saturation",
        "gateway",
        "CPU above ninety percent and rising queue depth",
        "Compute-heavy path or excessive retries",
        "Correlate CPU with request rate; inspect profiles and retry volume; disable optional batch work only with approval; consider capacity planning",
        "CPU and queue depth return to normal",
        "CPU pegged on gateway workers",
        "How should I triage CPU saturation?",
    ),
    (
        "RB-05",
        "Payment dependency unavailable",
        "payments",
        "Connection refused or timeout from payment dependency",
        "Dependency endpoint unavailable or connection pool exhausted",
        "Check dependency health and DNS; inspect connection pool errors; keep retry budget bounded; notify dependency owner",
        "Synthetic payment request succeeds without retry storm",
        "Payment upstream connection refused",
        "How do I diagnose an unavailable payment dependency?",
    ),
    (
        "RB-06",
        "Payment rollback decision",
        "payments",
        "Errors begin shortly after a payment service release",
        "New payment version introduced regression",
        "Verify deploy timestamp against error onset; compare canary and stable instances; collect failing trace; draft a rollback request with owner sign-off",
        "After approved rollback, errors drop and smoke payment passes",
        "Payment errors started after a new version",
        "When should I roll back a bad payment deploy?",
    ),
    (
        "RB-07",
        "Backup job failed",
        "backups",
        "Backup status failed and error code recorded",
        "Client offline, repository unreachable, authentication or policy error",
        "Read backup job error code and policy; verify client and destination reachability; rerun only after cause is fixed; preserve the previous valid restore point",
        "A retry succeeds and restore-point age is within policy",
        "A scheduled backup job failed",
        "How to investigate a failed backup policy run?",
    ),
    (
        "RB-08",
        "Backup capacity exhausted",
        "backups",
        "Disk free metric at zero or write failure",
        "Backup repository capacity exhausted",
        "Check free space and retention; identify safe expired snapshots; ask storage owner before deletion; extend capacity through approved change",
        "Free space and a test backup write remain healthy",
        "Backup storage says disk full",
        "How to respond to no free disk space for backups?",
    ),
    (
        "RB-09",
        "Payment timeout budget",
        "payments",
        "Payment calls exceed timeout and gateway latency rises",
        "Processor latency exceeds client timeout budget",
        "Compare upstream and gateway spans; check connection timeout and retry count; capture processor status; propose timeout change with load test evidence",
        "End-to-end p95 and success rate recover",
        "Payment calls keep timing out",
        "Which timeout budget should I inspect for checkout?",
    ),
    (
        "RB-10",
        "Retry storm",
        "gateway",
        "Request rate and retries rise while successful checkouts fall",
        "Retries magnify a failing dependency",
        "Graph original requests versus retries; confirm circuit breaker state; cap retry attempts through approved config change",
        "Retry ratio falls while accepted checkouts recover",
        "Gateway retries are amplifying an outage",
        "How do I stop a retry storm safely?",
    ),
    (
        "RB-11",
        "Alert deduplication",
        "gateway",
        "Many duplicate alerts for one underlying fault",
        "Alertmanager grouping or webhook retry behavior",
        "Group by service and alert name; use alert fingerprint and status transition for idempotency; compare unique incidents to deliveries",
        "One incident remains for repeated deliveries",
        "Alertmanager sends duplicate webhooks",
        "How should repeated alerts be deduplicated?",
    ),
    (
        "RB-12",
        "Noisy false alarm",
        "gateway",
        "One brief metric excursion without user impact",
        "Transient scrape gap or short traffic burst",
        "Verify metric continuity and request impact; check alert duration; avoid incident escalation until corroborated; record reason for suppression",
        "Signal returns without recurrent impact",
        "A single short spike fired an alert",
        "How do I check whether an alert is a false positive?",
    ),
    (
        "RB-13",
        "Incident severity",
        "gateway",
        "Multiple service alerts with differing user impact",
        "Shared dependency or separate incidents",
        "Count failed user requests and affected services; assign severity using impact and duration; record why severity changed",
        "Severity matches observed scope",
        "How to set severity during a checkout outage?",
        "What evidence distinguishes SEV2 from SEV3?",
    ),
    (
        "RB-14",
        "Trace correlation",
        "gateway",
        "Gateway latency and payment errors overlap",
        "Payment dependency is probable upstream cause",
        "Follow trace IDs from gateway spans into payment spans; compare onset timestamps; distinguish symptom from first failing span",
        "Root-cause hypothesis cites a trace and metric query",
        "Gateway and payments both show anomalies",
        "How can traces reveal the first failing service?",
    ),
    (
        "RB-15",
        "Deployment evidence",
        "payments",
        "Alert starts near release window",
        "Release regression or coincidental dependency failure",
        "Record version and rollout time; compare baseline and current error rates; inspect change ticket and canary; avoid claiming causation from timing alone",
        "Hypothesis includes supporting and contradictory evidence",
        "What evidence ties an alert to a deployment?",
        "How should I distinguish correlation from deploy causation?",
    ),
    (
        "RB-16",
        "Backup partial completion",
        "backups",
        "Backup status partial with some bytes written",
        "Some clients or files were skipped",
        "Read per-client errors and excluded paths; compare bytes written to last healthy job; rerun affected subset after approval",
        "Affected client restore test succeeds",
        "Backup completed only partially",
        "What does a partial backup status mean?",
    ),
    (
        "RB-17",
        "Backup restore test",
        "backups",
        "Backup job succeeds but recovery confidence is unknown",
        "Restore path has not been exercised",
        "Select non-production restore target; verify checksum and application readability; record recovery duration",
        "Restored data passes integrity check",
        "How to verify a successful backup is recoverable?",
        "Which restore test should follow a backup alert?",
    ),
    (
        "RB-18",
        "Processor authentication",
        "payments",
        "Processor responds unauthorized",
        "Expired credential or wrong secret scope",
        "Confirm error code without logging secret values; check rotation timeline and secret reference; coordinate credential fix with owner",
        "Authorized synthetic transaction passes and no secrets appear in logs",
        "Payment processor returns 401",
        "How to triage expired payment credentials?",
    ),
    (
        "RB-19",
        "Gateway readiness",
        "gateway",
        "Instances marked unready after startup",
        "Startup dependency or misconfigured readiness path",
        "Inspect readiness probe failures and startup logs; compare environment config; do not send traffic until checks pass",
        "All replicas ready and canary request succeeds",
        "Gateway pods never become ready",
        "What should I check when readiness probes fail?",
    ),
    (
        "RB-20",
        "Change approval",
        "payments",
        "Risky rollback or config change requested",
        "Unreviewed change could worsen incident",
        "Collect test evidence and affected services; check change freeze and open severe incidents; require owner, QA and security sign-off before execution",
        "Approval recorded and post-change verification attached",
        "Who needs to approve a production rollback?",
        "How are deployment changes checked before execution?",
    ),
]


def generate_files() -> None:
    target = ROOT / "knowledge" / "runbooks"
    target.mkdir(parents=True, exist_ok=True)
    queries = []
    for identifier, title, service, symptoms, cause, steps, verify, q1, q2 in RUNBOOKS:
        body = f"""---
id: {identifier}
service: {service}
symptoms: {symptoms}
---
# {title}

## Signal and scope
This runbook applies to the local `{service}` demo service. Symptoms: {symptoms}. Record the alert start time, affected requests, current release, and related services before suggesting a cause. Treat this document as guidance, not authorization to change a live system.

## Investigation
Likely cause to test: {cause}. {steps}. Link each observation to its Prometheus query, trace, backup job, or incident number. Compare the signal against a healthy interval; a coincident deploy alone does not prove causation.

## Recovery and verification
Any state-changing step requires a human-approved change request. {verify}. Record the exact time, owner, and evidence after verification. If the expected recovery does not occur, reopen the hypothesis and escalate.
"""
        (target / f"{identifier}.md").write_text(body, encoding="utf-8")
        queries.extend(
            [{"query": q1, "relevant": [identifier]}, {"query": q2, "relevant": [identifier]}]
        )
    (ROOT / "eval" / "retrieval_queries.json").write_text(
        json.dumps(queries, indent=2) + "\n", encoding="utf-8"
    )


def seed_incidents() -> int:
    engine = make_engine()
    init_database(engine)
    Session = session_factory(engine)
    with Session() as session:
        for index in range(80):
            rb = RUNBOOKS[index % 8]
            identifier, title, service, symptoms, cause, steps, verify, _, _ = rb
            number = f"INC{index + 1:04d}"
            if session.get(Incident, number):
                continue
            session.add(
                Incident(
                    number=number,
                    short_description=f"Synthetic scenario {index // 8 + 1}: {title.lower()} - {symptoms}",
                    state="closed",
                    cmdb_ci=service,
                    work_notes=f"Observed {symptoms}; tested {cause.lower()}. Followed {identifier}. {steps}.",
                    close_notes=f"Synthetic resolution note: {verify}. No production action occurred.",
                )
            )
        session.commit()
        return session.query(Incident).count()


if __name__ == "__main__":
    generate_files()
    print(f"Generated {len(RUNBOOKS)} runbooks and {len(RUNBOOKS) * 2} queries")
    print(f"Database contains {seed_incidents()} synthetic historical incidents")
