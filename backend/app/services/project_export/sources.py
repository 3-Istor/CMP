"""
Adapters between the export and the live platform: the cluster, GitHub,
CloudNativePG, the project's buckets, Vault, Keycloak and Loki.

Each is a small class behind a Protocol, so tests and the restore drill run
the export against fakes, or against a plain Postgres and a Garage container,
without platform credentials. All of them are blocking except the Vault and
Loki readers; the collectors call the blocking ones off the event loop.
"""

import base64
import os
import re
import subprocess
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import boto3
import psycopg2
from botocore.config import Config
from psycopg2 import sql

from app.core.config import settings
from app.models.project import TargetCloud
from app.services import (
    activity_sources,
    customer_identity,
    keycloak_service,
    vault_client,
)
from app.services.kube_client import kube_get, kube_list

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
    async def read(self, project: str) -> dict[str, dict[str, Any]]: ...


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

    def link(self, key: str, seconds: int) -> str: ...

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


class PgDumper:
    """
    A plain SQL dump with ``pg_dump``, restorable by ``psql`` into any
    PostgreSQL, plus the exact row count of every table so a restore can be
    checked. *resolve* gives the connection (host, port, dbname, user,
    password) of a :class:`DatabaseRef`.
    """

    def __init__(self, resolve: Callable[[DatabaseRef], dict]) -> None:
        self.resolve = resolve

    def dump(self, ref: DatabaseRef, dest: Path) -> dict:
        conn = self.resolve(ref)
        result = subprocess.run(
            [
                "pg_dump",
                "--format=plain",
                "--no-owner",
                "--no-privileges",
                f"--host={conn['host']}",
                f"--port={conn['port']}",
                f"--username={conn['user']}",
                f"--dbname={conn['dbname']}",
                f"--file={dest}",
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            # Through the environment, never argv: argv shows in ps.
            env={**os.environ, "PGPASSWORD": conn["password"]},
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump failed: {result.stderr.strip()[-300:]}"
            )
        return {
            "database": conn["dbname"],
            "row_counts": _row_counts(conn),
        }


def _row_counts(conn: dict) -> dict[str, int]:
    counts = {}
    with psycopg2.connect(
        host=conn["host"],
        port=conn["port"],
        dbname=conn["dbname"],
        user=conn["user"],
        password=conn["password"],
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN "
            "('pg_catalog', 'information_schema') ORDER BY 1, 2"
        )
        for schema, table in cursor.fetchall():
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            counts[f"{schema}.{table}"] = cursor.fetchone()[0]
    connection.close()
    return counts


def cnpg_resolver(cloud: TargetCloud) -> Callable[[DatabaseRef], dict]:
    """
    The owner's connection to a CNPG cluster, from the secret the chart
    bootstraps it with. Needs the CMP to reach ``<cluster>-rw`` on 5432,
    which the project network policies do not allow yet.
    """

    def resolve(ref: DatabaseRef) -> dict:
        secret = kube_get(
            f"/api/v1/namespaces/{ref.namespace}/secrets/{ref.secret}",
            cloud=cloud,
        )
        data = (secret or {}).get("data") or {}
        if "username" not in data or "password" not in data:
            raise RuntimeError(
                f"no credentials in {ref.namespace}/{ref.secret}"
            )
        return {
            "host": f"{ref.cluster}-rw.{ref.namespace}.svc.cluster.local",
            "port": 5432,
            "dbname": ref.database,
            "user": base64.b64decode(data["username"]).decode(),
            "password": base64.b64decode(data["password"]).decode(),
        }

    return resolve


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
    ) -> None:
        self.bucket = bucket
        self.client = _s3(endpoint, access_key, secret_key, region)

    @classmethod
    def from_settings(cls) -> "S3ExportStore":
        return cls(
            settings.EXPORT_S3_ENDPOINT or settings.AVATARS_S3_ENDPOINT,
            settings.EXPORT_S3_BUCKET,
            settings.EXPORT_S3_ACCESS_KEY_ID,
            settings.EXPORT_S3_SECRET_ACCESS_KEY,
            settings.EXPORT_S3_REGION,
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
    """The project's KV v2 mount, ``project-<name>``."""

    async def read(self, project: str) -> dict[str, dict[str, Any]]:
        mount = f"project-{project}"
        return {
            path: await vault_client.read_kv(mount, path)
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
