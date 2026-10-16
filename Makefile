.PHONY: install test lint typecheck up down kind-up kind-down kind-smoke fault load demo seed ingest retrieval-eval anomaly-collect anomaly-baseline anomaly-eval web-install web-dev web-build mcp change-eval slack teams-dev eval

install:
	uv sync --extra analysis --extra database --extra embeddings --extra agent --extra mcp --extra bots --extra telemetry --group dev

test:
	uv run pytest -q

lint:
	uv run ruff check src scripts eval tests

typecheck:
	uv run mypy src/opspilot --ignore-missing-imports

up:
	docker compose up -d --build

down:
	docker compose down

kind-up:
	kind create cluster --name opspilot --config infra/kind-config.yaml --wait 120s
	docker build -t opspilot-lite-local:0.1.0 -f Dockerfile.lite .
	docker build -t opspilot-api-local:0.1.0 -f Dockerfile .
	kind load docker-image opspilot-lite-local:0.1.0 opspilot-api-local:0.1.0 --name opspilot
	helm upgrade --install opspilot charts/opspilot --kube-context kind-opspilot --wait --timeout 5m

kind-smoke:
	kubectl --context kind-opspilot rollout status deployment/opspilot-api --timeout=180s
	kubectl --context kind-opspilot rollout status deployment/opspilot-gateway --timeout=180s
	kubectl --context kind-opspilot rollout status deployment/opspilot-payments --timeout=180s
	kubectl --context kind-opspilot rollout status deployment/opspilot-backups --timeout=180s
	kubectl --context kind-opspilot get svc opspilot-api

kind-down:
	kind delete cluster --name opspilot

fault:
	@test -n "$(NAME)" || (echo "Usage: make fault NAME=payment_errors DURATION=600"; exit 2)
	uv run python scripts/fault.py $(NAME) --duration $(or $(DURATION),600)

load:
	uv run python scripts/loadgen.py --seconds $(or $(SECONDS),60) --rps $(or $(RPS),2)

demo: up
	$(MAKE) load SECONDS=10
	$(MAKE) fault NAME=payment_errors DURATION=120
	$(MAKE) load SECONDS=30
	@echo "Prometheus http://127.0.0.1:18109, Alertmanager http://127.0.0.1:18193, OpsPilot http://127.0.0.1:18100/api/alerts"

seed:
	uv run python scripts/seed_knowledge.py

ingest:
	uv run python scripts/ingest.py

retrieval-eval:
	uv run python eval/retrieval_eval.py

anomaly-collect:
	uv run python eval/collect_anomaly.py

anomaly-baseline:
	uv run python eval/collect_baseline.py

anomaly-eval:
	uv run python eval/anomaly_eval.py

web-install:
	cd web && npm ci

web-dev:
	cd web && npm run dev

web-build:
	cd web && npm run build

mcp:
	uv run --extra mcp opspilot-mcp

change-eval:
	uv run python eval/change_eval.py

slack:
	uv run --extra bots opspilot-slack

teams-dev:
	OPSPILOT_TEAMS_DEV_UNAUTH=1 uv run --extra bots opspilot-teams

eval:
	uv run python eval/run_eval.py --backend fixture --update-results
