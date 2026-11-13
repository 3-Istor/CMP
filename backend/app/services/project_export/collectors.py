"""
One collector per kind of data of the inventory. Each writes its files into
the bundle and records them; a failure is recorded as a problem of the
export by the runner, and the other collectors still run.
"""

import asyncio
import importlib
import json
import pkgutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models
from app.core.config import settings
from app.core.database import Base
from app.services.project_export import bundle as fmt
from app.services.project_export.bundle import Bundle, UnsafePathError
from app.services.project_export.inventory import EXPORTED_TABLES
from app.services.project_export.sources import (
    BucketReader,
    ClusterReader,
    DatabaseDumper,
    DatabaseRef,
    IdentityReader,
    LogReader,
    RepoArchiver,
    SecretReader,
)


@dataclass
class Sources:
    cluster: ClusterReader
    repos: RepoArchiver
    databases: DatabaseDumper
    buckets: BucketReader
    secrets: SecretReader
    identity: IdentityReader
    logs: LogReader


# ---------------------------------------------------------------------------
# CMP database
# ---------------------------------------------------------------------------


def load_models() -> None:
    # Base.metadata only knows the models imported so far.
    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")


def collect_cmp(session: Session, project: str, bundle: Bundle) -> list[dict]:
    """Every exported table as JSON Lines; returns the project's apps."""
    load_models()
    tables = Base.metadata.tables
    deployments = tables["deployments"]
    ids = list(
        session.execute(
            select(deployments.c.id).where(deployments.c.project_id == project)
        ).scalars()
    )
    apps: list[dict] = []
    for name, spec in EXPORTED_TABLES.items():
        table = tables[name]
        columns = [c for c in table.columns if c.name not in spec.drop]
        rows = [
            dict(row._mapping)  # pylint: disable=protected-access
            for row in session.execute(
                select(*columns)
                .where(spec.where(table, project, ids))
                .order_by(*table.primary_key.columns)
            )
        ]
        if name == "deployments":
            apps = rows
        bundle.add_jsonl(
            f"cmp/{name}.jsonl",
            rows,
            item_type="cmp.table",
            source=f"cmp-db:{name}",
            metadata={"columns": [c.name for c in columns]},
        )
    return apps


# ---------------------------------------------------------------------------
# Kubernetes and GitOps
# ---------------------------------------------------------------------------

_DROPPED_METADATA = (
    "uid",
    "resourceVersion",
    "generation",
    "creationTimestamp",
    "managedFields",
    "selfLink",
    "deletionTimestamp",
    "deletionGracePeriodSeconds",
)
_DROPPED_ANNOTATIONS = ("kubectl.kubernetes.io/last-applied-configuration",)
# Assigned by the cluster, and refused when re-applied elsewhere.
_DROPPED_SPEC = {
    "Service": ("clusterIP", "clusterIPs"),
    "PersistentVolumeClaim": ("volumeName",),
}
_SKIPPED_NAMES = {("ConfigMap", "kube-root-ca.crt")}


def sanitize(obj: dict) -> dict | None:
    """
    The object as it can be applied to another cluster, or None when it is
    controller output that the restored workloads recreate.
    """
    metadata = obj.get("metadata") or {}
    if metadata.get("ownerReferences"):
        return None
    if (obj.get("kind"), metadata.get("name")) in _SKIPPED_NAMES:
        return None
    clean_metadata = {
        k: v for k, v in metadata.items() if k not in _DROPPED_METADATA
    }
    annotations = {
        k: v
        for k, v in (metadata.get("annotations") or {}).items()
        if k not in _DROPPED_ANNOTATIONS
    }
    clean_metadata.pop("annotations", None)
    if annotations:
        clean_metadata["annotations"] = annotations
    clean = {k: v for k, v in obj.items() if k not in ("metadata", "status")}
    clean["metadata"] = clean_metadata
    dropped = _DROPPED_SPEC.get(obj.get("kind", ""), ())
    if dropped and isinstance(clean.get("spec"), dict):
        clean["spec"] = {
            k: v for k, v in clean["spec"].items() if k not in dropped
        }
    return clean


def _add_object(bundle: Bundle, rel: str, obj: dict, item_type: str) -> None:
    metadata = obj["metadata"]
    # JSON rather than YAML: kubectl reads YAML 1.1, where an unquoted "on"
    # or "no" written by a YAML 1.2 emitter turns into a boolean.
    bundle.add_json(
        rel,
        obj,
        item_type=item_type,
        source="kubernetes",
        metadata={
            "apiVersion": obj["apiVersion"],
            "kind": obj["kind"],
            "name": metadata["name"],
            "namespace": metadata.get("namespace"),
        },
    )


