# Verified results

## Offline test entry-point check — 1 November 2026

The previous `make test` target launched the `pytest` executable directly and failed during collection here because `tests/test_eval.py` could not import the repository's `eval` package. Running pytest through the environment's Python module entry point includes the project root and passed the unchanged suite. The Make target now uses `uv run python -m pytest -q`: **69 passed, 2 integration tests skipped**. Ruff passed and mypy found no issues in 24 source files. This is a test invocation fix, not a new incident, retrieval or LLM measurement. The original eight-case diagnostic and human-label boundary below are unchanged.

<!-- agent-eval:start -->
## M6 authored scenario harness (fixture backend)

These values are from 40 authored context fixtures and a deterministic backend,
not from a learned LLM or a live fault experiment. Root-cause IDs are absent in
the fixture backend, so the accuracy numbers are a harness baseline only.
The fixture backend has no model token count. Semantic citation precision,
model cost, and a full hosted-model comparison remain unmeasured.

| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | Citation ID validity | Median summary time |
|---|---:|---:|---:|---:|---:|---:|
| Single-shot RAG fixture | 0.000 | 0.000 | 0.000 | 1.000 | 1.000 | 0.0001 s |
| Five-node agent fixture | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.0014 s |
<!-- agent-eval:end -->

### Local LLM: qwen2.5:0.5b

The same authored fixtures were evaluated with an actual local model. This is a development benchmark, not a production incident estimate. No hosted backend was run in this saved comparison; the later opt-in Gemini check is recorded separately below. The historical false-alarm scorer checked cause IDs in hypotheses and runbook citations in recommendations, but missed cause IDs in recommendations; its abstention column can overstate complete abstention. The 31 October limited report below uses a corrected rule.

| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | Citation ID validity | Median output time | Mean input/output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Single-shot RAG | 0.188 | 0.500 | 0.781 | 0.125 | 0.950 | 1.294 s | 1000.6 / 188.9 |
| Five-node agent | 0.156 | 0.625 | 0.719 | 0.500 | 1.000 | 2.750 s | 2977.8 / 404.5 |

