# Cluster UP/DOWN from the CMP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cnp-admin` users start/stop EKS and GKE from the CMP home page; the CMP owns cluster state and blocks deployments to a cluster that is not `up` (CNP-109 + CNP-128).

**Architecture:** A `cluster_state` table plus a `cluster_ops` service run the `cnp.sh` equivalent as subprocesses inside the backend pod, streaming lines to a log file on the `/app/data` PVC and updating step/progress in DB. The global health endpoint carries cluster state (15 s polling drives buttons and toasts); an SSE endpoint streams logs. The Terraform roots are baked into the backend image at build.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic, pytest; Next.js + sonner; Terraform 1.11.4, aws CLI v2, gcloud + gke-gcloud-auth-plugin, kubectl; Helm; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-25-cluster-up-down-design.md`

**Deviation from spec:** the spec says "reuse `TerraformExecutor`". It rewrites the S3 backend key, forces `TF_DATA_DIR` and injects app-deployment credentials, all wrong for the cluster roots. `cluster_ops` uses its own ~30-line streaming runner; it reuses `state_lock.parse_state_lock` and the SSE pattern of `/deployments/{id}/logs/stream`.

## Parallel tracks

| Track | Repo | Tasks | Agent |
|---|---|---|---|
| Infra | `Cloud` (branch `feat/cnp-109-cmp-operator`), `K3s` (branch `feat/cnp-109-vault-cluster-ops` **from `main`**, not from the unpushed `feat/cnp-129-cnp-admin-role`) | 1, 2 | agent 1 |
| CMP | `CMP` (branch `feat/cnp-109-cluster-up-down`) | 3 → 9 | agent 2 |

Only contract between tracks: the secret keys of Task 2 consumed by Task 8. Nothing is `terraform apply`-ed by an agent; Hugo applies.

## Global Constraints

- Clouds: `aws` (EKS `cnp-eks`, region `eu-west-3`, account `947554757826`), `gcp` (GKE `cnp-gke`, zone `europe-west9-a`, project `project-94dabc15-3981-4317-8da`). Values match `TargetCloud.AWS/GCP`.
- States: `up`, `down`, `starting`, `stopping`, `error`. UP allowed from `down|error`, DOWN from `up|error`; otherwise HTTP 409.
- UP steps (in order): `init`, `apply`, `kubeconfig`, `nodes`, `argocd`, `wireguard`. DOWN steps: `checks`, `destroy`.
- UP is done only when ArgoCD has the cluster and the WireGuard pod (`-n cnp-vpn -l app=wireguard`) is Running.
- DOWN refuses while any Service of type `LoadBalancer` exists.
- Retry after pod restart: at most once (`attempts` ≤ 2), then `error`.
- Admin = `is_cnp_admin(token)` (realm role `cnp-admin` or `FINOPS_ADMIN_USERS`).
- Notifications: sonner toast in the CMP only. No Discord.
- Code comments, commits, PR text in English, concise, no ticket keys in comments. Commit/PR titles carry `CNP-109`.

## Review Focus

1. Admin clicks UP twice fast (or two admins) → second call gets 409, only one subprocess runs. Test in Task 6.
2. Pod restarted mid-operation, S3 lock held by a *different* actor (Hugo's laptop) → no force-unlock, state `error`. Test in Task 5.
3. Reconciliation sees "cluster absent" while state is `starting` → state untouched. Test in Task 5.
4. Deployment requested for an `onprem` project while EKS is down → allowed. Test in Task 7.
5. Terraform fails mid-apply (non-zero exit) → state `error`, log file keeps the full output, toast says failure. Test in Task 4.

---

### Task 1: EKS admin principals as a list (Cloud)

**Files:**
- Modify: `Cloud/terraform/public/aws/EKS/variables.tf:9-12`, `main.tf:35-45`, `terraform.tfvars.example`, `README.md`

- [ ] **Step 1:** Replace `variable "admin_principal_arn"` (string) with `admin_principal_arns` (`set(string)`, no default); turn `aws_eks_access_entry.operator` and `aws_eks_access_policy_association.operator` into `for_each = var.admin_principal_arns`. Update both `depends_on` (lines ~178, ~198) — they reference the resource, which still works with `for_each`.
- [ ] **Step 2:** Add `moved` blocks from the old single addresses to `["arn:aws:iam::947554757826:user/Hugo"]` keys so Hugo's existing entry is not recreated.
- [ ] **Step 3:** Verify: `terraform fmt -check && terraform validate` in `aws/EKS` (run `terraform init -backend=false` first). Expected: `Success!`.
- [ ] **Step 4:** Commit `feat(eks): allow several admin principals (CNP-109)`.

### Task 2: CMP operator identities and Vault policy (Cloud + K3s)

**Files:**
- Create: `Cloud/terraform/public/cmp-operator/{main.tf,versions.tf,README.md}` — S3 backend key `cnp/cmp-operator/terraform.tfstate`, same bucket.
- Create: `K3s/terraform/policies/cmp_cluster_ops.hcl`; Modify: `K3s/terraform/policies.tf`

**Produces (contract for Task 8):** Vault KV `kvv2/arcl-cmp/cluster-ops` with keys `aws-access-key-id`, `aws-secret-access-key`, `gcp-sa-key` (JSON), `vault-token`. VSO syncs it to Secret `arcl-cmp-cluster-ops` in the CMP namespace (same pattern as the existing arcl-cmp VaultSecret). The IAM user ARN is output as `cmp_operator_arn` for EKS `admin_principal_arns`.

- [ ] **Step 1:** `cmp-operator/main.tf`: `aws_iam_user` `cnp-cmp-operator` + inline policy covering what `aws/EKS` and `wireguard-endpoints` touch (eks, ec2, iam (scoped to `cnp-*` roles), ssm `/cnp/eks/*`, s3 on `istor-cnp-tfstate-757826` and `cnp-wireguard-endpoints-757826`) + access key; `google_service_account` `cnp-cmp-operator` with `roles/container.admin`, `roles/compute.networkAdmin`, `roles/compute.securityAdmin`, `roles/iam.serviceAccountUser`, `roles/iam.serviceAccountAdmin`, `roles/resourcemanager.projectIamAdmin`, `roles/storage.objectAdmin` + key; `vault_kv_secret_v2` writing the KV above (vault token excluded: Hugo adds it, Step 3).
- [ ] **Step 2:** `cmp_cluster_ops.hcl`: `create/update/read` on `kvv2/data/argocd/clusters/*`, `read` on `kvv2/data/wireguard/*` (whatever path `cluster-addons` `data "vault_kv_secret_v2" "wireguard"` reads), `create/update/read` on `auth/kubernetes-*/config` (and `sys/auth` read if `cluster-addons` needs it — check `modules/cluster-addons/main.tf:57-130`). Register `vault_policy "cmp_cluster_ops"` in `policies.tf`.
- [ ] **Step 3:** README in `cmp-operator`: apply order (K3s policy → `vault token create -policy=cmp-cluster-ops -period=768h` → put into `kvv2/arcl-cmp/cluster-ops` key `vault-token` → `cmp-operator` apply → add `cmp_operator_arn` to EKS `admin_principal_arns`). Add the VaultSecret manifest for `arcl-cmp-cluster-ops` next to the existing arcl-cmp one in K3s.
- [ ] **Step 4:** Verify `terraform fmt -check && terraform validate` in `cmp-operator` and `K3s/terraform`. Expected: `Success!`.
- [ ] **Step 5:** Commit in each repo `feat: cmp operator identities for cluster up/down (CNP-109)`.

### Task 3: Cluster state model and transitions (CMP)

**Files:**
- Create: `backend/app/models/cluster.py`, `backend/alembic/versions/<rev>_add_cluster_state.py`, `backend/app/services/clusters/__init__.py`, `backend/app/services/clusters/state.py`
- Modify: `backend/app/models/__init__.py` (register model)
- Test: `backend/tests/test_cluster_state.py` (use `session_factory` from `tests/test_app_security_data.py`)

**Produces:**
- `ClusterState(str, Enum)`: `UP, DOWN, STARTING, STOPPING, ERROR` (values lowercase).
- Model `Cluster` (table `cluster_state`): `cloud: TargetCloud` (PK), `state`, `operation: str|None` (`up|down`), `step: str|None`, `done: int`, `total: int`, `started_at`, `started_by: str|None`, `attempts: int = 0`, `log_path: str|None`, `last_error: str|None`, `updated_at`.
- `begin(db, cloud: TargetCloud, operation: str, user: str) -> Cluster` — raises `TransitionError` (→409) if not allowed; sets `starting|stopping`, `attempts=1`, resets step/progress, `log_path=data/cluster_ops/<cloud>-<utc ts>.log`.
- `set_step(db, cloud, step: str, done=0, total=0)`, `finish(db, cloud, ok: bool, error: str|None=None)` (`ok` → `up` for op up, `down` for op down; else `error`).
- `PUBLIC_CLOUDS = (TargetCloud.AWS, TargetCloud.GCP)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_up_allowed_from_down_and_error(session_factory): ...      # state -> starting, attempts == 1
def test_up_refused_while_starting_or_up(session_factory): ...     # raises TransitionError
def test_down_allowed_from_up_and_error(session_factory): ...      # state -> stopping
def test_finish_ok_maps_operation_to_stable_state(session_factory): ...  # up->up, down->down
def test_finish_failure_sets_error_and_message(session_factory): ...
```

- [ ] **Step 2:** Run `cd backend && poetry run pytest tests/test_cluster_state.py -v` → FAIL (import error).
- [ ] **Step 3:** Implement model, Alembic migration (follow `f1a2b3c4d5e6_add_finops_tables.py`), and `state.py`. `begin` must lock the row (`with_for_update()`) so two concurrent calls cannot both pass (SQLite ignores it; single replica + DB check is enough there).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(clusters): cluster state model and transitions (CNP-109)`.