async def collect_cluster(
    cluster: ClusterReader, project: str, bundle: Bundle
) -> tuple[list[str], list[dict]]:
    """The project's namespaces and their objects; returns both."""
    found: list[dict] = []
    namespaces = await asyncio.to_thread(cluster.namespaces, project)
    for namespace in namespaces:
        name = namespace["metadata"]["name"]
        clean = sanitize(
            {"apiVersion": "v1", "kind": "Namespace", **namespace}
        )
        _add_object(
            bundle,
            f"kubernetes/{name}/Namespace/{name}.json",
            clean,
            "kubernetes.object",
        )
        for obj in await asyncio.to_thread(cluster.objects, name):
            found.append(obj)
            clean = sanitize(obj)
            if clean is None:
                continue
            _add_object(
                bundle,
                f"kubernetes/{name}/{obj['kind']}/"
                f"{obj['metadata']['name']}.json",
                clean,
                "kubernetes.object",
            )
        bundle.add_json(
            f"kubernetes/{name}/secret-names.json",
            await asyncio.to_thread(cluster.secret_names, name),
            item_type="kubernetes.secret-names",
            source="kubernetes",
        )
    for app in await asyncio.to_thread(cluster.applications, project):
        clean = sanitize(app)
        if clean is None:
            continue
        clean["metadata"].pop("namespace", None)
        _add_object(
            bundle,
            f"gitops/applications/{app['metadata']['name']}.json",
            clean,
            "gitops.application",
        )
    return [ns["metadata"]["name"] for ns in namespaces], found


def _pod_specs(obj: dict) -> list[dict]:
    spec = obj.get("spec") or {}
    if obj.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    template = (spec.get("template") or {}).get("spec")
    return [template] if template else []


