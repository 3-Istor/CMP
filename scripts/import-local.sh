#!/usr/bin/env bash
# Local UI on the import branches. Never dispatches CI or applies Terraform.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
action=doctor
if [[ $# -gt 0 ]]; then action=$1; fi
require_branch() {
  for path in "$repo_root" "$repo_root/../app-templates"; do
    [[ $(git -C "$path" branch --show-current) == import-fichier-joe-raph ]] ||
      { echo "Use import-fichier-joe-raph in $path" >&2; exit 1; }
  done
}
frontend() {
  if node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' 2>/dev/null; then
    npm "$@"
  else
    npm exec --yes --package=node@22 --package=npm@11.4.2 -- npm "$@"
  fi
}
require_branch
case "$action" in
  setup)
    for tool in python3 poetry node npm git; do
      command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
    done
    mkdir -p backend/data .dev
    python3 - <<'PY'
from pathlib import Path
import secrets
backend = Path("backend/.env")
if not backend.exists():
    backend.write_text(Path("backend/.env.import-local.example").read_text())
    backend.chmod(0o600)
frontend = Path("frontend/.env.local")
if not frontend.exists():
    frontend.write_text(
        Path("frontend/.env.local.example").read_text().replace(
            "AUTH_SECRET=\n", "AUTH_SECRET=" + secrets.token_urlsafe(48) + "\n"
        )
    )
    frontend.chmod(0o600)
print("Local env files prepared; existing files and credentials preserved.")
PY
    (cd backend && poetry install --no-interaction &&
      DEBUG=false IMPORT_ENABLED=false TF_BACKEND_S3_ENABLED=false \
        DATABASE_URL=sqlite:///./data/import-local.db poetry run alembic upgrade head)
    (cd frontend && frontend ci)
    echo "Ready to start the UI. Run make import-doctor to check login settings."
    ;;
  auth)
    command -v kubectl >/dev/null || { echo "kubectl missing: ask a CNP admin for local Keycloak configuration." >&2; exit 1; }
    (cd backend && poetry run python - <<'PY'
from pathlib import Path
from dotenv import set_key
import base64
import os
import subprocess
import sys
paths = [Path(".env"), Path("../frontend/.env.local")]
if not all(path.is_file() for path in paths):
    sys.exit("Run make import-setup first.")
result = subprocess.run(
    ["kubectl", "--context", os.environ.get("KUBE_CONTEXT", "3-istor-k3s"),
     "-n", "arcl-cmp", "get", "secret", "arcl-cmp-secret",
     "-o", "jsonpath={.data.keycloak-client-secret}"],
    capture_output=True, text=True, timeout=20,
)
if result.returncode or not result.stdout.strip():
    sys.exit("CNP secret unavailable: check cluster access and permissions.")
value = base64.b64decode(result.stdout.strip(), validate=True).decode()
if not value:
    sys.exit("The Keycloak client secret is empty.")
for path in paths:
    set_key(str(path), "KEYCLOAK_CLIENT_SECRET", value)
    path.chmod(0o600)
print("Keycloak client secret installed in ignored local files; no value printed.")
PY
    )
    ;;
  doctor)
    (cd backend && poetry run python - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
import sys
front_path = Path("../frontend/.env.local")
back_path = Path(".env")
if not front_path.is_file() or not back_path.is_file():
    sys.exit("Missing local env files: run make import-setup.")
front = dotenv_values(front_path)
back = dotenv_values(back_path)
problems = []
for name in ("AUTH_SECRET", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET", "KEYCLOAK_ISSUER"):
    value = front.get(name) or ""
    if not value or value.lower().startswith(("your_", "replace", "change")):
        problems.append("frontend/.env.local: " + name + " is missing")
if front.get("KEYCLOAK_CLIENT_ID") != (back.get("KEYCLOAK_CLIENT_ID") or "3-istor-openid"):
    problems.append("The backend and frontend Keycloak client IDs differ")
if not back.get("KEYCLOAK_CLIENT_SECRET"):
    problems.append("backend/.env: KEYCLOAK_CLIENT_SECRET is missing")
elif front.get("KEYCLOAK_CLIENT_SECRET") != back["KEYCLOAK_CLIENT_SECRET"]:
    problems.append("The backend and frontend Keycloak client secrets differ")
realm = back.get("KEYCLOAK_REALM") or "3istor"
issuer = (back.get("KEYCLOAK_URL") or "https://auth.3istor.com").rstrip("/") + "/realms/" + realm
if front.get("KEYCLOAK_ISSUER") != issuer:
    problems.append("The backend and frontend Keycloak realms differ")
if (front.get("AUTH_URL") or "").rstrip("/") != "http://localhost:3000":
    problems.append("AUTH_URL must be http://localhost:3000 for this launcher")
if problems:
    print("\n".join(problems))
    sys.exit(1)
print("Login settings present and consistent. No secret was printed.")
print("Use an existing project where your CNP account is admin/owner.")
print("Import stays disabled here: the form is inspectable, CI/deploy are not dispatched.")
PY
    )
    ;;
  backend)
    mkdir -p backend/data
    cd backend
    export DEBUG=false IMPORT_ENABLED=false TF_BACKEND_S3_ENABLED=false
    export BACKGROUND_COLLECTORS_ENABLED=false
    export DATABASE_URL=sqlite:///./data/import-local.db
    export TEMPLATE_REPOSITORY_PATH
    TEMPLATE_REPOSITORY_PATH=$(cd "$repo_root/../app-templates" && pwd)
    poetry run alembic upgrade head
    exec poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000
    ;;
  frontend)
    cd frontend
    frontend run dev -- --hostname 127.0.0.1 --port 3000
    ;;
  dev)
    "$0" doctor
    "$0" backend &
    backend_pid=$!
    "$0" frontend &
    frontend_pid=$!
    trap 'kill "$backend_pid" "$frontend_pid" 2>/dev/null || true' EXIT INT TERM
    wait -n "$backend_pid" "$frontend_pid"
    ;;
  *) echo "Usage: $0 setup|auth|doctor|backend|frontend|dev" >&2; exit 2 ;;
esac
