import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus
from app.models.imports import ImportRun, ImportService, ImportSource
from app.services.imports import deploy, generate, service

PLAN = json.loads(
    (
        Path(__file__).parent / "fixtures" / "imports" / "epitweet-plan.json"
    ).read_text()
)
SHA = "0123456789abcdef0123456789abcdef01234567"
RECORD = {
    "apiVersion": "cnp.3istor.com/v1alpha1",
    "kind": "ProjectRecord",
    "metadata": {"name": "epitweet"},
    "spec": {"targetCloud": "onprem", "apps": []},
}


def _digest(app: str) -> str:
    return "sha256:" + format(abs(hash(app)) % 16, "x") * 64


class Cluster:
    """Vault, the registry, Cloudflare and Argo CD as the deploy sees them."""

    def __init__(self):
        self.vault: dict[tuple[str, str], dict] = {}
        self.vault_writes: list[tuple[str, str]] = []
        self.commits: list[dict] = []
        self.cnames: list[str] = []
        self.argo: dict[str, str] = {}
        self.other_records: list[dict] = []
        self.record = json.loads(json.dumps(RECORD))
        self.record_reads: list[str] = []
        self.unhealthy: set[str] = set()

    async def read_kv(self, mount, path):
        return dict(self.vault.get((mount, path), {}))

    async def write_kv(self, mount, path, data):
        self.vault[(mount, path)] = dict(data)
        self.vault_writes.append((mount, path))

    async def commit(self, project, change, message):
        record = json.loads(json.dumps(self.record))
        files = await change(record, "head")
        self.record = record
        self.commits.append({"record": record, "files": files})
        return "c0ffee"

    async def records(self, ref=None):
        self.record_reads.append(ref)
        return json.loads(json.dumps(self.other_records))

    async def cname(self, project, app, hostname):
        self.cnames.append(hostname)

    def argo_state(self, name, image):
        return "failed" if name in self.unhealthy else "synced"


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    cluster = Cluster()
    monkeypatch.setattr(deploy, "SessionLocal", Session)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(deploy.vault_client, "read_kv", cluster.read_kv)
    monkeypatch.setattr(deploy.vault_client, "write_kv", cluster.write_kv)
    monkeypatch.setattr(deploy, "commit_record_change", cluster.commit)
    monkeypatch.setattr(deploy, "list_records", cluster.records)
    monkeypatch.setattr(deploy.dns, "ensure_cname", cluster.cname)
    monkeypatch.setattr(deploy, "_argo_state", cluster.argo_state)

    async def smoke(url):
        return True

    monkeypatch.setattr(deploy, "_smoke", smoke)
    monkeypatch.setattr(
        service.audit, "record_system_event", lambda *a, **k: None
    )
    monkeypatch.setattr(settings, "GITHUB_REGISTRY_USERNAME", "puller")
    monkeypatch.setattr(settings, "GITHUB_REGISTRY_TOKEN", "ghp_pulltoken")
    return Session, cluster


def _published_run(Session) -> str:
    specs = generate.resolve(PLAN)
    with Session() as db:
        source = db.query(ImportSource).first()
        if source is None:
            source = ImportSource(
                project_name="epitweet",
                repo_full_name="3-Istor/epitweet",
                repo_id=1,
                installation_id=2,
                branch="main",
                created_by="alice",
            )
            db.add(source)
            db.flush()
        for spec in specs if not source.services else []:
            db.add(
                ImportService(
                    source_id=source.id,
                    app_name=spec.name,
                    kind=spec.kind,
                    path=spec.path,
                    spec=spec.to_dict(),
                )
            )
        run = ImportRun(
            source_id=source.id,
            kind="build",
            sha=SHA,
            status="running",
            requested_by="alice",
            expires_at=service._now() + timedelta(hours=1),
            report={
                "images": {
                    s.name: {"digest": _digest(s.name)}
                    for s in specs
                    if s.kind == "service"
                }
            },
        )
        db.add(run)
        db.commit()
        return run.id


def _deploy(run_id):
    asyncio.run(deploy.deploy(run_id))


def test_deploy_writes_one_commit_with_the_record_and_eleven_values(world):
    Session, cluster = world
    run_id = _published_run(Session)

    _deploy(run_id)

    assert len(cluster.commits) == 1
    files = cluster.commits[0]["files"]
    assert len(files) == 11
    assert len(cluster.commits[0]["record"]["spec"]["apps"]) == 11
    with Session() as db:
        run = db.get(ImportRun, run_id)
        assert run.status == "succeeded"
        assert run.report["commit"] == "c0ffee"