### Task 4: Operation runner (CMP)

**Files:**
- Create: `backend/app/services/clusters/ops.py`, `backend/app/services/clusters/progress.py`
- Modify: `backend/app/core/config.py` (settings below)
- Test: `backend/tests/test_cluster_progress.py`, `backend/tests/test_cluster_ops.py`

**Settings (all from env, Task 8 wires them):** `CLUSTER_TF_ROOT: str = "/app/cloud"` (contains `aws/EKS`, `gcp/GKE`, `modules/`), `CLUSTER_OPS_AWS_ACCESS_KEY_ID`, `CLUSTER_OPS_AWS_SECRET_ACCESS_KEY`, `CLUSTER_OPS_GCP_SA_KEY_PATH`, `CLUSTER_OPS_VAULT_TOKEN`, `CLUSTER_TF_VARS_AWS: str = "{}"`, `CLUSTER_TF_VARS_GCP: str = "{}"` (JSON objects → `TF_VAR_<k>`; lists/sets JSON-encoded).

**Produces:**
- `progress.py`: `plan_total(line: str) -> int|None` (from `Plan: N to add` / `N to destroy`), `is_resource_done(line: str, operation: str) -> bool` (`Creation complete` for up, `Destruction complete` for down).
- `ops.py`: `async def run_operation(cloud: TargetCloud, operation: str) -> None` — the full step sequence; never raises (catches, calls `finish(ok=False, error=...)`). `def affected_apps(db, cloud) -> list[dict]` (`{"project": ..., "app": ..., "deployment_id": ...}` for deployments of projects whose `target_cloud == cloud` and status not deleted). `async def cluster_exists(cloud) -> bool|None` (`aws eks describe-cluster --name cnp-eks` / `gcloud container clusters describe cnp-gke`; `None` when the call itself fails).
- Internal `_stream(cmd: list[str], cwd: Path, env: dict, log, on_line) -> int` — `asyncio.create_subprocess_exec`, stdout+stderr merged, each line appended to the log file and passed to `on_line`; returns exit code.

