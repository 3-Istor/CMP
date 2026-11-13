"""
Adapters between the export and the live platform: the cluster, GitHub,
CloudNativePG, the project's buckets, Vault, Keycloak and Loki.

Each is a small class behind a Protocol, so tests and the restore drill run
the export against fakes, or against a kind cluster and a Garage container,
without platform credentials. All of them are blocking except the Vault and
Loki readers; the collectors call the blocking ones off the event loop.
"""

import base64
import hashlib
import os
import re
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import settings
from app.models.project import TargetCloud
from app.services import (
    activity_sources,
    customer_identity,
    keycloak_service,
    vault_client,
)
from app.services.kube_client import (
    kube_create,
    kube_delete_collection,
    kube_get,
    kube_list,
)

PROJECT_LABEL = "cnp.3istor.com/project"

# What a project's namespaces hold that is its own: the objects Argo CD
# renders from the app charts. Pods, ReplicaSets, Events, reports and other
# controller output are left out, and so is anything a controller owns
# (ownerReferences): the restored workloads recreate them.
NAMESPACED_KINDS: tuple[tuple[str, str, str], ...] = (
    ("v1", "configmaps", "ConfigMap"),
    ("v1", "services", "Service"),
    ("v1", "persistentvolumeclaims", "PersistentVolumeClaim"),
    ("v1", "serviceaccounts", "ServiceAccount"),
    ("apps/v1", "deployments", "Deployment"),
    ("apps/v1", "statefulsets", "StatefulSet"),
    ("batch/v1", "cronjobs", "CronJob"),
    ("argoproj.io/v1alpha1", "rollouts", "Rollout"),
    ("gateway.networking.k8s.io/v1", "httproutes", "HTTPRoute"),
    ("gateway.envoyproxy.io/v1alpha1", "securitypolicies", "SecurityPolicy"),
    ("postgresql.cnpg.io/v1", "clusters", "Cluster"),
    ("postgresql.cnpg.io/v1", "scheduledbackups", "ScheduledBackup"),
    ("ricoberger.de/v1alpha1", "vaultsecrets", "VaultSecret"),
    ("guard.3istor.com/v1alpha1", "offhoursschedules", "OffhoursSchedule"),
    ("objectbucket.io/v1alpha1", "objectbucketclaims", "ObjectBucketClaim"),
)

ARGO_APPLICATIONS = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"


@dataclass(frozen=True)
class DatabaseRef:
    namespace: str
    cluster: str
    database: str
    # Secret holding the owner's username and password.
    secret: str
    # The cluster's own image: pg_dump of the server's exact version.
    image: str


@dataclass(frozen=True)
class BucketRef:
    namespace: str
    claim: str
    bucket: str
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)


class ClusterReader(Protocol):
    def namespaces(self, project: str) -> list[dict]: ...

    def objects(self, namespace: str) -> list[dict]: ...

    def secret_names(self, namespace: str) -> list[dict]: ...

    def applications(self, project: str) -> list[dict]: ...


class RepoArchiver(Protocol):
    def bundle(self, url: str, dest: Path, token: str | None) -> dict: ...


class DatabaseDumper(Protocol):
    def dump(self, ref: DatabaseRef, dest: Path) -> dict: ...


class BucketReader(Protocol):
    def buckets(
        self, project: str, objects: list[dict]
    ) -> list[BucketRef]: ...

    def download(
        self, ref: BucketRef, target: Callable[[str], Path | None]
    ) -> list[str]: ...


class SecretReader(Protocol):
    async def read(self, project: str) -> dict[str, list[str]]: ...


class IdentityReader(Protocol):
    def realm(self, name: str) -> tuple[dict, list[dict]] | None: ...

    def members(self, project: str) -> list[dict]: ...


