import asyncio
import hashlib
import json
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.core.config import settings
from app.core.database import Base
from app.core.roles import ProjectRole
from app.models.audit import AuditEvent
from app.models.deployment import Deployment, DeploymentStatus
from app.models.notification import Notification
from app.models.project import Project
from app.models.project_export import ProjectExport
from app.routers import project_export as export_router
from app.services import (
    activity_sources,
    audit,
    keycloak_service,
    vault_client,
)
from app.services.authz import assert_project_role
from app.services.project_export import collectors, runner
from app.services.project_export.bundle import MANIFEST, Bundle
from app.services.project_export.inventory import (
    EXCLUDED_TABLES,
    EXPORTED_TABLES,
)
from app.services.project_export.sources import (
    DatabaseRef,
    GitRepoArchiver,
    JobDumper,
    LokiLogReader,
    VaultSecretReader,
    dump_job,
)
from tests.project_export_fakes import (
    FailingIdentity,
    FakeKube,
    FakeStore,
    fake_sources,
    no_token,
)

OWNER = {"sub": "o1", "preferred_username": "o1"}
ADMIN = {"sub": "u1", "preferred_username": "u1"}
AGENT = {**OWNER, "cnp_agent": {"grant_id": "g"}}


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(Project(project_name="alpha", owner_username="o1"))
        for project in ("alpha", "beta"):
            session.add(
                Deployment(
                    name=f"{project}-web",
                    template_id="k3s-gitops-app",
                    project_id=project,
                    status=DeploymentStatus.RUNNING,
                    github_repo_url=f"https://github.com/3-Istor/{project}-web",
                    terraform_outputs='{"db_password": "x"}',
                )
            )
            session.add(
                AuditEvent(
                    actor="o1",
                    action="app.create",
                    project=project,
                    outcome="success",
                    status_code=201,
                )
            )
        session.add(
            Notification(
                user_key="o1", project="alpha", kind="x", title_key="t"
            )
        )
        session.commit()
    return make


@pytest.fixture
def roles():
    with mock.patch.object(
        keycloak_service,
        "get_project_role",
        side_effect=lambda user, project: {("u1", "alpha"): "admin"}.get(
            (user, project)
        ),
    ):
        yield


@pytest.fixture
def store_configured():
    with (
        mock.patch.object(settings, "EXPORT_S3_ACCESS_KEY_ID", "k"),
        mock.patch.object(settings, "EXPORT_S3_SECRET_ACCESS_KEY", "s"),
        mock.patch.object(settings, "STEP_UP_ENABLED", False),
    ):
        yield


def _request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []}
    )


def _new_export(factory, status="pending") -> str:
    with factory() as session:
        row = ProjectExport(
            id="e1",
            project="alpha",
            requested_by="o1",
            status=status,
            created_at=datetime(2026, 11, 13),
            problems="[]",
        )
        session.add(row)
        session.commit()
    return "e1"


def _run(factory, store=None, **overrides) -> ProjectExport:
    store = store or FakeStore()
    asyncio.run(
        runner.run_export(
            "e1",
            sources=fake_sources("alpha", **overrides),
            store=store,
            session_factory=factory,
            github_token=no_token,
        )
    )
    with factory() as session:
        return session.get(ProjectExport, "e1")


def _archive(store: FakeStore, tmp_path: Path) -> Path:
    ((key, data),) = store.objects.items()
    path = tmp_path / "export.tar.gz"
    path.write_bytes(data)
    return path


def _manifest(archive: Path) -> dict:
    with tarfile.open(archive) as tar:
        return json.load(tar.extractfile(MANIFEST))


def _member(archive: Path, name: str) -> bytes:
    with tarfile.open(archive) as tar:
        return tar.extractfile(name).read()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_every_cmp_table_is_classified_as_exported_or_excluded():
    # Arrange
    collectors.load_models()

    # Act
    unclassified = (
        set(Base.metadata.tables) - set(EXPORTED_TABLES) - set(EXCLUDED_TABLES)
    )

    # Assert
    assert unclassified == set()


def test_no_cmp_table_is_both_exported_and_excluded():
    assert set(EXPORTED_TABLES) & set(EXCLUDED_TABLES) == set()


# ---------------------------------------------------------------------------
# Archive content
# ---------------------------------------------------------------------------