**Step logic (decisions the implementer cannot guess):**
- Env: minimal (`PATH`, `HOME`), `TF_IN_AUTOMATION=1`, `TF_PLUGIN_CACHE_DIR=/app/cloud/.plugin-cache`, `KUBECONFIG=<tmp per op>`, `VAULT_ADDR=settings.VAULT_URL`, `VAULT_TOKEN=CLUSTER_OPS_VAULT_TOKEN`; aws: `AWS_ACCESS_KEY_ID/SECRET`, `AWS_REGION=eu-west-3`, `AWS_PAGER=""`; gcp: `GOOGLE_APPLICATION_CREDENTIALS`, `CLOUDSDK_CORE_PROJECT`, plus `gcloud auth activate-service-account --key-file` once per op; `TF_VAR_*` from the JSON setting.
- `init`: `terraform init -input=false`. `apply`: `terraform apply -auto-approve -input=false` (progress via `progress.py`). `destroy`: same with `destroy`.
- `kubeconfig`: aws `aws ssm get-parameter --name /cnp/eks/kubeconfig --query Parameter.Value --output text` → file; gcp `gcloud container clusters get-credentials cnp-gke --zone europe-west9-a`.
- `nodes`: `kubectl wait --for=condition=Ready nodes --all --timeout=10m`.
- `argocd`: poll (10 s, 10 min max) the Secret `cluster-<cloud>` in namespace `argocd` on the **on-prem** cluster via the existing `kube_client`; done when its `server` equals `terraform output -raw cluster_endpoint` (exists in both roots; GKE value has no scheme, so compare against `https://` + it). The secret persists across downs, so existence alone proves nothing.
- `wireguard`: poll (10 s, 10 min max) `kubectl -n cnp-vpn get pod -l app=wireguard -o jsonpath={.items[*].status.phase}` until `Running`.
- `checks` (down): kubeconfig, then refuse if `kubectl get svc -A -o jsonpath='{.items[?(@.spec.type=="LoadBalancer")].metadata.name}'` is non-empty: `finish(ok=False, error="Delete LoadBalancer Services first: <names>")`. If the cluster does not exist (`cluster_exists` False), skip the check.
- Any non-zero exit → `parse_state_lock(output)`; if locked, error message = `format_state_lock_message`.