**20 October rerun and citation-review packet.** `eval/run_eval.py --backend ollama --export-citations …` reran all 40 fixtures on the same local `qwen2.5:0.5b`, with Ollama manifest digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`, now recorded in the report. The agent path repeated Root@1 0.156, Root@3 0.625, Runbook Recall@3 0.719 and false-alarm abstention 0.500. Mean tokens were 2979.1 input and 412.1 output per scenario, against 2977.8 and 404.5 before, so the outputs are not byte-stable ([`eval/citation_review/agent_results_ollama_2026-10-20.json`](eval/citation_review/agent_results_ollama_2026-10-20.json)). The run exported **933** claim/source pairs. After removing 565 duplicate pairs, 24 with unresolved sources and 12 validator fallbacks, **332** were eligible. A seeded (20261020) random sample selected **30**: 21 from the agent path and 9 from single-shot RAG. These went into a blinded two-labeller packet with a sealed key ([`eval/citation_review/build_summary.json`](eval/citation_review/build_summary.json)). **Human labels: 0.** Semantic citation precision is therefore still unmeasured; the scorer and rubric are ready.

`citation_id_validity` only checks whether an ID occurs in supplied context. It is **not** semantic citation precision. Under the historical scorer, the agent was counted as abstaining on 4/8 false alarms and the single-shot path on 1/8; those counts do not establish that recommendations also abstained from assigning causes. Median time is local first output, not alert-to-chat latency. Dollar cost remains `TBD` because hardware and electricity cost were not measured.

The new opt-in citation-review export was smoke tested on five deterministic fixture scenarios: 50 claim/source pairs, 0 missing evidence objects, and 0 prefilled support labels. This checks reviewer-queue construction, not semantic correctness. The previously requested human labels and semantic citation precision remain unmeasured.

## M1 local Compose baseline

- Python unit/API tests: 5 passed (`uv run pytest -q tests/test_lite_app.py tests/test_api.py`).
- End-to-end alert delivery: 1 integration test passed, covering all 8 injected fault types (`OPSPILOT_INTEGRATION=1 uv run pytest -q tests/integration/test_faults.py`). The test finished in 12.18 seconds on this machine, including setup and polling; this is **not** a per-alert detection-delay measurement.
- Ruff checks: passed (`uv run ruff check src tests scripts`).
- Docker Compose: all 11 project services started; gateway, Prometheus, Loki, Tempo, and Grafana endpoints were queried locally.
- Observed webhook evidence: a `PaymentErrorsInjected` firing alert with `service=payments` reached `/api/alerts` after the local payment fault was injected.
- A later local run fired `PaymentErrorsInjected`, `LatencyInjected`, and `BackupFailureInjected` simultaneously. `docs/alertmanager-three-faults.png` and `docs/grafana-three-faults.png` capture the three alerts. Grafana also plotted scraped agent-run and duration metrics. One OTLP trace contained a `gateway → payments` parent-child span; Tempo returned its trace ID and Loki returned a payment failure log with the same trace ID.

## M2 retrieval development benchmark

`uv run python eval/retrieval_eval.py` evaluated 40 fixed, authored questions (two for each of 20 runbooks) against 160 indexed chunks: 80 runbook sections and 80 synthetic incident notes. A hit requires the expected runbook ID in the first five chunks. Values come from `eval/retrieval_results.json` and can be regenerated after `make seed && make ingest`. Vector and reranker inference use the ONNX BGE models through FastEmbed.

| Ranking method | Recall@5 | MRR@5 |
|---|---:|---:|
| Okapi BM25 | 0.475 | 0.421 |
| BGE vector cosine | 0.500 | 0.458 |
| BM25 + vector RRF | 0.500 | 0.384 |
| RRF + BGE cross-encoder reranker | 0.675 | 0.577 |
| PostgreSQL `ts_rank` lexical (diagnostic) | 0.050 | 0.050 |

The corpus and questions are synthetic and from the same domain. The baseline was revised after inspecting an initial run, so these are **development-set scores**, not held-out generalization claims. The non-reranked hybrid does not outperform vector-only here. The 80 near-duplicate incident notes often outrank the intended runbook, and strict `plainto_tsquery` misses paraphrases. These failure modes remain visible in the report.

## Symptom-aware agent retrieval development check, 26 October 2026

The [predeclared comparison](docs/RETRIEVAL_QUERY_PROTOCOL.md) used a separate temporary PostgreSQL 18/pgvector database containing the unchanged 160 indexed chunks: 80 runbook sections and 80 synthetic incident notes. The agent's actual runbook tool filters to runbook chunks and uses hybrid BM25/vector RRF, five returned chunks. Across the **32 actionable authored scenario fixtures**, the old query used only `<service> ServiceDegraded`; the new query includes the alert's symptom description and summary, then its name, within 500 characters. The [saved per-scenario result](eval/agent_retrieval_query_2026-10-26.json) records both queries against the same database and corpus.

| Query supplied to the unchanged runbook search | Mean expected-runbook recall among five chunks | Full-credit scenarios | Zero-credit scenarios |
|---|---:|---:|---:|
| Service + generic alert name | **0.46875** | 13/32 | 15/32 |
| Service + observed symptom + alert name | **0.890625** | 25/32 | 0/32 |

The seven remaining partial scores are all concurrent-fault fixtures: their alert text describes the first fault, while the expected list also contains a second fault. Repeated sections from one runbook can occupy multiple of the five slots. This comparison was made on development fixtures whose contents were already known; it is evidence that the query path uses available symptoms, **not a held-out or production retrieval estimate**. The original 40-question full-corpus reranker result above remains 0.675, and its query/corpus filter differs from this check. The saved local Ollama runbook-citation scores (25/32 single-shot, 23/32 agent) were **not rerun**; that harness supplies a fixed candidate list and ignores the tool's query. Human citation support is still unmeasured.

## Separately authored runbook-query holdout, 26 October 2026

The owner supplied a Claude-authored set of 40 queries, two labelled for each RB-01–RB-20, with SHA-256 `966df6939b701bb3789a446381c4cbf1edce1e92a132494963de0686ddadab45`. Its [exact input](eval/independent_retrieval_queries_2026-10-26.json), [precommitted scoring protocol](docs/INDEPENDENT_RETRIEVAL_HOLDOUT_PROTOCOL.md), [evaluator](eval/independent_retrieval_holdout_eval.py), and [per-query output](eval/independent_retrieval_holdout_2026-10-26.json) are saved. The 160 indexed synthetic chunks, modes and top-five limit were unchanged. The primary metric was full-corpus reranked Recall@5, with a hit when any labelled runbook ID appeared among the five chunks.

| Search path on the new 40 queries | Runbook hit@5 | MRR@5 |
|---|---:|---:|
| Full corpus: reranked hybrid (predeclared primary) | **31/40 = 0.775** | 0.7113 |
| Full corpus: Okapi BM25 | 29/40 = 0.725 | 0.6833 |
| Full corpus: vector | 32/40 = 0.800 | 0.7104 |
| Full corpus: hybrid RRF | 31/40 = 0.775 | 0.6858 |
| Full corpus: PostgreSQL lexical | 0/40 = 0.000 | 0.0000 |
| Runbook-filtered hybrid, direct query text | 40/40 = 1.000 | 0.9875 |

All nine full-corpus reranker misses targeted RB-01–RB-08; in each, five near-duplicate synthetic incident notes occupied the returned slots. Vector-only exceeded reranking by one query on this set, so the earlier 0.675 development result is not evidence that reranking is consistently best. PostgreSQL lexical returned no chunks for these long paraphrased questions because `plainto_tsquery` requires all query terms. The runbook-filtered result is promising for the agent's runbook tool but uses the supplied query text directly; it does **not** test whether a real alert supplies enough symptom text, nor any LLM diagnosis or citation. The queries were authored from these same synthetic runbooks, their relevance labels have not been independently adjudicated, and the author's reading restrictions were self-reported. This is an independent *question-writing* check, not a real-incident generalization estimate. No retrieval change was made after seeing the results.

The first evaluation attempt was blocked by sandbox localhost access before scoring. A second attempt reached `lexical` mode, then failed because its existing nullable SQL filter lacked an explicit type; no result file was written or rankings inspected. The text cast was committed and smoke tested on a development phrase before the completed run, with the same holdout hash, corpus, evaluator and modes. See the protocol for that interruption.

## M3 anomaly development benchmark

`make anomaly-baseline`, `make anomaly-collect`, and `make anomaly-eval` recorded 180 separate healthy training observations, then 32 local fault-injection episodes (four repeats of each of eight faults). Each episode has 16 healthy and six fault samples: 704 evaluation samples, of which 192 are labeled positive. Observations contain rolling request p50/p95 latency and error fraction, plus process CPU and resident memory. The direct `opspilot_fault_active` gauge is **excluded**. The resource faults are simulated by the app, not actual OOM, CPU exhaustion, or disk fill.

| Method | Episodes detected | Sample precision | Sample recall | Sample F1 | Median delay among detected episodes |
|---|---:|---:|---:|---:|---:|
| STL/MAD + IsolationForest with healthy-range guard | 32/32 | 0.835 | 1.000 | 0.910 | 0.0050 s |
| Static error fraction >0.2 or p95 >2 s | 28/32 | 1.000 | 0.729 | 0.843 | 0.0094 s |

The static rule missed all four `cpu_saturation` episodes because their synthetic response delay is about 0.2 s, below its 2 s threshold. The detector produced 38 false positives among 512 healthy evaluation samples. Delay is measured from the accepted local fault request to the first sampled response classified positive; it is **not** Prometheus/Alertmanager detection latency, and millisecond differences here have little operational meaning. The baseline and threshold logic were revised after inspecting this development set. A held-out workload with realistic traffic and actual resource pressure is needed before claiming production performance.

Pending measurements: held-out diagnosis quality, semantic citation precision, a full hosted-model comparison, and real resource-fault behavior.

## 29 October live triage evidence check

The new triage context path reuses the STL/MAD and IsolationForest classes on Prometheus p50/p95 latency, error fraction, process CPU and memory. It trains only on samples before the alert `startsAt`, requires at least 20 complete baseline points, scores at most 24 incident points, and excludes `opspilot_fault_active`. The `make demo` target now creates a three-minute healthy load before a bounded synthetic payment fault. A local manual query of that fault's recorded time window returned 44 complete baseline samples and 12 incident samples; 11 of the latter were flagged. This verifies the plumbing on one synthetic run. It does not re-estimate the 32-episode benchmark's precision, recall, or alert latency.

The Tempo read tool queried the local Tempo API and returned three traces with three span summaries each. A mock transport test verifies service allow-listing, bounded output and removal of a private span attribute. One opt-in `gemini-3.8-flash` API smoke request returned three structured claims with valid source IDs and reported 254 input and 228 output tokens. This verifies connectivity and parsing, not model accuracy or a full hosted cost comparison. The user's Gemini key remains in ignored local `.env` and is never in this report.

The first five-scenario hosted evaluation produced no completed report after about 20 minutes and was interrupted. A subsequent one-scenario run with a 30-second request timeout failed both the agent and single-shot paths with `ReadTimeout`. A lower-thinking retry on 3.8 Flash returned HTTP 503 for both paths. Changing to stable 3.6 Flash yielded a successful single-shot response but the agent still met an HTTP 503. With one bounded retry on 503, a one-scenario 3.6 Flash run completed: the agent and single-shot paths each found the authored root cause and expected runbook, and each cited only available source IDs. The agent took 18.40 seconds with 2,909 input and 458 output tokens; single-shot took 3.26 seconds with 1,009 input and 222 output tokens.

A final five-scenario 3.6 Flash sample completed three scenarios on each path; the last two received HTTP 429 rate limits. The sample contains five variants of one authored backup-failure scenario, so it does not represent the 40-scenario suite. On the three completed cases, each path found the expected root cause and runbook in both actionable cases and produced valid source IDs; neither abstained on the one false alarm. The evaluation harness now excludes transport-failed cases from model-quality averages and reports completed and failed counts separately. The full 40-scenario Gemini comparison has not been run. The limited reports remain in the local `/private/tmp` workspace and contain no key. The full hosted comparison and semantic citation precision remain unmeasured.

A later end-to-end synthetic payment fault reached Alertmanager and the persisted LangGraph triage with the local deterministic backend. Its saved payment context had 47 pre-alert samples, one observed incident sample and one anomaly flag; Tempo returned three trace summaries. A separate gateway error-rate alert had 53 pre-alert samples, two incident samples and two flags, again with three traces. These are two related alerts from one controlled fault and verify live context delivery, not detector accuracy.

The blinded packet still has no human labels. No human semantic citation precision is reported.

## 31 October prompt-context checkpoint and limited Ollama diagnostic

The model prompt now receives a bounded projection of the saved investigation context. A regression test supplies 2,000 metric points, repeated chunks from one runbook, a second distinct runbook, and an unrelated backup job. Both distinct runbook IDs remain in the prompt, the metric series is reduced to recent values, and the unrelated job is omitted. A separate test confirms that a backup alert retains its job error code and source ID. At the earlier checkpoint the offline suite passed **66 tests**, with **2 integration tests skipped**; Ruff and the project-configured mypy check passed. That verified prompt construction and scoping only.

**Later 31 October local diagnostic.** The installed `qwen2.5:0.5b` with the same manifest digest as the 20 October report ran eight selected authored fixtures: baseline and false-alarm variants of backup failure, bad deploy, dependency down and payment errors. [The saved report](eval/prompt_context_ollama_2026-10-31.json) contains each scenario's claims, citations, fixture context and scores. All eight agent and single-shot paths completed. The agent found the expected cause at rank 1 in **1/4** actionable cases and within three hypotheses in **2/4**; it cited an expected runbook in **4/4**. The single-shot path reached **1/4**, **2/4** and **2/4** respectively. Under the corrected rule requiring no cause ID in either hypotheses or recommendations and no runbook citation, **neither path abstained on any of the four false alarms**. Source-ID validity was 1.000 for the agent and 0.7812 for single-shot RAG; neither number measures semantic support.

For the same eight IDs, the saved 20 October agent rows had root@1 **1/4**, root@3 **1/4**, expected-runbook citation **3/4**, and historical-rule false-alarm abstention **2/4**. The current run gained the backup-failure and bad-deploy causes in its top three, lost the payment-error cause, and still missed dependency down. Some claims assigned a present cause from runbook possibilities alone, and a backup false alarm's recommendation assigned unrelated cause IDs even when its hypothesis fell back. Those are visible failures, not evidence of improved diagnosis or citation support. The cases were chosen for inspection after viewing the historical rows; they are a development diagnostic, not a fresh holdout. Since the prior run, the graph also gained unavailable anomaly and Tempo fixture slots, and local model outputs are not byte-stable. The scoring rule changed, so the false-alarm figures are not a like-for-like before/after comparison. No full 40-case rerun or Gemini comparison followed this prompt change, and the existing blinded human packet was not rebuilt.

## M4 agent core verification

- A local synthetic payment alert completed the five LangGraph nodes in order and cited `RB-01` in the recommendation. A second run restored a previous thread through PostgreSQL checkpoint state after an API container restart.
- The seven investigation tool contracts passed a live local integration test, including the Loki query path. When Loki is not configured, the log tool explicitly reports `available: false`.
- Unit tests reject a model's made-up citation after one retry, then emit a cautious alert-grounded fallback. They also prove that drafting a ticket note leaves the incident unchanged until an approval-token request applies the note exactly once.
- The default backend is a deterministic fixture. A separate 40-scenario run exercised the actual local Ollama `qwen2.5:0.5b` backend; its root-cause accuracy and token counts are shown in M6 above. Semantic support of each cited claim remains unverified.

## M5 console baseline

`npm run build` passed TypeScript checking and produced a Vite production bundle. The local browser rendered the incident list, an alert/deploy timeline, cited summary, agent step durations, follow-up form, and pending-approval panel; see `docs/console.png`. Slack Socket Mode and Teams SDK adapters passed fake-client and credential-gate tests. Real channel delivery remains unverified without workspace or tenant credentials.

M2 verification: 8 unit/API tests passed; the live container returned a hybrid search result and a filtered mock incident response. The ONNX API image built and started locally.

## M7 local MCP and change review

The official MCP Python SDK's in-memory client discovered exactly six read-only tools, called the metric tool successfully, and received an error for an invalid metric name. `make change-eval` recorded 10/10 agreements with **authored local policy cases** in `eval/change_results.json`. These fixtures check rule consistency; they do not measure quality on real deployment decisions. A sign-off integration test confirmed missing-token HTTP 403 and persisted re-evaluation from `NEEDS INFO` to advisory `APPROVE`. No deployment, chat post, or production change was made.

## M8 local quality and Kubernetes smoke

- Ruff passed for `src`, `scripts`, `eval`, and `tests`; mypy reported no issues in 21 source files; unit/API tests passed **39**, with **2** integration tests skipped when Compose was down. With the local Compose stack running, both integration test files passed.
- `helm lint charts/opspilot` passed and `helm template` rendered the chart. A local kind v0.33.0 cluster deployed the API, gateway, payments, backups, and PostgreSQL; all five pods reached `1/1 Ready`, and `make kind-smoke` passed. The API restarted three times while PostgreSQL initialized, then stayed ready. The temporary cluster was removed after verification.
- `.github/workflows/ci.yml` runs on the public GitHub repository. Its `python`, `integration` and `kind` jobs passed on every run from 16 to 22 October 2026, through the 22 October push to `main`. The public copy also points the runner's FastEmbed cache at a writable directory. This is a dated CI record; the current branch's checks still need to run after publication. GHCR publication is gated on a manual workflow dispatch with `publish=true`; that job was skipped and no image was pushed.
- The Helm chart is a core application smoke deployment. The full Prometheus, Alertmanager, Grafana, Loki, Tempo, and OTel Collector path was verified in Compose, not in kind.