def test_deploy_writes_the_project_pull_secret_once(world):
    Session, cluster = world
    _deploy(_published_run(Session))
    first = cluster.vault[("project-epitweet", "system/registry")]

    _deploy(_published_run(Session))

    assert (
        cluster.vault_writes.count(("project-epitweet", "system/registry"))
        == 1
    )
    auths = json.loads(first[".dockerconfigjson"])["auths"]
    assert set(auths) == {"ghcr.io", "https://ghcr.io"}


def test_neo4j_password_is_generated_once_and_copied_to_its_consumer(world):
    Session, cluster = world
    _deploy(_published_run(Session))
    auth = cluster.vault[("project-epitweet", "system/imports/neo4j")]

    _deploy(_published_run(Session))

    assert cluster.vault[("project-epitweet", "system/imports/neo4j")] == auth
    password = auth["NEO4J_AUTH"].split("/", 1)[1]
    assert len(password) >= 24
    consumer = cluster.vault[
        ("project-epitweet", "system/imports/social-graph-service")
    ]
    assert consumer == {
        "QUARKUS_NEO4J_AUTHENTICATION_USERNAME": "neo4j",
        "QUARKUS_NEO4J_AUTHENTICATION_PASSWORD": password,
        "QUARKUS_MONGODB_CONNECTION_STRING": "mongodb://mongodb:27017",
    }


def test_deploy_creates_one_app_row_per_imported_app(world):
    Session, cluster = world
    run_id = _published_run(Session)

    _deploy(run_id)

    with Session() as db:
        rows = db.query(Deployment).all()
        assert sorted(r.name for r in rows) == sorted(
            s.app_name for s in db.query(ImportService).all()
        )
        mongo = next(r for r in rows if r.name == "mongodb")
        assert mongo.template_id == "import-backing"
        assert mongo.template_name == "MongoDB"
        assert mongo.k8s_namespace == "epitweet-mongodb"
        assert mongo.argocd_app_name == "epitweet-mongodb-app"
        assert all(r.status == DeploymentStatus.RUNNING for r in rows)


def test_public_app_gets_its_cname(world):
    Session, cluster = world

    _deploy(_published_run(Session))

    assert cluster.cnames == ["frontend-epitweet.3istor.com"]


def test_a_failed_service_does_not_block_the_others(world):
    Session, cluster = world
    cluster.unhealthy.add("epitweet-search-service-app")
    run_id = _published_run(Session)

    _deploy(run_id)

    with Session() as db:
        statuses = {
            s.app_name: s.status for s in db.query(ImportService).all()
        }
        run = db.get(ImportRun, run_id)
    assert statuses["search-service"] == "failed"
    assert statuses["user-service"] == "running"
    assert run.status == "failed"


def test_a_registry_failure_fails_the_run(world, monkeypatch):
    Session, cluster = world

    async def refuse(project, change, message):
        raise service.RegistryError("registry down")

    monkeypatch.setattr(deploy, "commit_record_change", refuse)
    run_id = _published_run(Session)

    _deploy(run_id)

    with Session() as db:
        run = db.get(ImportRun, run_id)
    assert run.status == "failed"
    assert run.report["stage"] == "deploy"


def test_values_carry_the_published_digest(world):
    Session, cluster = world

    _deploy(_published_run(Session))

    files = cluster.commits[0]["files"]
    values = YAML(typ="safe").load(
        files["apps/epitweet/user-service/values.yaml"]
    )
    assert values["image"]["digest"] == _digest("user-service")


def test_deploy_reads_the_registry_at_the_head_it_commits_on(world):
    Session, cluster = world

    _deploy(_published_run(Session))

    assert cluster.record_reads == ["head"]


def test_name_taken_by_another_project_since_approval_aborts_the_commit(
    world,
):
    Session, cluster = world
    # Project "epitweet-user" owns namespace epitweet-user-service.
    cluster.other_records = [
        {
            "metadata": {"name": "epitweet-user"},
            "spec": {"apps": [{"name": "service", "type": "static"}]},
        }
    ]
    run_id = _published_run(Session)

    _deploy(run_id)

    assert cluster.commits == []
    with Session() as db:
        run = db.get(ImportRun, run_id)
        rows = {s.app_name: s for s in db.query(ImportService).all()}
    assert run.status == "failed"
    assert "namespace 'epitweet-user-service'" in run.report["error"]
    assert rows["user-service"].status == "failed"
    assert "registry refused" in rows["user-service"].message


# ── Removal ─────────────────────────────────────────────────────────────────


