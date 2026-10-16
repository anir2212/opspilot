# Verified results

<!-- agent-eval:start -->
## M6 authored scenario harness (fixture backend)

These values are from 40 authored context fixtures and a deterministic backend,
not from a learned LLM or a live fault experiment. Root-cause IDs are absent in
the fixture backend, so the accuracy numbers are a harness baseline only.
Semantic citation precision, token counts, model cost, and a hosted-model
comparison remain `TBD` until independently measured.

| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | Citation ID validity | Median summary time |
|---|---:|---:|---:|---:|---:|---:|
| Single-shot RAG fixture | 0.000 | 0.000 | 0.000 | 1.000 | 1.000 | 0.0001 s |
| Five-node agent fixture | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.0014 s |

### Local LLM: qwen2.5:0.5b

The same authored fixtures were evaluated with an actual local model. This is a development benchmark, not a production incident estimate. No hosted backend was run or charged.

| Path | Root@1 | Root@3 | Runbook Recall@3 | False-alarm abstention | Citation ID validity | Median output time | Mean input/output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Single-shot RAG | 0.188 | 0.500 | 0.781 | 0.125 | 0.950 | 1.294 s | 1000.6 / 188.9 |
| Five-node agent | 0.156 | 0.625 | 0.719 | 0.500 | 1.000 | 2.750 s | 2977.8 / 404.5 |

`citation_id_validity` only checks whether an ID occurs in supplied context. It is **not** semantic citation precision. The agent abstained from diagnosing 4/8 false alarms; the single-shot path abstained from 1/8. Median time is local first output, not alert-to-chat latency. Dollar cost remains `TBD` because hardware and electricity cost were not measured.

The new opt-in citation-review export was smoke tested on five deterministic fixture scenarios: 50 claim/source pairs, 0 missing evidence objects, and 0 prefilled support labels. This checks reviewer-queue construction, not semantic correctness. The previously requested human labels and semantic citation precision remain unmeasured.
<!-- agent-eval:end -->

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

## M3 anomaly development benchmark

`make anomaly-baseline`, `make anomaly-collect`, and `make anomaly-eval` recorded 180 separate healthy training observations, then 32 local fault-injection episodes (four repeats of each of eight faults). Each episode has 16 healthy and six fault samples: 704 evaluation samples, of which 192 are labeled positive. Observations contain rolling request p50/p95 latency and error fraction, plus process CPU and resident memory. The direct `opspilot_fault_active` gauge is **excluded**. The resource faults are simulated by the app, not actual OOM, CPU exhaustion, or disk fill.

| Method | Episodes detected | Sample precision | Sample recall | Sample F1 | Median delay among detected episodes |
|---|---:|---:|---:|---:|---:|
| STL/MAD + IsolationForest with healthy-range guard | 32/32 | 0.835 | 1.000 | 0.910 | 0.0050 s |
| Static error fraction >0.2 or p95 >2 s | 28/32 | 1.000 | 0.729 | 0.843 | 0.0094 s |

The static rule missed all four `cpu_saturation` episodes because their synthetic response delay is about 0.2 s, below its 2 s threshold. The detector produced 38 false positives among 512 healthy evaluation samples. Delay is measured from the accepted local fault request to the first sampled response classified positive; it is **not** Prometheus/Alertmanager detection latency, and millisecond differences here have little operational meaning. The baseline and threshold logic were revised after inspecting this development set. A held-out workload with realistic traffic and actual resource pressure is needed before claiming production performance.

Pending measurements: held-out diagnosis quality, semantic citation precision, a hosted-model comparison, and real resource-fault behavior.

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
- `.github/workflows/ci.yml` has been authored but **has not run on GitHub**. GHCR publication is gated on manual workflow dispatch with `publish=true`; no image was pushed.
- The Helm chart is a core application smoke deployment. The full Prometheus, Alertmanager, Grafana, Loki, Tempo, and OTel Collector path was verified in Compose, not in kind.