- [ ] **Step 1: Write the failing tests**

```python
# test_cluster_progress.py
def test_plan_total_add(): assert plan_total("Plan: 42 to add, 0 to change, 0 to destroy.") == 42
def test_plan_total_destroy(): assert plan_total("Plan: 0 to add, 0 to change, 37 to destroy.") == 37
def test_resource_done(): assert is_resource_done("aws_vpc.this: Creation complete after 2s [id=vpc-1]", "up")
def test_resource_done_wrong_op(): assert not is_resource_done("x: Creation complete after 2s", "down")

# test_cluster_ops.py — _stream and the cloud CLIs patched with fakes
async def test_up_runs_steps_in_order_and_ends_up(...): ...   # steps seen == init,apply,kubeconfig,nodes,argocd,wireguard; state up
async def test_apply_failure_sets_error_and_keeps_log(...): ...  # exit 1 -> state error, log file has the failing line
async def test_down_refused_with_loadbalancer(...): ...      # state error, last_error mentions the service name, destroy never called
async def test_lock_error_message(...): ...                    # output with "Error acquiring the state lock" -> last_error from format_state_lock_message
```

- [ ] **Step 2:** Run `poetry run pytest tests/test_cluster_progress.py tests/test_cluster_ops.py -v` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(clusters): run cluster up/down operations (CNP-109)`.

### Task 5: Retry on startup and reconciliation (CMP)

**Files:**
- Create: `backend/app/services/clusters/recovery.py`
- Modify: `backend/app/main.py` (lifespan: `asyncio.create_task(resume_interrupted())` after `create_all`)
- Test: `backend/tests/test_cluster_recovery.py`

**Produces:** `async def resume_interrupted() -> None`, `async def reconcile(db) -> None` (called from the health path, Task 6), `def is_own_stale_lock(who: str, current_host: str) -> bool` — true iff `who` host part starts with `settings.CLUSTER_OPS_POD_PREFIX` (default `"arcl-cmp-backend-"`) and differs from `current_host` (`socket.gethostname()`).

- `resume_interrupted`: for each row in `starting|stopping`: if `attempts >= 2` → `finish(ok=False, error="Interrupted twice")`. Else run `terraform force-unlock -force <id>` only when a lock exists (detect via a `terraform plan -lock-timeout=0s -input=false` probe parsed with `parse_state_lock`) **and** `is_own_stale_lock`; foreign lock → `finish(ok=False, error=format_state_lock_message(...))`. Then `attempts += 1`, re-run `run_operation` as a background task.
- `reconcile`: for rows in `up|down` (never in-flight), `cluster_exists(cloud)`; `True` & `down` → `up`, `False` & `up` → `down`, `None` → no change. Missing row → create from `cluster_exists` (`None` → `down`). Rate-limit to once per 60 s per cloud (module-level timestamp).

- [ ] **Step 1: Write the failing tests**

```python
def test_own_stale_lock(): assert is_own_stale_lock("root@arcl-cmp-backend-7f9-abc", "arcl-cmp-backend-7f9-xyz")
def test_foreign_lock(): assert not is_own_stale_lock("hugo@laptop", "arcl-cmp-backend-7f9-xyz")
def test_same_pod_lock_not_stale(): assert not is_own_stale_lock("root@arcl-cmp-backend-1", "arcl-cmp-backend-1")
async def test_resume_retries_once(...): ...          # attempts 1 -> 2, run_operation called
async def test_resume_gives_up_after_two(...): ...    # attempts 2 -> error, run_operation not called
async def test_resume_foreign_lock_goes_error(...): ...  # no force-unlock call
async def test_reconcile_skips_in_flight(...): ...    # starting + cluster absent -> still starting
async def test_reconcile_fixes_drift(...): ...        # up + absent -> down
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(clusters): resume interrupted operations once (CNP-109)`.

### Task 6: API — health, up/down, logs, admin (CMP)

**Files:**
- Create: `backend/app/routers/clusters.py`
- Modify: `backend/app/services/authz.py` (move `is_cnp_admin` here; `routers/finops.py` imports it), `backend/app/services/monitoring_service.py` (`GlobalHealthResponse`), `backend/app/routers/infra.py`, `backend/app/main.py` (include router at `/api`), `backend/app/services/audit.py` (`ACTIONS`, `NOTABLE`)
- Test: `backend/tests/test_clusters_api.py`

**Produces (HTTP contract for Task 9):**
- `GlobalHealthResponse`: drop `openstack_vpn`, `aws_vpns` (and `_get_openstack_vpn_status`, `_get_aws_vpn_status`, `VPNStatus` if unused elsewhere — grep first); add `clusters: list[ClusterStatus]` and `can_operate_clusters: bool`.
- `ClusterStatus`: `cloud`, `state`, `operation`, `step`, `done`, `total`, `started_at`, `started_by`, `last_error`, `wireguard_running: bool|None` (only when `up`; same kubectl check as Task 4, cached with reconcile).
- `GET /api/infra/health` now takes `token: CurrentUser` to fill `can_operate_clusters = is_cnp_admin(token)`, calls `reconcile`.
- `POST /api/clusters/{cloud}/up` → 202 `ClusterStatus`. `POST /api/clusters/{cloud}/down` body `{"confirm": bool}`: without `confirm` → 200 `{"affected_apps": [...]}` and no state change; with `confirm: true` → 202. Both: 403 if not admin, 404 if cloud not `aws|gcp`, 409 on `TransitionError`. Start with `asyncio.create_task(run_operation(...))`.
- `GET /api/clusters/{cloud}/logs/stream` → SSE of `log_path`, admin only, stops when state is stable (copy the `deployments.py:310-349` loop).
- Audit: `("POST", "/api/clusters/{cloud}/up"): "cluster.up"`, `("POST", "/api/clusters/{cloud}/down"): "cluster.down"`, both in `NOTABLE`.

- [ ] **Step 1: Write the failing tests** (pattern: `tests/test_deployment_target_cloud.py`, `run_operation` patched)

```python
def test_up_forbidden_for_non_admin(...): ...           # 403
def test_up_starts_operation(...): ...                  # 202, state starting, run_operation called once
def test_double_up_conflicts(...): ...                  # second call 409, run_operation called once
def test_down_without_confirm_lists_apps(...): ...      # 200, affected_apps has the aws project app, state still up
def test_down_confirmed_starts(...): ...                # 202, state stopping
def test_health_exposes_clusters_and_admin_flag(...): ...  # can_operate_clusters True for cnp-admin, False otherwise
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run full suite `poetry run pytest -q` → PASS (finops tests still green after the `is_cnp_admin` move).
- [ ] **Step 5:** Commit `feat(clusters): admin API to start and stop public clusters (CNP-109)`.

