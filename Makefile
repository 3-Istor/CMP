SHELL := bash
.SHELLFLAGS := -euo pipefail -c

CMP_SSH_HOST ?= pae-node-1
KUBE_CONTEXT ?= 3-istor-k3s
KEYSTONE_URL ?= http://192.168.1.210:5000/v3
BACKEND_PORT ?= 8000
DEV_DIR := .dev
SSHUTTLE_PID := $(DEV_DIR)/sshuttle.pid

export CMP_SSH_HOST KUBE_CONTEXT

.PHONY: help setup env network network-stop backend frontend dev doctor

help: ## List the targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'
	@echo "  Variables: CMP_SSH_HOST=$(CMP_SSH_HOST) KUBE_CONTEXT=$(KUBE_CONTEXT) BACKEND_PORT=$(BACKEND_PORT)"

setup: ## Install backend and frontend dependencies, create env files, run migrations
	./setup.sh

env: ## Copy every secret-backed backend variable from the cluster into backend/.env
	./scripts/sync-local-env.sh

network: ## Route OpenStack through the bastion unless Keystone is already reachable
	@mkdir -p $(DEV_DIR)
	@if curl -s -o /dev/null --max-time 3 $(KEYSTONE_URL); then \
		echo "Keystone reachable, no tunnel needed"; \
	elif [[ -f $(SSHUTTLE_PID) ]] && kill -0 "$$(cat $(SSHUTTLE_PID))" 2>/dev/null; then \
		echo "sshuttle already running but Keystone unreachable: make network-stop, then retry"; exit 1; \
	else \
		./scripts/dev-network.sh --daemon --pidfile="$(CURDIR)/$(SSHUTTLE_PID)"; \
		for _ in $$(seq 1 10); do curl -s -o /dev/null --max-time 2 $(KEYSTONE_URL) && exit 0; sleep 1; done; \
		echo "Tunnel started but Keystone still unreachable"; exit 1; \
	fi

network-stop: ## Stop the sshuttle tunnel
	@if [[ -f $(SSHUTTLE_PID) ]]; then kill "$$(cat $(SSHUTTLE_PID))" 2>/dev/null || true; rm -f $(SSHUTTLE_PID); echo "sshuttle stopped"; else echo "no tunnel running"; fi

backend: ## Run the API with reload on BACKEND_PORT (8000)
	cd backend && poetry run alembic upgrade head && poetry run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend: ## Run the Next.js dev server on :3000
	cd frontend && { [[ -d node_modules ]] || npm ci; } && npm run dev

dev: network ## Tunnel if needed, then backend and frontend together (Ctrl-C stops both)
	@$(MAKE) --no-print-directory -j2 backend frontend

doctor: ## Check the tools and access the dev env needs
	@for tool in poetry node npm terraform kubectl curl; do \
		command -v $$tool >/dev/null && echo "ok      $$tool" || echo "missing $$tool"; \
	done
	@command -v sshuttle >/dev/null && echo "ok      sshuttle" || { command -v nix >/dev/null && echo "ok      sshuttle (through nix run)" || echo "missing sshuttle (apt install sshuttle)"; }
	@[[ -f backend/.env ]] && echo "ok      backend/.env" || echo "missing backend/.env (make setup)"
	@[[ -f frontend/.env.local ]] && echo "ok      frontend/.env.local" || echo "missing frontend/.env.local (make setup)"
	@kubectl --context $(KUBE_CONTEXT) -n arcl-cmp get secret arcl-cmp-secret >/dev/null 2>&1 && echo "ok      cluster access (make env works)" || echo "missing cluster access: open the k3s tunnel to run make env"
	@curl -s -o /dev/null --max-time 3 $(KEYSTONE_URL) && echo "ok      Keystone reachable" || echo "missing Keystone (make network)"

# Local import UI: no OpenStack tunnel, cloud state or CI dispatch.
.PHONY: import-auth import-setup import-doctor import-backend import-frontend import-local

import-setup: ## Prepare import UI, env examples, dependencies and dedicated DB
	./scripts/import-local.sh setup

import-auth: ## With cluster access, copy only the Keycloak secret into ignored env files
	./scripts/import-local.sh auth

import-doctor: ## Check Keycloak login config without showing secrets
	./scripts/import-local.sh doctor

import-backend: ## API on :8000, local DB and sibling app-templates; import disabled
	./scripts/import-local.sh backend

import-frontend: ## UI on :3000, uses temporary Node 22 when needed
	./scripts/import-local.sh frontend

import-local: ## Authenticated local UI; no cloud tunnel
	./scripts/import-local.sh dev
