import asyncio
import base64
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.deployment import Deployment, DeploymentStatus, ProviderType
from app.models.environment import AppEnvironment
from app.models.project import Project, TargetCloud
from app.services import app_security_data as sd
from app.services import cloud_backups as cb
from app.services.kube_client import ClusterNotConnectedError

BACKEND = "deploy/values-backend.yaml"
KEY = {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"}


@pytest.mark.parametrize(
    "release,env,expected",
    [
        ("shop-api-backend", "prod", "shop-api-backend-db"),
        ("shop-api-backend-stg", "staging", "shop-api-backend-stg-db"),
        (
            "long-project-name-an-application-name-xyz-backend",
            "prod",
            "long-project-name-an-application-name-xyz-backe-db",
        ),
        (
            "long-project-name-an-application-name-xyz-backend-stg",
            "staging",
            "long-project-name-an-application-name-xyz-b-stg-db",
        ),
    ],
)
def test_db_base_name_matches_chart_truncation(release, env, expected):
    assert cb.db_base_name(release, env) == expected


def test_targets_include_staging():
    prod, staging = cb.targets_for("shop", "api", ["prod", "staging"])

    assert (prod.vault_path, staging.vault_path) == (
        "api/backup",
        "api/staging/backup",
    )
    assert prod.secret_namespace == staging.secret_namespace == "shop-backups"
    assert prod.secret_name == "shop-api-backend-db-backup"
    assert staging.secret_name == "shop-api-backend-stg-db-backup"


class World:
    def __init__(self, monkeypatch):
        cb._settled.clear()
        cb._last_error.clear()
        self.file_reads = 0
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(
            bind=engine, expire_on_commit=False
        )
        self.secrets: dict[str, dict] = {}
        self.vault: dict[tuple[str, str], dict] = {}
        self.values = "migrations:\n  backupBefore: false\n"
        self.commits: list[tuple[str, str, str]] = []
        self.archiving: str | None = "True"  # None: condition absent
        self.cluster_down = False
        self.cluster_name = "shop-api-backend-db"

        async def read_kv(mount, path):
            return self.vault.get((mount, path), {})

        async def write_kv(mount, path, data):
            self.vault[(mount, path)] = data

        def kube_get(path, params=None, cloud=TargetCloud.ONPREM):
            return self.secrets.get(path.rsplit("/", 1)[-1])

        def kube_list(path, params=None, cloud=TargetCloud.ONPREM):
            if self.cluster_down:
                raise ClusterNotConnectedError(TargetCloud.AWS)
            conditions = (
                []
                if self.archiving is None
                else [
                    {"type": "ContinuousArchiving", "status": self.archiving}
                ]
            )
            return [
                {
                    "metadata": {"name": self.cluster_name},
                    "status": {"conditions": conditions},
                }
            ]

        async def token():
            return "tok"

        async def get_file(tok, repo, path, ref="main"):
            self.file_reads += 1
            return self.values, "sha"

        async def put_file(**kw):
            self.commits.append(
                (kw["file_path"], kw["content"], kw["message"])
            )
            return {}

        monkeypatch.setattr(cb.vault_client, "read_kv", read_kv)
        monkeypatch.setattr(cb.vault_client, "write_kv", write_kv)
        monkeypatch.setattr(cb, "kube_get", kube_get)
        monkeypatch.setattr(cb, "kube_list", kube_list)
        monkeypatch.setattr(cb, "get_org_installation_token", token)
        monkeypatch.setattr(cb, "get_file_content", get_file)
        monkeypatch.setattr(cb, "put_file_content", put_file)

    def add(
        self,
        cloud=TargetCloud.AWS,
        app_type="fullstack",
        staging=False,
        name="api",
        project="shop",
    ):
        with self.session_factory() as db:
            if not db.query(Project).filter_by(project_name=project).first():
                db.add(
                    Project(
                        project_name=project,
                        owner_username="o",
                        target_cloud=cloud,
                    )
                )
            row = Deployment(
                name=name,
                template_id="k3s-gitops-app",
                provider_type=ProviderType.KUBERNETES,
                project_id=project,
                status=DeploymentStatus.RUNNING,
                github_repo_url=f"https://github.com/3-istor/{name}",
                app_config=json.dumps({"app_type": app_type}),
            )
            db.add(row)
            db.commit()
            if staging:
                db.add(
                    AppEnvironment(
                        deployment_id=row.id,
                        name="staging",
                        state=staging if isinstance(staging, str) else "ready",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                db.commit()

    def secret(self, name="shop-api-backend-db-backup"):
        self.secrets[name] = {
            "data": {
                k: base64.b64encode(v.encode()).decode()
                for k, v in KEY.items()
            }
        }

    def sync(self):
        with self.session_factory() as db:
            return asyncio.run(cb.sync_keys(db))


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_sync_writes_key_then_enables_backup_before(world):
    world.add()
    world.secret()

    written = world.sync()

    assert written == ["api/backup"]
    assert world.vault == {("project-shop", "api/backup"): KEY}
    assert not world.commits  # VSO gets one cycle before backups are on
    assert world.sync() == []
    [(path, content, message)] = world.commits
    assert path == BACKEND
    assert "backupBefore: true" in content
    assert (
        message
        == "chore(cmp): enable pre-migration backups (backup key ready)"
    )


def test_sync_copies_staging_key_without_second_commit(world):
    world.add(staging=True)
    world.secret()
    world.secret("shop-api-backend-stg-db-backup")

    written = world.sync()

    assert written == ["api/backup", "api/staging/backup"]
    world.sync()
    assert len(world.commits) == 1


def test_sync_skips_when_secret_missing(world):
    world.add()

    assert world.sync() == []
    assert not world.vault and not world.commits


def test_sync_is_idempotent(world):
    world.add()
    world.secret()
    world.vault[("project-shop", "api/backup")] = dict(KEY)
    world.values = "migrations:\n  backupBefore: true\n"

    assert world.sync() == []
    assert not world.commits


def test_sync_leaves_absent_backup_before_alone(world):
    world.add()
    world.secret()
    world.values = "db:\n  enabled: true\n"

    world.sync()

    assert not world.commits


@pytest.mark.parametrize(
    "cloud,app_type",
    [(TargetCloud.ONPREM, "fullstack"), (TargetCloud.AWS, "static")],
)
def test_sync_ignores_onprem_and_static(world, cloud, app_type):
    world.add(cloud=cloud, app_type=app_type)
    world.secret()

    assert world.sync() == []
    assert not world.vault and not world.commits


def test_backup_patch_on_cloud_app_uses_vault():
    update = sd.BackupUpdate(enabled=True)

    cloud = sd.backup_patch(update, TargetCloud.GCP)["db"]["backup"]

    assert cloud["enabled"] is True
    assert cloud["bucket"] == "vault"
    assert cloud["endpointURL"].startswith("http://onprem-apps.cnp-vpn")
    assert sd.backup_patch(update) == {"db": {"backup": {"enabled": True}}}


def test_sync_reads_the_repo_once_per_deployment(world):
    world.add()
    world.secret()
    world.vault[("project-shop", "api/backup")] = dict(KEY)
    world.values = "migrations:\n  backupBefore: true\n"

    world.sync()
    world.sync()

    assert world.file_reads == 1


def test_sync_skips_staging_that_is_not_ready(world):
    world.add(staging="provisioning")
    world.secret()
    world.secret("shop-api-backend-stg-db-backup")

    assert world.sync() == ["api/backup"]


def test_sync_continues_after_a_failing_deployment(world, monkeypatch):
    world.add(name="bad")
    world.add(name="api")
    world.secret()
    world.secret("shop-bad-backend-db-backup")
    real = world.vault

    async def write_kv(mount, path, data):
        if path.startswith("bad/"):
            raise RuntimeError("vault down")
        real[(mount, path)] = data

    monkeypatch.setattr(cb.vault_client, "write_kv", write_kv)

    assert world.sync() == ["api/backup"]


def test_sync_does_not_flip_while_archiving_is_not_true(world):
    world.add()
    world.secret()
    world.vault[("project-shop", "api/backup")] = dict(KEY)

    for state in (None, "False"):
        world.archiving = state
        world.sync()
        assert not world.commits

    world.archiving = "True"
    world.sync()
    assert len(world.commits) == 1


def test_sync_does_not_settle_while_the_cloud_is_down(world):
    world.add()
    world.secret()
    world.vault[("project-shop", "api/backup")] = dict(KEY)
    world.cluster_down = True

    world.sync()
    assert not world.commits and not world.file_reads

    world.cluster_down = False
    world.sync()
    assert len(world.commits) == 1


def test_sync_flips_for_a_restored_cluster(world):
    world.add()
    world.secret()
    world.vault[("project-shop", "api/backup")] = dict(KEY)
    world.cluster_name = "shop-api-backend-db-r1"

    world.sync()

    assert len(world.commits) == 1