### Task 7: Block deployments to a cluster that is not up (CMP, CNP-128)

**Files:**
- Modify: `backend/app/routers/deployments.py` (after `target_cloud` is resolved, ~line 213), and every other path that deploys to a cloud: grep `target_cloud` in `routers/` and `services/release/` — redeploy/promote endpoints included.
- Test: `backend/tests/test_deployment_target_cloud.py` (extend)

**Produces:** `def ensure_cluster_up(db, cloud: TargetCloud) -> None` in `services/clusters/state.py` — no-op for `ONPREM`; raises `HTTPException(409, "The <AWS|GCP> cluster is <state>; deployments resume once it is up.")` otherwise when state ≠ `up`. Missing row counts as not up.

- [ ] **Step 1: Write the failing tests**

```python
def test_deploy_refused_when_cluster_down(...): ...    # aws project, cluster down -> 409, message contains "down"
def test_deploy_allowed_when_cluster_up(...): ...      # 202
def test_onprem_ignores_cluster_state(...): ...        # onprem project, aws down -> 202
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement + call at each deploy entry point. **Step 4:** Run full suite → PASS.
- [ ] **Step 5:** Commit `feat(deployments): refuse deployments to a stopped cluster (CNP-128)`.

### Task 8: Image, CI, Helm (CMP)

**Files:**
- Modify: `backend/Dockerfile`, `backend/.gitignore` (or root `.gitignore`: `backend/cloud/`), `.github/workflows/build-and-push.yml`, `.github/workflows/test.yml`, `helm/arcl-cmp/values.yaml`, `helm/arcl-cmp/templates/backend-deployment.yaml`, `Makefile` (target `cloud`), `README.md` (one line)

- [ ] **Step 1: CI checkout.** In both workflows, before building the backend image: `actions/checkout@v7` with `repository: 3-Istor/Cloud`, `token: ${{ secrets.CLOUD_REPO_TOKEN }}`, `path: backend/cloud-src`, `sparse-checkout: terraform/public`. Local dev: `make cloud` does the same with `git clone --depth 1 --filter=blob:none --sparse`.
- [ ] **Step 2: Dockerfile.** Final stage: install `unzip`, aws CLI v2 (official zip), kubectl (pinned, e.g. `v1.33.x`, matching cluster versions in `aws/EKS/variables.tf` ± 1), Google Cloud CLI + `google-cloud-cli-gke-gcloud-auth-plugin` (apt repo). `COPY cloud-src/terraform/public /app/cloud`, then `RUN for d in aws/EKS gcp/GKE; do (cd /app/cloud/$d && TF_PLUGIN_CACHE_DIR=/app/cloud/.plugin-cache terraform init -backend=false -input=false); done`. Remove copied `.runtime`/`*.tfvars` if any. Exclude `cloud-src` from the `COPY . .` via `.dockerignore` except what is copied explicitly.
- [ ] **Step 3: Helm.** `values.yaml`: `clusterOps.tfVars.aws` / `.gcp` (objects: `api_allowed_cidrs`, `admin_principal_arns`, `service_cidr`, `vpn_source_cidrs`, `node_instance_type` / `node_machine_type` — values from Hugo's current tfvars, plus `cmp_operator_arn` in `admin_principal_arns`). Deployment: env `CLUSTER_TF_VARS_AWS/GCP` (`toJson`), `CLUSTER_OPS_*` from Secret `arcl-cmp-cluster-ops` (Task 2 keys), GCP key mounted as file at `/var/run/cluster-ops/gcp-sa-key.json`, `CLUSTER_OPS_POD_PREFIX` = `{{ include fullname }}-backend-`. Check `/app/data` PVC is mounted (it is per builder check).
- [ ] **Step 4: Verify.** `docker build -t cmp-backend:test backend` (after `make cloud`), then `docker run --rm cmp-backend:test sh -c 'terraform version && aws --version && gcloud version && kubectl version --client && ls /app/cloud/aws/EKS/.terraform* ; ls /app/cloud/.plugin-cache'` → all print, providers cached. `helm template helm/arcl-cmp | grep -A2 CLUSTER_TF_VARS_AWS` → JSON present.
- [ ] **Step 5:** Commit `build(backend): bake cluster terraform and cloud CLIs (CNP-109)`.

### Task 9: Home page EKS/GKE cards (CMP frontend)

**Files:**
- Create: `frontend/src/components/dashboard/ClusterCard.tsx`, `frontend/src/components/dashboard/ClusterLogsDialog.tsx`
- Modify: `frontend/src/components/dashboard/GlobalInfraHealth.tsx` (replace the AWS VPNs and OpenStack VPN columns), `frontend/src/types/index.ts` (`GlobalHealthResponse`, `ClusterStatus`), `frontend/src/lib/api.ts` (`startCluster(cloud)`, `stopCluster(cloud, confirm)`, logs URL helper)

**Decisions:**
- Columns: OpenStack Hypervisors | EKS | GKE. Card styled like a hypervisor row: name, badge (`up` blue as today, `down` muted, `starting/stopping` amber, `error` destructive), WireGuard line when `up`.
- Buttons only when `can_operate_clusters`: UP when `down|error`, DOWN when `up`.
- DOWN: call without confirm → shadcn dialog listing `affected_apps` (`project / app`), "Stop cluster" button calls with `confirm: true`. Empty list → dialog still shown with "No app runs on this cluster."
- In-flight: step bar with the 6 (up) or 2 (down) segments from Global Constraints; `apply`/`destroy` segment fills by `done/total`; under it the step label. "Logs" opens `ClusterLogsDialog` (EventSource on the SSE endpoint, autoscroll, monospace).
- Toast: in `useGlobalHealth`, keep previous `clusters`; on transition `starting|stopping` → `up|down` call `toast.success("EKS is up")` / `"GKE is down"`; → `error` call `toast.error("EKS start failed", { description: last_error })`. Since the hook is used on home only, mount the toast watcher in `app/layout.tsx` via a tiny `ClusterToasts` client component that uses `useGlobalHealth(15000)` and renders nothing — toasts then fire on any page.

- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Verify `cd frontend && npm run lint && npm run build` → no errors.
- [ ] **Step 3:** Commit (UI is checked end-to-end in Task 10) `feat(frontend): start and stop EKS/GKE from the home page (CNP-109)`.

### Task 10: End-to-end on real clusters (Hugo + agent, after Hugo applied Task 2)

- [ ] **Step 1:** Confirm CMP egress IP: `kubectl -n <cmp ns> exec deploy/<backend> -- curl -s ifconfig.me` ∈ `api_allowed_cidrs` (expected `176.186.130.237`). If not, add it to Helm `tfVars`.
- [ ] **Step 2:** UP GKE from the home page → steps progress, toast "GKE is up", WireGuard Running, ArgoCD shows cluster `gcp` Successful.
- [ ] **Step 3:** Deploy a test app to a `gcp` project → OK. DOWN GKE → dialog lists it → confirm → toast "GKE is down". Deploy to that project → 409.
- [ ] **Step 4:** UP GKE again → the test app comes back via ArgoCD.
- [ ] **Step 5:** During an UP of EKS, `kubectl rollout restart` the backend → operation resumes (attempts 2) and ends `up`.
- [ ] **Step 6:** PR per repo, titles `CNP-109: …`, linked to CNP-109 / CNP-128.
