#!/usr/bin/env bash
# Project export restore drill, end to end, without platform credentials:
# throwaway Garage (same version as the platform) and two PostgreSQL
# containers, an export made by the CMP code from a seeded fake project,
# downloaded through its presigned link, restored by restore.sh into the
# current kubectl context (a blank kind cluster in CI).
#
#   scripts/export-drill/run.sh            # from the repository root
#
# Needs docker, a kind/k3d cluster as current context, the backend's Python
# environment (PYTHON, default "poetry run python"), pg_dump/psql 18+, jq,
# aws CLI v2.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
backend="$here/../../backend"
python=${PYTHON:-poetry run python}
garage_image=dxflrs/amd64_garage:v2.2.0
# The platform's CNPG major version. pg_dump must be at least as new as the
# server, and the target at least as new as pg_dump.
pg_image=postgres:18
out=${DRILL_OUT:-$PWD/drill-out}
mkdir -p "$out"

containers=(drill-garage drill-pg-source drill-pg-target)
cleanup() { docker rm -f "${containers[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

# Generated per run: nothing to keep secret, nothing hard-coded either.
pg_password=$(openssl rand -hex 16)
key_id="GK$(openssl rand -hex 12)"
key_secret=$(openssl rand -hex 32)

for name in drill-pg-source:55432 drill-pg-target:55433; do
  docker run -d --name "${name%%:*}" -p "${name##*:}:5432" \
    -e POSTGRES_PASSWORD="$pg_password" "$pg_image" >/dev/null
done

sed "s/RPC_SECRET/$(openssl rand -hex 32)/" "$here/garage.toml" > "$out/garage.toml"
docker run -d --name drill-garage -p 3900:3900 \
  -v "$out/garage.toml:/etc/garage.toml:ro" "$garage_image" >/dev/null
garage() { docker exec drill-garage /garage "$@" 2>/dev/null; }
for _ in $(seq 30); do garage status >/dev/null && break; sleep 1; done
garage layout assign -z dc1 -c 1G "$(garage node id -q | cut -d@ -f1)" >/dev/null
garage layout apply --version 1 >/dev/null
garage key import "$key_id" "$key_secret" -n drill --yes >/dev/null
garage key allow --create-bucket drill >/dev/null
for bucket in cnp-project-exports shop-media; do
  garage bucket create "$bucket" >/dev/null
  garage bucket allow --read --write --owner "$bucket" --key drill >/dev/null
done

for port in 55432 55433; do
  for _ in $(seq 30); do
    PGPASSWORD=$pg_password psql -h 127.0.0.1 -p "$port" -U postgres -Atc 'select 1' >/dev/null 2>&1 && break
    sleep 1
  done
done

export SOURCE_PG_HOST=127.0.0.1 SOURCE_PG_PORT=55432 SOURCE_PG_USER=postgres
export SOURCE_PG_PASSWORD=$pg_password
export S3_ENDPOINT=http://127.0.0.1:3900 S3_ACCESS_KEY_ID=$key_id
export S3_SECRET_ACCESS_KEY=$key_secret
echo "== export"
result=$(cd "$backend" && $python -m tests.export_drill.make_fixture --work "$out/work" | tail -n1)
echo "$result" | jq '{sha256, size}'

echo "== download through the presigned link"
curl -fsS -o "$out/export.tar.gz" "$(echo "$result" | jq -r .url)"
tar -xzf "$out/export.tar.gz" -C "$out" manifest.json

echo "== restore"
export TARGET_PG_URL="postgresql://postgres:$pg_password@127.0.0.1:55433/postgres"
export TARGET_S3_ENDPOINT=$S3_ENDPOINT AWS_ACCESS_KEY_ID=$key_id
export AWS_SECRET_ACCESS_KEY=$key_secret AWS_DEFAULT_REGION=garage
cd "$out"
"$here/restore.sh" "$out/export.tar.gz" "$(echo "$result" | jq -r .sha256)"

echo "== a tampered archive must fail the drill"
tampered=$(mktemp -d)
tar -xzf "$out/export.tar.gz" -C "$tampered"
echo "-- tampered" >> "$tampered/databases/drill-shop/shop-db.sql"
tar -czf "$out/tampered.tar.gz" -C "$tampered" .
mkdir -p "$out/tampered-run"
if (cd "$out/tampered-run" && "$here/restore.sh" "$out/tampered.tar.gz" >/dev/null 2>&1); then
  echo "FAIL: the tampered archive was accepted" >&2
  exit 1
fi
echo "tampered archive rejected"