class LogReader(Protocol):
    def pages(
        self,
        project: str,
        namespaces: list[str],
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[list[dict]]: ...


class ExportStore(Protocol):
    def put(self, key: str, path: Path) -> None: ...

    def fetch(self, key: str, path: Path) -> None: ...

    def link(self, key: str, seconds: int) -> str: ...

    def upload_link(self, key: str, seconds: int) -> str: ...

    def delete(self, key: str) -> None: ...


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


def _collection(group_version: str, plural: str, namespace: str) -> str:
    root = "/api" if group_version == "v1" else "/apis"
    return f"{root}/{group_version}/namespaces/{namespace}/{plural}"


class KubeClusterReader:
    def __init__(self, cloud: TargetCloud) -> None:
        self.cloud = cloud

    def namespaces(self, project: str) -> list[dict]:
        return kube_list(
            "/api/v1/namespaces",
            {"labelSelector": f"{PROJECT_LABEL}={project}"},
            cloud=self.cloud,
        )

    def objects(self, namespace: str) -> list[dict]:
        found = []
        for group_version, plural, kind in NAMESPACED_KINDS:
            for item in kube_list(
                _collection(group_version, plural, namespace), cloud=self.cloud
            ):
                # List responses leave apiVersion and kind off the items.
                found.append(
                    {"apiVersion": group_version, "kind": kind, **item}
                )
        return found

    def secret_names(self, namespace: str) -> list[dict]:
        # Reduced here, so no secret value leaves this function.
        return [
            {
                "name": item["metadata"]["name"],
                "type": item.get("type", ""),
                "keys": sorted((item.get("data") or {}).keys()),
            }
            for item in kube_list(
                _collection("v1", "secrets", namespace), cloud=self.cloud
            )
        ]

    def applications(self, project: str) -> list[dict]:
        # Argo CD runs on-prem for every cloud.
        return [
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                **app,
            }
            for app in kube_list(
                ARGO_APPLICATIONS,
                {"labelSelector": f"{PROJECT_LABEL}={project}"},
            )
        ]


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


class GitError(RuntimeError):
    pass


def _git(args: list[str], secret: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()[-300:]
        if secret:
            message = message.replace(secret, "***")
        raise GitError(f"git {args[0]} failed: {message}")
    return result.stdout.strip()


class GitRepoArchiver:
    """Every ref of a repository as one ``git bundle``."""

    def bundle(self, url: str, dest: Path, token: str | None) -> dict:
        source = url
        if token and url.startswith("https://github.com/"):
            source = url.replace(
                "https://", f"https://x-access-token:{token}@", 1
            )
        with tempfile.TemporaryDirectory() as work:
            mirror = f"{work}/repo.git"
            _git(["clone", "--mirror", "--quiet", source, mirror], token)
            _git(["-C", mirror, "bundle", "create", str(dest), "--all"])
            head = _git(["-C", mirror, "rev-parse", "HEAD"])
            refs = _git(["-C", mirror, "for-each-ref", "--format=%(refname)"])
        return {"repository": url, "head": head, "refs": len(refs.split())}


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

EXPORT_LABEL = "cnp.3istor.com/export"
DUMP_TIMEOUT_SECONDS = 3600

# Runs in the database's own image, inside the project namespace: the CMP
# never connects to a project database nor reads its password. pg_dump and
# psql read PG* from the environment; the two files go to the export bucket
# through presigned PUT URLs good for this export only.
DUMP_SCRIPT = r"""set -eu
pg_dump --format=plain --no-owner --no-privileges --file=/work/dump.sql
psql -AtX -v ON_ERROR_STOP=1 -c "SELECT format('SELECT %L, count(*) FROM %I.%I;', schemaname || '.' || tablename, schemaname, tablename) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1" > /work/count.sql
psql -AtX -v ON_ERROR_STOP=1 -F "$(printf '\t')" -f /work/count.sql > /work/counts.tsv
python3 - <<'PY'
import os, urllib.request
for path, var in (("/work/dump.sql", "DUMP_URL"), ("/work/counts.tsv", "COUNTS_URL")):
    with open(path, "rb") as body:
        request = urllib.request.Request(
            os.environ[var], data=body, method="PUT",
            headers={"Content-Length": str(os.path.getsize(path))},
        )
        urllib.request.urlopen(request, timeout=600).close()
PY
"""


class KubeApi(Protocol):
    def get(self, path: str, params: dict | None = None) -> dict | None: ...

    def create(self, path: str, body: dict) -> dict: ...

    def delete_collection(self, path: str, params: dict) -> None: ...


class ClusterKubeApi:
    """The CMP's own identity on the project's cluster."""

    def __init__(self, cloud: TargetCloud) -> None:
        self.cloud = cloud

    def get(self, path: str, params: dict | None = None) -> dict | None:
        return kube_get(path, params, cloud=self.cloud)

    def create(self, path: str, body: dict) -> dict:
        return kube_create(path, body, cloud=self.cloud)

    def delete_collection(self, path: str, params: dict) -> None:
        kube_delete_collection(path, params, cloud=self.cloud)


def _env_secret(name: str, secret: str, key: str) -> dict:
    return {
        "name": name,
        "valueFrom": {"secretKeyRef": {"name": secret, "key": key}},
    }


def dump_job(
    ref: DatabaseRef, name: str, urls_secret: str, labels: dict
) -> dict:
    """
    The Job that dumps one CNPG database. TLS is verified against the
    cluster's own CA; the pod runs with the restricted profile, no service
    account token and a read-only root filesystem.
    """
    env = [
        {"name": "PGHOST", "value": f"{ref.cluster}-rw"},
        {"name": "PGPORT", "value": "5432"},
        {"name": "PGDATABASE", "value": ref.database},
        {"name": "PGSSLMODE", "value": "verify-full"},
        {"name": "PGSSLROOTCERT", "value": "/ca/ca.crt"},
        {"name": "HOME", "value": "/work"},
        {"name": "TMPDIR", "value": "/work"},
        _env_secret("PGUSER", ref.secret, "username"),
        _env_secret("PGPASSWORD", ref.secret, "password"),
        _env_secret("DUMP_URL", urls_secret, "DUMP_URL"),
        _env_secret("COUNTS_URL", urls_secret, "COUNTS_URL"),
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": ref.namespace,
            "labels": labels,
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": DUMP_TIMEOUT_SECONDS,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 26,
                        "runAsGroup": 26,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "pg-dump",
                            "image": ref.image,
                            "command": ["sh", "-c", DUMP_SCRIPT],
                            "env": env,
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "128Mi"},
                                "limits": {"memory": "1Gi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "terminationMessagePolicy": "FallbackToLogsOnError",
                            "volumeMounts": [
                                {"name": "work", "mountPath": "/work"},
                                {
                                    "name": "ca",
                                    "mountPath": "/ca",
                                    "readOnly": True,
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "work", "emptyDir": {}},
                        {
                            "name": "ca",
                            "secret": {
                                "secretName": f"{ref.cluster}-ca",
                                "items": [{"key": "ca.crt", "path": "ca.crt"}],
                            },
                        },
                    ],
                },
            },
        },
    }