def test_cmp_tables_hold_only_the_rows_of_the_exported_project(
    factory, tmp_path
):
    # Arrange
    bundle = Bundle(tmp_path)

    # Act
    with factory() as session:
        collectors.collect_cmp(session, "alpha", bundle)

    # Assert
    rows = [
        json.loads(line)
        for line in (tmp_path / "cmp/audit_events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [row["project"] for row in rows] == ["alpha"]


def test_deployments_are_exported_without_the_platform_terraform_state(
    factory, tmp_path
):
    # Arrange
    bundle = Bundle(tmp_path)

    # Act
    with factory() as session:
        collectors.collect_cmp(session, "alpha", bundle)

    # Assert
    row = json.loads((tmp_path / "cmp/deployments.jsonl").read_text())
    assert "terraform_outputs" not in row and row["name"] == "alpha-web"


def test_manifest_lists_every_archive_file_with_its_checksum(
    factory, tmp_path
):
    # Arrange
    _new_export(factory)
    store = FakeStore()

    # Act
    _run(factory, store)

    # Assert
    archive = _archive(store, tmp_path)
    manifest = _manifest(archive)
    with tarfile.open(archive) as tar:
        names = set(tar.getnames()) - {MANIFEST}
    checksums = {
        item["path"]: hashlib.sha256(
            _member(archive, item["path"])
        ).hexdigest()
        for item in manifest["items"]
    }
    assert names == set(checksums) and all(
        checksums[item["path"]] == item["sha256"] for item in manifest["items"]
    )


def test_archive_checksum_recorded_on_the_export_matches_the_stored_file(
    factory, tmp_path
):
    # Arrange
    _new_export(factory)
    store = FakeStore()

    # Act
    row = _run(factory, store)

    # Assert
    data = next(iter(store.objects.values()))
    assert row.sha256 == hashlib.sha256(data).hexdigest()


def test_cluster_assigned_fields_and_controller_output_are_not_exported(
    factory, tmp_path
):
    # Arrange
    _new_export(factory)
    store = FakeStore()

    # Act
    _run(factory, store)

    # Assert
    archive = _archive(store, tmp_path)
    paths = {item["path"] for item in _manifest(archive)["items"]}
    service = _member(
        archive, "kubernetes/alpha-shop/Service/shop.json"
    ).decode()
    assert (
        "kubernetes/alpha-shop/Service/shop-db-rw.json" not in paths
        and "kubernetes/alpha-shop/ConfigMap/kube-root-ca.crt.json"
        not in paths
        and "clusterIP" not in service
        and "resourceVersion" not in service
        and "status" not in service
    )


def test_vault_secrets_are_read_as_key_names_never_values():
    # Arrange
    names = mock.AsyncMock(return_value=["API_KEY"])
    values = mock.AsyncMock(return_value={"API_KEY": "hunter2"})

    # Act
    with (
        mock.patch.object(
            vault_client, "list_paths", mock.AsyncMock(return_value=["shop"])
        ),
        mock.patch.object(vault_client, "read_key_names", names),
        mock.patch.object(vault_client, "read_kv", values),
    ):
        result = asyncio.run(VaultSecretReader().read("alpha"))

    # Assert
    assert result == {"shop": ["API_KEY"]} and values.await_count == 0


def test_the_manifest_says_where_the_secret_values_stay(factory, tmp_path):
    # Arrange
    _new_export(factory)
    store = FakeStore()

    # Act
    _run(factory, store)

    # Assert
    manifest = _manifest(_archive(store, tmp_path))
    assert any(
        "Vault" in entry["data"] and "members" in entry["reason"]
        for entry in manifest["not_exported"]
    )


def _ref() -> DatabaseRef:
    return DatabaseRef(
        namespace="alpha-shop",
        cluster="shop-db",
        database="shop",
        secret="shop-secrets",
        image="ghcr.io/cloudnative-pg/postgresql:18@sha256:" + "b" * 64,
    )


def test_the_dump_job_takes_the_database_password_from_its_own_namespace():
    # Act
    job = dump_job(_ref(), "cnp-export-x", "cnp-export-x", {"l": "v"})

    # Assert
    pod = job["spec"]["template"]["spec"]
    env = {e["name"]: e for e in pod["containers"][0]["env"]}
    assert (
        env["PGPASSWORD"]["valueFrom"]["secretKeyRef"]["name"]
        == "shop-secrets"
        and "value" not in env["PGPASSWORD"]
        and env["PGSSLMODE"]["value"] == "verify-full"
        and pod["automountServiceAccountToken"] is False
        and pod["containers"][0]["image"] == _ref().image
        and pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"]
    )


def test_a_database_is_dumped_by_a_job_and_everything_it_left_is_removed(
    tmp_path,
):
    # Arrange
    store = FakeStore()
    kube = FakeKube(store)
    dumper = JobDumper(kube, store, "e1abcdef", poll_seconds=0)

    # Act
    metadata = dumper.dump(_ref(), tmp_path / "dump.sql")

    # Assert
    assert (
        (tmp_path / "dump.sql").read_bytes().startswith(b"CREATE TABLE")
        and metadata["row_counts"] == {"public.t": 3}
        and store.objects == {}
        and {path.rsplit("/", 1)[-1] for path, _ in kube.deleted}
        == {"jobs", "secrets"}
    )


def test_a_failed_dump_job_reports_why(tmp_path):
    # Arrange
    store = FakeStore()
    dumper = JobDumper(
        FakeKube(store, fail_with="FATAL: password authentication failed"),
        store,
        "e1abcdef",
        poll_seconds=0,
    )

    # Act
    with pytest.raises(RuntimeError) as exc:
        dumper.dump(_ref(), tmp_path / "dump.sql")

    # Assert
    assert "password authentication failed" in str(exc.value)


def test_an_object_key_escaping_the_archive_is_reported_not_written(
    factory, tmp_path
):
    # Arrange
    from tests.project_export_fakes import FakeBuckets

    _new_export(factory)
    store = FakeStore()

    # Act
    row = _run(
        factory,
        store,
        buckets=FakeBuckets({"../../etc/passwd": b"x", "ok": b"y"}),
    )

    # Assert
    paths = {
        item["path"] for item in _manifest(_archive(store, tmp_path))["items"]
    }
    assert "objects/alpha-shop/shop-media/ok" in paths and any(
        "etc/passwd" in p["error"] for p in json.loads(row.problems)
    )


def test_a_repository_outside_the_platform_organisation_is_listed_not_copied(
    tmp_path,
):
    # Arrange
    from tests.project_export_fakes import FakeRepos

    repos = FakeRepos()
    apps = [{"github_repo_url": "https://github.com/acme/shop"}]

    # Act
    asyncio.run(
        collectors.collect_repos(apps, repos, no_token, Bundle(tmp_path))
    )

    # Assert
    listing = json.loads((tmp_path / "git/repositories.json").read_text())
    assert repos.calls == [] and listing[0]["reason"] == "customer-owned"


def test_repositories_are_read_with_a_read_only_token_limited_to_them():
    # Arrange
    calls = []

    async def scoped(installation, repositories, permissions):
        calls.append((repositories, permissions))
        return "t"

    # Act
    with (
        mock.patch.object(settings, "GITHUB_INSTALLATION_ID", "42"),
        mock.patch.object(runner, "get_scoped_installation_token", scoped),
    ):
        asyncio.run(runner._github_token(["alpha-web"]))

    # Assert
    assert calls == [(["alpha-web"], {"contents": "read", "metadata": "read"})]


def test_git_bundle_carries_every_branch_of_the_repository(tmp_path):
    # Arrange
    origin = tmp_path / "origin"
    for args in (
        ["init", "-q", "-b", "main", str(origin)],
        ["-C", str(origin), "commit", "-q", "--allow-empty", "-m", "one"],
        ["-C", str(origin), "branch", "staging"],
    ):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
        )

    # Act
    metadata = GitRepoArchiver().bundle(
        str(origin), tmp_path / "r.bundle", None
    )

    # Assert
    heads = subprocess.run(
        ["git", "bundle", "list-heads", str(tmp_path / "r.bundle")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "refs/heads/staging" in heads and metadata["refs"] == 2


def test_log_pages_do_not_repeat_lines_at_page_boundaries():
    # Arrange
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    lines = [
        (t0 + timedelta(seconds=i // 2), {"a": "b"}, f"l{i}") for i in range(7)
    ]

    async def loki_lines(query, start, end, limit, direction, tenant):
        return [entry for entry in lines if start <= entry[0] < end][:limit]

    async def read() -> list[str]:
        reader = LokiLogReader()
        out = []
        async for page in reader.pages(
            "alpha", ["alpha-shop"], t0, t0 + timedelta(hours=1)
        ):
            out.extend(entry["line"] for entry in page)
        return out

    # Act
    with (
        mock.patch("app.services.project_export.sources.LOKI_PAGE", 3),
        mock.patch.object(activity_sources, "loki_lines", loki_lines),
    ):
        result = asyncio.run(read())

    # Assert
    assert result == [f"l{i}" for i in range(7)]


def test_logs_are_read_from_the_project_namespaces_never_a_name_prefix():
    # Arrange
    queries = []

    async def loki_lines(query, start, end, limit, direction, tenant):
        queries.append(query)
        return []

    async def read() -> None:
        async for _ in LokiLogReader().pages(
            "shop",
            ["shop-web", "shop-system"],
            datetime(2026, 11, 1),
            datetime(2026, 11, 2),
        ):
            pass

    # Act
    with mock.patch.object(activity_sources, "loki_lines", loki_lines):
        asyncio.run(read())

    # Assert
    assert queries == ['{namespace=~"shop-web|shop-system"}']


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_a_finished_export_is_kept_for_the_30_day_retrieval_period(factory):
    # Arrange
    _new_export(factory)

    # Act
    row = _run(factory)

    # Assert
    assert (
        row.status == "ready"
        and row.expires_at - row.finished_at == timedelta(days=30)
    )


def test_a_failing_source_makes_the_export_partial_and_says_why(factory):
    # Arrange
    _new_export(factory)

    # Act
    row = _run(factory, identity=FailingIdentity())

    # Assert
    assert row.status == "partial" and json.loads(row.problems) == [
        {"source": "keycloak", "error": "Keycloak unreachable"}
    ]


def test_an_export_whose_upload_fails_is_failed_without_a_download(factory):
    # Arrange
    _new_export(factory)
    store = FakeStore()
    store.put = mock.Mock(side_effect=RuntimeError("bucket gone"))

    # Act
    row = _run(factory, store)

    # Assert
    assert row.status == "failed" and row.object_key is None


def test_archives_past_their_retrieval_period_are_deleted(factory):
    # Arrange
    _new_export(factory)
    store = FakeStore()
    row = _run(factory, store)

    # Act
    purged = runner.purge_expired(
        store, factory, now=row.expires_at + timedelta(seconds=1)
    )

    # Assert
    with factory() as session:
        status = session.get(ProjectExport, "e1").status
    assert purged == 1 and store.objects == {} and status == "expired"


def test_an_export_interrupted_by_a_restart_is_marked_failed(factory):
    # Arrange
    _new_export(factory, status="running")

    # Act
    runner.fail_interrupted(factory)

    # Assert
    with factory() as session:
        assert session.get(ProjectExport, "e1").status == "failed"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _request_export(factory, token):
    with factory() as session:
        ctx = assert_project_role(session, token, "alpha", ProjectRole.OWNER)
        return asyncio.run(
            export_router.request_export(
                ctx,
                _request(),
                BackgroundTasks(),
                session,
            )
        )


def test_the_owner_starts_an_export(factory, roles, store_configured):
    # Act
    result = _request_export(factory, OWNER)

    # Assert
    assert result.status == "pending" and result.requested_by == "o1"


def test_a_project_admin_who_is_not_the_owner_cannot_export(
    factory, roles, store_configured
):
    # Act
    with pytest.raises(HTTPException) as exc:
        _request_export(factory, ADMIN)

    # Assert
    assert exc.value.status_code == 403


def test_the_owner_needs_a_fresh_passkey_to_export(
    factory, roles, store_configured
):
    # Act
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        pytest.raises(HTTPException) as exc,
    ):
        _request_export(factory, OWNER)

    # Assert
    assert exc.value.detail["code"] == "step_up_required"


def test_an_assistant_cannot_export_a_project(
    factory, roles, store_configured
):
    # Act
    with (
        mock.patch.object(settings, "STEP_UP_ENABLED", True),
        pytest.raises(HTTPException) as exc,
    ):
        _request_export(factory, AGENT)

    # Assert
    assert exc.value.status_code == 403


def test_a_second_export_while_one_runs_is_refused(
    factory, roles, store_configured
):
    # Arrange
    _request_export(factory, OWNER)

    # Act
    with pytest.raises(HTTPException) as exc:
        _request_export(factory, OWNER)

    # Assert
    assert exc.value.status_code == 409


def _link(factory, export_id="e1"):
    with factory() as session:
        ctx = assert_project_role(session, OWNER, "alpha", ProjectRole.OWNER)
        with mock.patch.object(
            export_router.S3ExportStore,
            "from_settings",
            return_value=FakeStore(),
        ):
            return export_router.export_link(
                export_id, ctx, _request(), session
            )


@pytest.mark.parametrize(
    "days_left, expected_seconds",
    [(29, 7 * 24 * 3600), (2, 2 * 24 * 3600)],
)
def test_a_download_link_never_outlives_the_retrieval_period(
    factory, roles, store_configured, days_left, expected_seconds
):
    # Arrange
    _new_export(factory)
    _run(factory)
    with factory() as session:
        row = session.get(ProjectExport, "e1")
        row.expires_at = runner.utcnow() + timedelta(
            days=days_left, seconds=30
        )
        session.commit()

    # Act
    link = _link(factory)

    # Assert
    assert expected_seconds <= link.expires_in <= expected_seconds + 30


def test_an_expired_export_has_no_download_link(
    factory, roles, store_configured
):
    # Arrange
    _new_export(factory, status="expired")

    # Act
    with pytest.raises(HTTPException) as exc:
        _link(factory)

    # Assert
    assert exc.value.status_code == 409


def test_export_and_download_link_requests_are_journaled():
    # Act
    actions = [
        audit.match_action("POST", "/api/projects/alpha/export")[0],
        audit.match_action("POST", "/api/projects/alpha/exports/e1/link")[0],
    ]

    # Assert
    assert actions == ["project.export", "project.export.link"]
