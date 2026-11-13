#!/usr/bin/env bash
# Restore a CNP project export and prove it is complete and intact.
#
#   restore.sh ARCHIVE.tar.gz [EXPECTED_ARCHIVE_SHA256]
#
# Checks every file against manifest.json (both directions), then restores:
# Kubernetes objects into the current kubectl context, each PostgreSQL dump
# into a fresh database of TARGET_PG_URL (row counts compared), each object
# into TARGET_S3_ENDPOINT (checksum read back), each git bundle into a clone
# (HEAD compared). Writes drill-report.json; exits 1 on any mismatch.
#
# Needs: jq, sha256sum, tar, git, kubectl, psql, aws (CLI v2).
# Environment: TARGET_PG_URL (a superuser URL, database part ignored),
# TARGET_S3_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# AWS_DEFAULT_REGION.
set -euo pipefail

archive=${1:?usage: restore.sh ARCHIVE.tar.gz [EXPECTED_SHA256]}
expected=${2:-}
: "${TARGET_PG_URL:?}" "${TARGET_S3_ENDPOINT:?}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0
declare -A counts=()

fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }
ok() { counts[$1]=$(( ${counts[$1]:-0} + 1 )); }

if [[ -n $expected ]]; then
  actual=$(sha256sum "$archive" | cut -d' ' -f1)
  [[ $actual == "$expected" ]] || { echo "archive sha256 $actual != $expected" >&2; exit 1; }
fi

# Refuse absolute paths and .. before extracting anything.
if tar -tzf "$archive" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "archive holds an unsafe path" >&2; exit 1
fi
tar -xzf "$archive" -C "$work" --no-same-owner --no-same-permissions
manifest="$work/manifest.json"
jq -e '.schema == "cnp.project-export/v1"' "$manifest" >/dev/null \
  || { echo "unknown manifest schema" >&2; exit 1; }
project=$(jq -r .project "$manifest")
echo "Export $(jq -r .export_id "$manifest") of project $project"

# 1. Integrity: every item is a file with its checksum and size, and every
#    file is an item.
while IFS=$'\t' read -r path sum size; do
  file="$work/$path"
  if [[ ! -f $file ]]; then fail "missing $path"; continue; fi
  [[ $(sha256sum "$file" | cut -d' ' -f1) == "$sum" ]] || fail "sha256 of $path"
  [[ $(stat -c %s "$file") == "$size" ]] || fail "size of $path"
  ok checksums
done < <(jq -r '.items[] | [.path, .sha256, (.size|tostring)] | @tsv' "$manifest")
extra=$(comm -13 \
  <(jq -r '.items[].path' "$manifest" | sort) \
  <(cd "$work" && find . -type f ! -name manifest.json | sed 's|^\./||' | sort))
[[ -z $extra ]] || fail "files not in the manifest: $extra"

# 2. JSON Lines tables: valid JSON, row count as recorded.
while IFS=$'\t' read -r path rows; do
  jq -c . "$work/$path" >/dev/null || fail "invalid JSON in $path"
  [[ $(grep -c '' "$work/$path" || true) == "$rows" ]] || fail "rows of $path"
  ok tables