class JobDumper:
    """
    A plain SQL dump of a CNPG database, made by a short-lived Job in the
    project namespace, plus the exact row count of every table so a restore
    can be checked.

    Least privilege: the CMP creates a Job and a Secret holding two presigned
    PUT URLs (one object each, this export only, one hour), then fetches the
    two objects from its own bucket. It needs no network path to the
    database, never sees its password, and needs no pods/exec or pods/log.
    """

    def __init__(
        self,
        kube: KubeApi,
        store: ExportStore,
        export_id: str,
        poll_seconds: float = 5,
    ) -> None:
        self.kube = kube
        self.store = store
        self.export_id = export_id
        self.poll_seconds = poll_seconds

    def dump(self, ref: DatabaseRef, dest: Path) -> dict:
        if not ref.image:
            raise RuntimeError(f"{ref.cluster}: no imageName on the Cluster")
        digest = hashlib.sha256(ref.cluster.encode()).hexdigest()[:8]
        name = f"cnp-export-{self.export_id[:8]}-{digest}"
        prefix = f"tmp/{self.export_id}/{ref.namespace}/{ref.cluster}"
        keys = {
            "DUMP_URL": f"{prefix}/dump.sql",
            "COUNTS_URL": f"{prefix}/counts.tsv",
        }
        labels = {EXPORT_LABEL: self.export_id}
        core = f"/api/v1/namespaces/{ref.namespace}"
        jobs = f"/apis/batch/v1/namespaces/{ref.namespace}/jobs"
        try:
            self.kube.create(
                f"{core}/secrets",
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": name, "labels": labels},
                    "type": "Opaque",
                    "stringData": {
                        var: self.store.upload_link(key, DUMP_TIMEOUT_SECONDS)
                        for var, key in keys.items()
                    },
                },
            )
            self.kube.create(jobs, dump_job(ref, name, name, labels))
            self._wait(core, jobs, name)
            self.store.fetch(keys["DUMP_URL"], dest)
            with tempfile.TemporaryDirectory() as work:
                counts = Path(work) / "counts.tsv"
                self.store.fetch(keys["COUNTS_URL"], counts)
                row_counts = _parse_counts(counts.read_text())
        finally:
            selector = {"labelSelector": f"{EXPORT_LABEL}={self.export_id}"}
            self.kube.delete_collection(
                jobs, {**selector, "propagationPolicy": "Background"}
            )
            self.kube.delete_collection(f"{core}/secrets", selector)
            for key in keys.values():
                try:
                    self.store.delete(key)
                except Exception:  # pylint: disable=broad-except
                    pass
        return {"database": ref.database, "row_counts": row_counts}

    def _wait(self, core: str, jobs: str, name: str) -> None:
        deadline = time.monotonic() + DUMP_TIMEOUT_SECONDS + 60
        while time.monotonic() < deadline:
            status = (self.kube.get(f"{jobs}/{name}") or {}).get(
                "status"
            ) or {}
            if status.get("succeeded"):
                return
            if status.get("failed"):
                raise RuntimeError(f"dump job failed: {self._why(core, name)}")
            time.sleep(self.poll_seconds)
        raise RuntimeError("dump job timed out")

    def _why(self, core: str, name: str) -> str:
        pods = self.kube.get(
            f"{core}/pods", {"labelSelector": f"job-name={name}"}
        )
        for pod in (pods or {}).get("items", []):
            for container in (pod.get("status") or {}).get(
                "containerStatuses", []
            ):
                terminated = (container.get("state") or {}).get(
                    "terminated"
                ) or {}
                if terminated.get("message"):
                    return terminated["message"].strip()[-300:]
        return "no message"