@pytest.fixture
def removal(world, monkeypatch):
    Session, cluster = world
    calls = {"namespaces": [], "dns": []}

    async def noop():
        return None

    def cleanup(project, apps, source_id):
        calls["namespaces"].append(apps)
        return noop()

    async def remove_cname(project, app, hostname):
        calls["dns"].append(hostname)

    monkeypatch.setattr(deploy, "_cleanup_namespaces", cleanup)
    monkeypatch.setattr(deploy, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(deploy.dns, "remove_cname", remove_cname)
    return Session, cluster, calls


def _never_deployed(Session) -> None:
    """Applied, then the build failed: no app of it was deployed."""
    run_id = _published_run(Session)
    with Session() as db:
        db.get(ImportRun, run_id).status = "failed"
        db.commit()


def _remove(Session):
    with Session() as db:
        source = db.query(ImportSource).one()
        asyncio.run(deploy.remove(db, "alice", source))


def _names(record) -> set[str]:
    return {a["name"] for a in record["spec"]["apps"]}


def test_removal_takes_out_what_the_import_deployed(removal):
    Session, cluster, calls = removal
    _deploy(_published_run(Session))
    with Session() as db:
        deployed = {s.app_name for s in db.query(ImportService).all()}

    _remove(Session)

    assert _names(cluster.record) == set()
    assert set(calls["namespaces"][0]) == deployed
    assert calls["dns"] == ["frontend-epitweet.3istor.com"]


def test_removal_keeps_a_template_app_named_like_a_pending_service(removal):
    Session, cluster, calls = removal
    _never_deployed(Session)
    cluster.record["spec"]["apps"] = [
        {
            "name": "frontend",
            "type": "static",
            "repoURL": "https://github.com/3-Istor/frontend",
            "hostnames": {"prod": "frontend-epitweet.3istor.com"},
        }
    ]

    _remove(Session)

    assert _names(cluster.record) == {"frontend"}
    assert calls["namespaces"] == [[]]
    assert calls["dns"] == []
    files = cluster.commits[-1]["files"]
    assert "apps/epitweet/frontend/values.yaml" not in files


def test_removal_keeps_a_service_of_another_repository(removal):
    Session, cluster, calls = removal
    _never_deployed(Session)
    cluster.record["spec"]["apps"] = [
        {
            "name": "user-service",
            "type": "service",
            "public": False,
            "source": {
                "repo": "https://github.com/someone/else",
                "path": "",
                "sha": SHA,
            },
        }
    ]

    _remove(Session)

    assert _names(cluster.record) == {"user-service"}
    assert calls["namespaces"] == [[]]


def test_removal_keeps_a_backing_app_it_never_deployed(removal):
    Session, cluster, calls = removal
    _never_deployed(Session)
    cluster.record["spec"]["apps"] = [{"name": "mongodb", "type": "backing"}]

    _remove(Session)

    assert _names(cluster.record) == {"mongodb"}
    assert calls["namespaces"] == [[]]


def _dns_fails_on_first_deploy(Session, monkeypatch) -> None:
    async def refuse(project, app, hostname):
        raise deploy.dns.DnsError("cloudflare down")

    monkeypatch.setattr(deploy.dns, "ensure_cname", refuse)
    run_id = _published_run(Session)
    _deploy(run_id)
    with Session() as db:
        assert db.get(ImportRun, run_id).status == "failed"


def test_removal_after_a_dns_failure_takes_out_the_backing_apps(
    removal, monkeypatch
):
    Session, cluster, calls = removal
    _dns_fails_on_first_deploy(Session, monkeypatch)
    backing = {s.name for s in generate.resolve(PLAN) if s.kind == "backing"}

    _remove(Session)

    assert _names(cluster.record) & backing == set()
    assert backing <= set(calls["namespaces"][0])


def test_removal_after_a_dns_failure_keeps_a_template_app_of_that_name(
    removal, monkeypatch
):
    Session, cluster, calls = removal
    _dns_fails_on_first_deploy(Session, monkeypatch)
    cluster.record["spec"]["apps"] = [
        a for a in cluster.record["spec"]["apps"] if a["name"] != "frontend"
    ] + [
        {
            "name": "frontend",
            "type": "static",
            "repoURL": "https://github.com/3-Istor/frontend",
        }
    ]

    _remove(Session)

    assert _names(cluster.record) == {"frontend"}
    assert "frontend" not in calls["namespaces"][0]
    assert calls["dns"] == []


def test_deploy_refuses_to_overwrite_a_service_of_another_repository(world):
    Session, cluster = world
    cluster.record["spec"]["apps"] = [
        {
            "name": "user-service",
            "type": "service",
            "public": False,
            "source": {
                "repo": "https://github.com/someone/else",
                "path": "",
                "sha": SHA,
            },
        }
    ]
    run_id = _published_run(Session)

    _deploy(run_id)

    assert cluster.commits == []
    with Session() as db:
        run = db.get(ImportRun, run_id)
    assert "imported from another repository" in run.report["error"]