done < <(jq -r '.items[] | select(.format == "application/x-ndjson")
  | [.path, (.metadata.rows|tostring)] | @tsv' "$manifest")

# 3. Kubernetes: namespaces first, then objects. A kind the target cluster
#    does not serve (CRDs of the platform's operators) is reported, not
#    failed: installing the operators is part of the new hosting.
apply_object() {
  local path=$1 api=$2 kind=$3 name=$4 group=""
  [[ $api == */* ]] && group=${api%/*}
  if ! kubectl api-resources --api-group="$group" --no-headers 2>/dev/null \
      | awk '{print $NF}' | grep -qx "$kind"; then
    echo "skip $kind/$name: kind not served by this cluster"; ok k8s_skipped; return
  fi
  kubectl apply -f "$work/$path" >/dev/null || { fail "apply $path"; return; }
  kubectl get -f "$work/$path" >/dev/null || { fail "not found after apply: $kind/$name"; return; }
  ok k8s_applied
}
while IFS=$'\t' read -r _ path api kind name; do
  apply_object "$path" "$api" "$kind" "$name"
done < <(jq -r '.items[] | select(.type == "kubernetes.object")
  | [(.metadata.kind != "Namespace"), .path, .metadata.apiVersion,
     .metadata.kind, .metadata.name] | @tsv' "$manifest" | sort -k1,1)

# 4. PostgreSQL: each dump into a fresh database, then exact row counts.
pg_base=${TARGET_PG_URL%/*}
export PGOPTIONS="--client-min-messages=warning"
while IFS=$'\t' read -r path cluster ns; do
  db="restored_$(echo "${ns}_${cluster}" | tr -c 'a-zA-Z0-9_\n' '_')"
  psql "$pg_base/postgres" -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS $db" \
    -c "CREATE DATABASE $db" >/dev/null
  if ! psql "$pg_base/$db" -v ON_ERROR_STOP=1 -q -f "$work/$path" >/dev/null; then
    fail "psql restore of $path"; continue
  fi
  while IFS=$'\t' read -r table expected_rows; do
    got=$(psql "$pg_base/$db" -Atc "SELECT count(*) FROM $table")
    [[ $got == "$expected_rows" ]] || fail "$db $table: $got rows, expected $expected_rows"
  done < <(jq -r --arg p "$path" '.items[] | select(.path == $p)
    | .metadata.row_counts | to_entries[] | [.key, (.value|tostring)] | @tsv' "$manifest")
  ok databases
done < <(jq -r '.items[] | select(.type == "postgres.dump")
  | [.path, .metadata.cluster, .metadata.namespace] | @tsv' "$manifest")

# 5. Objects: into a bucket of the target store, read back and compared.
s3() { aws --endpoint-url "$TARGET_S3_ENDPOINT" "$@"; }
while IFS=$'\t' read -r path bucket key sum; do
  target="restored-$bucket"
  s3 s3api head-bucket --bucket "$target" >/dev/null 2>&1 \
    || s3 s3api create-bucket --bucket "$target" >/dev/null
  s3 s3 cp --quiet "$work/$path" "s3://$target/$key"
  [[ $(s3 s3 cp --quiet "s3://$target/$key" - | sha256sum | cut -d' ' -f1) == "$sum" ]] \
    || fail "object $bucket/$key differs after restore"
  ok objects
done < <(jq -r '.items[] | select(.type == "object")
  | [.path, .metadata.bucket, .metadata.key, .sha256] | @tsv' "$manifest")

# 6. Repositories: every bundle verifies and clones at the recorded HEAD.
while IFS=$'\t' read -r path head; do
  clone="$work/clone-$(basename "$path" .bundle)"
  # verify needs a repository to run in; a clone of the bundle is one.
  if ! git clone -q "$work/$path" "$clone" \
      || ! git -C "$clone" bundle verify -q "$work/$path" >/dev/null 2>&1; then
    fail "bundle $path"; continue
  fi
  [[ $(git -C "$clone" rev-parse HEAD) == "$head" ]] || fail "HEAD of $path"
  ok repositories
done < <(jq -r '.items[] | select(.type == "git.bundle")
  | [.path, .metadata.head] | @tsv' "$manifest")

jq -n --arg project "$project" --argjson failures "$failures" \
  --argjson problems "$(jq .problems "$manifest")" \
  --argjson counts "$(for k in "${!counts[@]}"; do echo "{\"$k\": ${counts[$k]}}"; done | jq -s 'add // {}')" \
  '{project: $project, failures: $failures, restored: $counts, export_problems: $problems}' \
  | tee drill-report.json
[[ $failures -eq 0 ]]