def _parse_counts(text: str) -> dict[str, int]:
    counts = {}
    for line in text.splitlines():
        if line:
            table, _, count = line.partition("\t")
            counts[table] = int(count)
    return counts


# ---------------------------------------------------------------------------
# Object storage
# ---------------------------------------------------------------------------


def _s3(endpoint: str, access_key: str, secret_key: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3},
        ),
    )


class S3BucketReader:
    """Every object of the project's buckets, found by *discover*."""

    def __init__(
        self, discover: Callable[[str, list[dict]], list[BucketRef]]
    ) -> None:
        self.discover = discover

    def buckets(self, project: str, objects: list[dict]) -> list[BucketRef]:
        return self.discover(project, objects)

    def download(
        self, ref: BucketRef, target: Callable[[str], Path | None]
    ) -> list[str]:
        """The keys written; a key *target* refuses (None) is skipped."""
        client = _s3(ref.endpoint, ref.access_key, ref.secret_key, "us-east-1")
        keys = []
        for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=ref.bucket
        ):
            for entry in page.get("Contents", []):
                path = target(entry["Key"])
                if path is None:
                    continue
                client.download_file(ref.bucket, entry["Key"], str(path))
                keys.append(entry["Key"])
        return keys


def _decoded(data: dict, key: str) -> str:
    return base64.b64decode(data.get(key, "")).decode()


def obc_buckets(
    cloud: TargetCloud,
) -> Callable[[str, list[dict]], list[BucketRef]]:
    """
    The project's ObjectBucketClaims, with the credentials Rook puts in the
    Secret and ConfigMap named after each claim. CNPG backup buckets
    (``cnpg-*``) are left out: the logical dump carries their content.
    """

    def discover(_project: str, objects: list[dict]) -> list[BucketRef]:
        refs = []
        for claim in objects:
            if claim.get("kind") != "ObjectBucketClaim":
                continue
            bucket = (claim.get("spec") or {}).get("bucketName") or ""
            if bucket.startswith("cnpg-"):
                continue
            namespace = claim["metadata"]["namespace"]
            name = claim["metadata"]["name"]
            secret = kube_get(
                f"/api/v1/namespaces/{namespace}/secrets/{name}", cloud=cloud
            )
            config = kube_get(
                f"/api/v1/namespaces/{namespace}/configmaps/{name}",
                cloud=cloud,
            )
            if not secret or not config:
                raise RuntimeError(
                    f"bucket claim {namespace}/{name} not bound"
                )
            data, conf = secret.get("data") or {}, config.get("data") or {}
            refs.append(
                BucketRef(
                    namespace=namespace,
                    claim=name,
                    bucket=conf["BUCKET_NAME"],
                    endpoint=f"http://{conf['BUCKET_HOST']}:"
                    f"{conf.get('BUCKET_PORT', '80')}",
                    access_key=_decoded(data, "AWS_ACCESS_KEY_ID"),
                    secret_key=_decoded(data, "AWS_SECRET_ACCESS_KEY"),
                )
            )
        return refs

    return discover


