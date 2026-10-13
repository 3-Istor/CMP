#!/usr/bin/env bash
# Copies every secret-backed environment variable of the deployed CMP backend into backend/.env,
# so a local backend runs with the same live credentials as prod. Values are never printed.
# Plain values are skipped: they are in-cluster URLs or the defaults of app/core/config.py.
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-3-istor-k3s}"
NAMESPACE="arcl-cmp"
DEPLOYMENT="arcl-cmp-backend"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/backend/.env}"

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE, run ./setup.sh first" >&2; exit 1; }

kube() { kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" "$@"; }

read_secret() {
    local value
    value="$(kube get secret "$1" -o jsonpath="{.data.$2}")"
    [[ -n "$value" ]] || { echo "key $2 is empty in secret $1" >&2; exit 1; }
    base64 -d <<<"$value"
    printf x
}

# Multi-line values (PEM keys) are written double-quoted with \n escapes, which python-dotenv expands.
format_value() {
    if [[ "$1" == *$'\n'* ]]; then
        local escaped="${1//\\/\\\\}"
        escaped="${escaped//\"/\\\"}"
        printf '"%s"' "${escaped//$'\n'/\\n}"
    else
        printf '%s' "$1"
    fi
}

# Replaces KEY's line, including the continuation lines of an old multi-line quoted value.
set_env() {
    local key="$1" value tmp
    value="$(format_value "$2")"
    tmp="$(mktemp)"
    KEY="$key" VALUE="$value" awk '
        BEGIN { found = 0; skipping = 0 }
        skipping { if ($0 ~ /"[[:space:]]*$/) skipping = 0; next }
        index($0, ENVIRON["KEY"] "=") == 1 {
            print ENVIRON["KEY"] "=" ENVIRON["VALUE"]
            found = 1
            rest = substr($0, length(ENVIRON["KEY"]) + 2)
            if (rest ~ /^"/ && (length(rest) == 1 || rest !~ /"[[:space:]]*$/)) skipping = 1
            next
        }
        { print }
        END { if (!found) print ENVIRON["KEY"] "=" ENVIRON["VALUE"] }
    ' "$ENV_FILE" >"$tmp"
    cat "$tmp" >"$ENV_FILE"
    rm -f "$tmp"
}

refs="$(kube get deployment "$DEPLOYMENT" -o jsonpath='{range .spec.template.spec.containers[?(@.name=="backend")].env[?(@.valueFrom.secretKeyRef)]}{.name}{"\t"}{.valueFrom.secretKeyRef.name}{"\t"}{.valueFrom.secretKeyRef.key}{"\n"}{end}')"
[[ -n "$refs" ]] || { echo "no secret-backed env found on $DEPLOYMENT" >&2; exit 1; }

declare -A values
while IFS=$'\t' read -r name secret key; do
    # The trailing x keeps the value's final newlines, which $(...) would strip.
    value="$(read_secret "$secret" "$key")"
    values[$name]="${value%x}"
done <<<"$refs"

for name in "${!values[@]}"; do
    set_env "$name" "${values[$name]}"
done
set_env TF_BACKEND_S3_ENABLED "true"
echo "updated ${#values[@]} secret-backed variables from $DEPLOYMENT, plus TF_BACKEND_S3_ENABLED"