def collect_images(objects: list[dict], bundle: Bundle) -> None:
    """The images the project runs, as references: see inventory.py."""
    images: dict[str, set[str]] = {}
    for obj in objects:
        workload = (
            f"{obj['metadata'].get('namespace')}/{obj['kind']}/"
            f"{obj['metadata']['name']}"
        )
        for pod in _pod_specs(obj):
            for container in (pod.get("containers") or []) + (
                pod.get("initContainers") or []
            ):
                if container.get("image"):
                    images.setdefault(container["image"], set()).add(workload)
    bundle.add_json(
        "images.json",
        [
            {"image": image, "workloads": sorted(workloads)}
            for image, workloads in sorted(images.items())
        ],
        item_type="images.references",
        source="kubernetes",
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


def _repo_slug(url: str) -> str:
    return "/".join(url.rstrip("/").removesuffix(".git").split("/")[-2:])


async def collect_repos(
    apps: list[dict],
    archiver: RepoArchiver,
    token: str | None,
    bundle: Bundle,
) -> None:
    """
    A bundle of every repository of the platform's GitHub organisation the
    project's apps use. A repository elsewhere already belongs to the
    customer: it is listed, not copied.
    """
    org_prefix = f"https://github.com/{settings.GITHUB_ORG}/".lower()
    listing = []
    for url in sorted(
        {a["github_repo_url"] for a in apps if a.get("github_repo_url")}
    ):
        slug = _repo_slug(url)
        lower = url.lower()
        if lower.startswith("https://github.com/") and not lower.startswith(
            org_prefix
        ):
            listing.append(
                {
                    "repository": url,
                    "exported": False,
                    "reason": "customer-owned",
                }
            )
            continue
        rel = f"git/{slug.replace('/', '__')}.bundle"
        try:
            metadata = await asyncio.to_thread(
                archiver.bundle, url, bundle.path(rel), token
            )
        except Exception as exc:  # pylint: disable=broad-except
            bundle.problem(f"git:{slug}", str(exc))
            continue
        bundle.add_file(
            rel,
            item_type="git.bundle",
            source=f"git:{slug}",
            media_type=fmt.GIT_BUNDLE,
            metadata=metadata,
        )
        listing.append({"repository": url, "exported": True, "path": rel})
    bundle.add_json(
        "git/repositories.json",
        listing,
        item_type="git.repositories",
        source="cmp-db:deployments",
    )


# ---------------------------------------------------------------------------
# Databases and buckets
# ---------------------------------------------------------------------------


def database_refs(objects: list[dict]) -> list[DatabaseRef]:
    refs = []
    for obj in objects:
        if obj.get("kind") != "Cluster" or not obj.get(
            "apiVersion", ""
        ).startswith("postgresql.cnpg.io/"):
            continue
        name = obj["metadata"]["name"]
        initdb = ((obj.get("spec") or {}).get("bootstrap") or {}).get(
            "initdb"
        ) or {}
        refs.append(
            DatabaseRef(
                namespace=obj["metadata"]["namespace"],
                cluster=name,
                database=initdb.get("database", "app"),
                secret=(initdb.get("secret") or {}).get("name", f"{name}-app"),
            )
        )
    return refs


async def collect_databases(
    objects: list[dict], dumper: DatabaseDumper, bundle: Bundle
) -> None:
    for ref in database_refs(objects):
        rel = f"databases/{ref.namespace}/{ref.cluster}.sql"
        source = f"cnpg:{ref.namespace}/{ref.cluster}"
        try:
            metadata = await asyncio.to_thread(
                dumper.dump, ref, bundle.path(rel)
            )
        except Exception as exc:  # pylint: disable=broad-except
            bundle.problem(source, str(exc))
            continue
        bundle.add_file(
            rel,
            item_type="postgres.dump",
            source=source,
            media_type=fmt.SQL,
            metadata={
                "namespace": ref.namespace,
                "cluster": ref.cluster,
                **metadata,
            },
        )


async def collect_buckets(
    project: str, objects: list[dict], reader: BucketReader, bundle: Bundle
) -> None:
    for ref in await asyncio.to_thread(reader.buckets, project, objects):
        prefix = f"objects/{ref.namespace}/{ref.bucket}"
        source = f"s3:{ref.namespace}/{ref.bucket}"

        def target(key: str, prefix=prefix, source=source) -> Path | None:
            try:
                return bundle.path(f"{prefix}/{key}")
            except UnsafePathError:
                bundle.problem(source, f"object key not exportable: {key!r}")
                return None

        try:
            keys = await asyncio.to_thread(reader.download, ref, target)
        except Exception as exc:  # pylint: disable=broad-except
            bundle.problem(source, str(exc))
            continue
        for key in keys:
            bundle.add_file(
                f"{prefix}/{key}",
                item_type="object",
                source=source,
                media_type=fmt.OCTET,
                metadata={
                    "namespace": ref.namespace,
                    "bucket": ref.bucket,
                    "key": key,
                },
            )


# ---------------------------------------------------------------------------
# Secrets, identity, logs
# ---------------------------------------------------------------------------


async def collect_secrets(
    project: str,
    include_values: bool,
    reader: SecretReader,
    bundle: Bundle,
) -> None:
    secrets = await reader.read(project)
    source = f"vault:project-{project}"
    bundle.add_json(
        "secrets/names.json",
        {path: sorted(data) for path, data in sorted(secrets.items())},
        item_type="vault.secret-names",
        source=source,
    )
    if include_values:
        bundle.add_json(
            "secrets/values.json",
            secrets,
            item_type="vault.secret-values",
            source=source,
        )


async def collect_identity(
    project: str, reader: IdentityReader, bundle: Bundle
) -> None:
    bundle.add_json(
        "identity/members.json",
        await asyncio.to_thread(reader.members, project),
        item_type="keycloak.members",
        source="keycloak:platform-groups",
    )
    for realm in (project, f"{project}-staging"):
        exported = await asyncio.to_thread(reader.realm, realm)
        if exported is None:
            continue
        config, users = exported
        bundle.add_json(
            f"identity/{realm}/realm.json",
            config,
            item_type="keycloak.realm",
            source=f"keycloak:{realm}",
        )
        bundle.add_jsonl(
            f"identity/{realm}/users.jsonl",
            users,
            item_type="keycloak.users",
            source=f"keycloak:{realm}",
        )


async def collect_logs(
    project: str,
    namespaces: list[str],
    reader: LogReader,
    bundle: Bundle,
    start: datetime,
    end: datetime,
) -> None:
    rel = f"logs/{project}.jsonl"
    count = 0
    if not namespaces:
        return
    with bundle.path(rel).open("w", encoding="utf-8") as handle:
        async for page in reader.pages(project, namespaces, start, end):
            for entry in page:
                handle.write(
                    json.dumps(entry, sort_keys=True, default=fmt.jsonable)
                    + "\n"
                )
                count += 1
    bundle.add_file(
        rel,
        item_type="loki.logs",
        source=f"loki:{project}",
        media_type=fmt.JSONL,
        metadata={
            "namespaces": namespaces,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "rows": count,
        },
    )