class S3ExportStore:
    """The export bucket."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
        internal_endpoint: str = "",
    ) -> None:
        self.bucket = bucket
        self.client = _s3(endpoint, access_key, secret_key, region)
        # Upload links are used by dump Jobs inside the cluster, which reach
        # the store by its Service; download links by the customer.
        self.internal = (
            _s3(internal_endpoint, access_key, secret_key, region)
            if internal_endpoint
            else self.client
        )

    @classmethod
    def from_settings(cls) -> "S3ExportStore":
        return cls(
            settings.EXPORT_S3_ENDPOINT or settings.AVATARS_S3_ENDPOINT,
            settings.EXPORT_S3_BUCKET,
            settings.EXPORT_S3_ACCESS_KEY_ID,
            settings.EXPORT_S3_SECRET_ACCESS_KEY,
            settings.EXPORT_S3_REGION,
            settings.EXPORT_S3_INTERNAL_ENDPOINT,
        )

    def put(self, key: str, path: Path) -> None:
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": "application/gzip"},
        )

    def link(self, key: str, seconds: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=seconds,
        )

    def fetch(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))

    def upload_link(self, key: str, seconds: int) -> str:
        return self.internal.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=seconds,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def export_store_configured() -> bool:
    return bool(
        settings.EXPORT_S3_ACCESS_KEY_ID
        and settings.EXPORT_S3_SECRET_ACCESS_KEY
    )


# ---------------------------------------------------------------------------
# Vault, Keycloak, Loki
# ---------------------------------------------------------------------------


class VaultSecretReader:
    """
    The secret paths of the project's KV v2 mount, ``project-<name>``, and
    their key names. Never the values: they stay in Vault, readable by the
    project's members, for the whole transition.
    """

    async def read(self, project: str) -> dict[str, list[str]]:
        mount = f"project-{project}"
        return {
            path: await vault_client.read_key_names(mount, path)
            for path in await vault_client.list_paths(mount)
        }


class KeycloakIdentityReader:
    def realm(self, name: str) -> tuple[dict, list[dict]] | None:
        return customer_identity.export_realm(name)

    def members(self, project: str) -> list[dict]:
        return keycloak_service.list_project_members(project)


LOKI_PAGE = 5000
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class LokiLogReader:
    """The project's log lines, oldest first, a page at a time."""

    async def pages(
        self,
        project: str,
        namespaces: list[str],
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[list[dict]]:
        # The exact namespaces, never a prefix: project "shop" must not get
        # the logs of project "shop-eu". Kubernetes names are DNS labels, so
        # nothing in them needs escaping in the regex.
        if not namespaces or not all(_DNS_LABEL.match(n) for n in namespaces):
            raise ValueError(f"not namespace names: {namespaces!r}")
        query = f'{{namespace=~"{"|".join(namespaces)}"}}'
        seen_at_start: set[tuple] = set()
        while start < end:
            lines = await activity_sources.loki_lines(
                query,
                start,
                end,
                LOKI_PAGE,
                direction="forward",
                tenant=activity_sources.project_tenants(project),
            )
            lines.sort(key=lambda entry: entry[0])
            fresh = [
                entry
                for entry in lines
                if (entry[0], tuple(sorted(entry[1].items())), entry[2])
                not in seen_at_start
            ]
            if fresh:
                yield [
                    {"time": when, "labels": labels, "line": line}
                    for when, labels, line in fresh
                ]
            if len(lines) < LOKI_PAGE:
                return
            # Loki's start is inclusive: restart at the last timestamp and
            # skip what was already written at it.
            last = lines[-1][0]
            seen_at_start = {
                (when, tuple(sorted(labels.items())), line)
                for when, labels, line in lines
                if when == last
            }
            start = last if last > start else last + timedelta(microseconds=1)
