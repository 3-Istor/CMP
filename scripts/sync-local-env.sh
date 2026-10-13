#!/usr/bin/env bash
# Copies the AWS and Terraform state backend settings of the deployed CMP into backend/.env,
# so a local backend uses the same live credentials as prod. Values are never printed.
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-3-istor-k3s}"
NAMESPACE="arcl-cmp"
SECRET_NAME="arcl-cmp-secret"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/backend/.env}"

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE, run ./setup.sh first" >&2; exit 1; }

read_secret() {
    local value
    value="$(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get secret "$SECRET_NAME" \
        -o jsonpath="{.data.$1}")"
    [[ -n "$value" ]] || { echo "key $1 is empty in $SECRET_NAME" >&2; exit 1; }
    base64 -d <<<"$value"
}

set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    KEY="$key" VALUE="$value" awk '
        BEGIN { found = 0 }
        $0 ~ "^" ENVIRON["KEY"] "=" { print ENVIRON["KEY"] "=" ENVIRON["VALUE"]; found = 1; next }
        { print }
        END { if (!found) print ENVIRON["KEY"] "=" ENVIRON["VALUE"] }
    ' "$ENV_FILE" >"$tmp"
    cat "$tmp" >"$ENV_FILE"
    rm -f "$tmp"
    echo "updated $key"
}

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null

access_key_id="$(read_secret aws-access-key-id)"
secret_access_key="$(read_secret aws-secret-access-key)"
region="$(read_secret aws-region)"
state_bucket="$(read_secret aws-s3-bucket)"
state_key_prefix="$(read_secret aws-s3-key-prefix)"

set_env AWS_ACCESS_KEY_ID "$access_key_id"
set_env AWS_SECRET_ACCESS_KEY "$secret_access_key"
set_env AWS_DEFAULT_REGION "$region"
set_env TF_BACKEND_S3_ENABLED "true"
set_env TF_BACKEND_AWS_ACCESS_KEY_ID "$access_key_id"
set_env TF_BACKEND_AWS_SECRET_ACCESS_KEY "$secret_access_key"
set_env TF_BACKEND_AWS_REGION "$region"
set_env TF_BACKEND_S3_BUCKET "$state_bucket"
set_env TF_BACKEND_S3_KEY_PREFIX "$state_key_prefix"
