# Symptom-aware retrieval development check — 26 October 2026

This protocol was written before changing the agent's search query. It is a development comparison on the existing authored fixtures, not a held-out estimate.

## Problem and fixed change

`gather_context` currently sends `<service> <alertname>` to both knowledge tools. In all 40 authored agent scenarios, the alert name is the generic `ServiceDegraded`, so the query omits the actual symptom. Change only the query construction: use the validated service, then the alert annotation's description and summary when present, then the alert name. Normalize whitespace, ignore non-string fields, and cap the result at the `SearchQuery` limit of 500 characters. Keep the indexed corpus, hybrid ranker, runbook source filter, candidate count of five, model and claim validation unchanged.

## Baseline and one planned comparison

Using an isolated PostgreSQL 18/pgvector database on 26 October, the unchanged repo indexed the 80 runbook sections and 80 synthetic incident notes. The saved full-corpus reranker benchmark reproduced its prior 40-query development result: Recall@5 27/40 and MRR@5 0.5767. The agent's runbook tool uses a runbook-only hybrid search, so that full-corpus score does not describe its actual candidate path. With the current agent query (`<service> ServiceDegraded`) over the 32 actionable scenario fixtures, mean expected-runbook recall among five chunks was **0.46875**. Repeated chunks can occupy multiple slots. These numbers use different query inputs and must not be presented as one before/after comparison.

After the code change, rerun those same 32 actionable scenarios against the same temporary database and compute the same mean recall, without changing ranker, `k`, corpus or query wording after seeing the result. Report all misses, latency and the unchanged full-corpus benchmark separately. This measures whether the agent retrieves evidence it could use; it does not measure whether the local LLM diagnoses or cites correctly. The existing LLM fixture test supplies a fixed runbook list and ignores the tool's query, so its 25/32 single-shot and 23/32 agent runbook citation results cannot be claimed as improved by this retrieval change.

An independently authored query set should be frozen by hash and evaluated once after the implementation is fixed. Until then, any improvement here is development evidence only. Semantic citation support still requires the separate blinded review.
