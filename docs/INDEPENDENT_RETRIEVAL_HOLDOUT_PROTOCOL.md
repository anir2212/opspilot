# Independent runbook-query holdout protocol — 26 October 2026

Written and committed before opening the query text or running retrieval. The owner supplied `/private/tmp/opspilot_holdout_claude_20261026.json`, SHA-256 `966df6939b701bb3789a446381c4cbf1edce1e92a132494963de0686ddadab45`. Its hash matches. Schema-only checks found 40 unique nonempty queries, each with `query` and `relevant`; each RB-01–RB-20 appears twice as a relevance label. Query text and rankings were not inspected before this protocol.

## Fixed evaluation

Use the unchanged 160-chunk synthetic corpus (80 runbook sections and 80 incident notes) in the separate PostgreSQL 18/pgvector evaluation database. Do not alter the corpus, embeddings, ranker, queries, labels, or code after seeing results. Search the whole corpus with `limit=5`, as in `eval/retrieval_eval.py`. The **primary** metric is full-corpus `rerank` Recall@5: a query counts as a hit if any labelled runbook source ID occurs in the first five results. Also report MRR@5, and the existing `bm25`, `vector`, `hybrid` and `lexical` modes as descriptive comparisons. Report runbook-filtered `hybrid` Recall@5 and MRR@5 separately because that filter matches the agent's runbook tool. Record per-query retrieved IDs and ranks, but do not use this holdout to select or tune a mode.

Evaluate the frozen file once and save a machine-readable report with the input hash, corpus count, modes, metrics and per-query ranks. Inspect failures only after scoring. If a label is debatable, report the ambiguity without editing this file's labels or rescoring. Keep the original 40 authored development-question result and the 32 authored alert-scenario query comparison separate.

These queries were independently written from the same synthetic runbooks, according to the owner's submission; their authoring process was not independently audited. This check measures retrieval of runbook IDs from query text, **not** whether the changed agent builds a better query from a real alert, diagnoses a fault, or cites a claim correctly. Real incident evaluation and human citation-support labels remain open.

## Technical interruption before a saved result

The first invocation could not connect to the isolated localhost database from the sandbox and scored nothing. The second invocation reached the final `lexical` mode but PostgreSQL rejected an existing nullable `source_type` parameter whose SQL type was ambiguous. The evaluator wrote no result file, and no ranks or aggregate scores were inspected. `src/opspilot/retrieval.py` now explicitly casts that nullable parameter as text; this preserves the filter's meaning and does not change the other search modes. A smoke call with the existing development phrase `payment errors` succeeded both with and without the runbook filter. The same hash, modes, metrics, corpus and evaluator will be used for the completed run. This note and the SQL fix are committed before that run.
