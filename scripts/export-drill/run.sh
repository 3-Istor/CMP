#!/usr/bin/env bash
# Project export restore drill, end to end, without platform credentials.
#
# Source: a kind cluster "drill-source" running the project's database (TLS
# PostgreSQL 18 behind <cluster>-rw, credentials in a namespace Secret, as
# CNPG lays it out) and a throwaway Garage (the platform's version). The CMP
# export code runs its real dump Job there, copies the bucket and bundles a
# git repository; the rest of the platform is faked.
# Target: the current kubectl context (a blank kind cluster in CI), a fresh
# PostgreSQL 18 container and Garage, restored by restore.sh from the
# archive downloaded through its presigned link.
#
#   scripts/export-drill/run.sh            # from the repository root
#
# Needs docker, kind, kubectl, openssl, the backend's Python environment
# (PYTHON, default "poetry run python"), psql 18+, jq, aws CLI v2.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
backend="$here/../../backend"
python=${PYTHON:-poetry run python}
garage_image=dxflrs/amd64_garage:v2.2.0
# The platform's CNPG major version and image: the dump Job runs in the
# database cluster's own image, and the target must be at least as new.
pg_image=postgres:18
dump_image=ghcr.io/cloudnative-pg/postgresql:18.6-202610050823-system-trixie@sha256:3e89ea90485ffda67094fd6950d9d61dec9ec314aabc8d63c048c67759918621
out=${DRILL_OUT:-$PWD/drill-out}
mkdir -p "$out"
source_kubeconfig="$out/source.kubeconfig"
ks() { kubectl --kubeconfig "$source_kubeconfig" "$@"; }

proxy_pid=""
cleanup() {
  [[ -n $proxy_pid ]] && kill "$proxy_pid" 2>/dev/null
  docker rm -f drill-garage drill-pg-target >/dev/null 2>&1 || true
  kind delete cluster --name drill-source >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

# Generated per run: nothing to keep secret, nothing hard-coded either.
pg_password=$(openssl rand -hex 16)
key_id="GK$(openssl rand -hex 12)"
key_secret=$(openssl rand -hex 32)

echo "== source cluster"
kind create cluster --name drill-source --kubeconfig "$source_kubeconfig" --wait 120s >/dev/null

sed "s/RPC_SECRET/$(openssl rand -hex 32)/" "$here/garage.toml" > "$out/garage.toml"
# On kind's network, so the dump Job reaches it as the platform's Jobs reach
# the garage Service.
docker run -d --name drill-garage --network kind -p 3900:3900 \
  -v "$out/garage.toml:/etc/garage.toml:ro" "$garage_image" >/dev/null
garage_ip=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' drill-garage)
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

docker run -d --name drill-pg-target -p 55433:5432 \
  -e POSTGRES_PASSWORD="$pg_password" "$pg_image" >/dev/null

# The project's database, laid out as CNPG does: Service shop-db-rw, a TLS
# certificate for that name signed by the CA in Secret shop-db-ca, the
# owner's credentials in the app's Secret.
tls="$out/tls"; mkdir -p "$tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=drill-ca \
  -keyout "$tls/ca.key" -out "$tls/ca.crt" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj /CN=shop-db-rw \
  -keyout "$tls/tls.key" -out "$tls/tls.csr" 2>/dev/null
openssl x509 -req -in "$tls/tls.csr" -CA "$tls/ca.crt" -CAkey "$tls/ca.key" \
  -CAcreateserial -days 1 -out "$tls/tls.crt" \
  -extfile <(printf 'subjectAltName=DNS:shop-db-rw') 2>/dev/null
ns=drill-shop
ks create namespace "$ns" >/dev/null
ks -n "$ns" create secret generic shop-db-ca --from-file=ca.crt="$tls/ca.crt" >/dev/null
ks -n "$ns" create secret tls shop-db-server --cert="$tls/tls.crt" --key="$tls/tls.key" >/dev/null
ks -n "$ns" create secret generic shop-secrets \
  --from-literal=username=app --from-literal=password="$pg_password" >/dev/null
ks -n "$ns" apply -f - >/dev/null <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: {name: shop-db}
spec:
  selector: {matchLabels: {app: shop-db}}
  template:
    metadata: {labels: {app: shop-db}}
    spec:
      securityContext: {fsGroup: 999}
      containers:
        - name: postgres
          image: $pg_image
          args: [-c, ssl=on, -c, ssl_cert_file=/tls/tls.crt, -c, ssl_key_file=/tls/tls.key]
          env:
            - {name: POSTGRES_USER, value: app}
            - {name: POSTGRES_DB, value: shop}
            - name: POSTGRES_PASSWORD
              valueFrom: {secretKeyRef: {name: shop-secrets, key: password}}
          volumeMounts: [{name: tls, mountPath: /tls}]
          readinessProbe: {exec: {command: [pg_isready, -U, app, -d, shop]}}
      volumes:
        - name: tls
          secret: {secretName: shop-db-server, defaultMode: 0640}
---
apiVersion: v1
kind: Service
metadata: {name: shop-db-rw}
spec:
  selector: {app: shop-db}
  ports: [{port: 5432}]
YAML
ks -n "$ns" rollout status deploy/shop-db --timeout=180s >/dev/null
# The image's init runs a temporary server first: wait for the real one.
for _ in $(seq 30); do
  ks -n "$ns" exec deploy/shop-db -- psql -U app -d shop -Atc 'show ssl' 2>/dev/null | grep -qx on && break
  sleep 2
done
ks -n "$ns" exec -i deploy/shop-db -- psql -U app -d shop -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE customers (id serial PRIMARY KEY, name text, created_at timestamptz DEFAULT now());
CREATE TABLE orders (id serial PRIMARY KEY, customer_id int REFERENCES customers(id),
  total numeric(10,2), note jsonb);
INSERT INTO customers (name) SELECT 'client ' || g FROM generate_series(1, 250) g;
INSERT INTO orders (customer_id, total, note)
  SELECT 1 + g % 250, g * 1.5, jsonb_build_object('n', g) FROM generate_series(1, 1000) g;
SQL

ks proxy --port 8001 >/dev/null 2>&1 &
proxy_pid=$!
for _ in $(seq 30); do curl -fs http://127.0.0.1:8001/version >/dev/null && break; sleep 1; done

for _ in $(seq 30); do
  PGPASSWORD=$pg_password psql -h 127.0.0.1 -p 55433 -U postgres -Atc 'select 1' >/dev/null 2>&1 && break
  sleep 1
done

export KUBE_PROXY=http://127.0.0.1:8001 DUMP_IMAGE=$dump_image
export S3_ENDPOINT=http://127.0.0.1:3900 S3_INTERNAL_ENDPOINT=http://$garage_ip:3900
export S3_ACCESS_KEY_ID=$key_id S3_SECRET_ACCESS_KEY=$key_secret
echo "== export"
result=$(cd "$backend" && $python -m tests.export_drill.make_fixture --work "$out/work" | tail -n1)
echo "$result" | jq '{sha256, size}'
if [[ -n $(ks -n "$ns" get jobs,secrets -l cnp.3istor.com/export -o name) ]]; then
  echo "FAIL: the dump Job or its Secret was left behind" >&2; exit 1
fi

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
