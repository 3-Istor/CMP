"""
Restore drill, step 1: export a project from a fake platform.

The CMP database (SQLite), the cluster, Vault, Keycloak and Loki are fakes;
the database dump (pg_dump), the bucket copy (S3), the git bundle and the
export bucket (Garage) are the real adapters against throwaway containers.
Prints {"url", "sha256", "size"} of the stored archive as JSON.

    python -m tests.export_drill.make_fixture --work DIR

Environment: SOURCE_PG_HOST/PORT/USER/PASSWORD, S3_ENDPOINT,
S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import boto3
import psycopg2
from botocore.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.audit import AuditEvent
from app.models.deployment import Deployment, DeploymentStatus
from app.models.project import Project
from app.models.project_export import ProjectExport
from app.models.security import SecurityFinding
from app.services.project_export import runner
from app.services.project_export.collectors import Sources, load_models
from app.services.project_export.sources import (
    BucketRef,
    GitRepoArchiver,
    PgDumper,
    S3BucketReader,
    S3ExportStore,
)
from tests.project_export_fakes import (
    FakeCluster,
    FakeIdentity,
    FakeLogs,
    FakeSecrets,
    no_token,
)

PROJECT = "drill"
DATABASE = "shop"
SOURCE_BUCKET = "shop-media"
EXPORT_BUCKET = "cnp-project-exports"
OBJECTS = {
    "img/logo.png": bytes(range(256)) * 64,
    "docs/terms.txt": "Conditions générales\n".encode(),
    "empty": b"",
}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set")
    return value


def _pg(dbname: str) -> dict:
    return {
        "host": _env("SOURCE_PG_HOST"),
        "port": int(_env("SOURCE_PG_PORT")),
        "user": _env("SOURCE_PG_USER"),
        "password": _env("SOURCE_PG_PASSWORD"),
        "dbname": dbname,
    }


def seed_database() -> None:
    admin = psycopg2.connect(**_pg("postgres"))
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {DATABASE}")
        cursor.execute(f"CREATE DATABASE {DATABASE}")
    admin.close()
    with psycopg2.connect(**_pg(DATABASE)) as conn, conn.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE customers (id serial PRIMARY KEY, name text, "
            "created_at timestamptz DEFAULT now());"
            "CREATE TABLE orders (id serial PRIMARY KEY, customer_id int "
            "REFERENCES customers(id), total numeric(10,2), note jsonb);"
            "INSERT INTO customers (name) SELECT 'client ' || g "
            "FROM generate_series(1, 250) g;"
            "INSERT INTO orders (customer_id, total, note) SELECT "
            "1 + g % 250, g * 1.5, jsonb_build_object('n', g) "
            "FROM generate_series(1, 1000) g;"
        )
    conn.close()


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=_env("S3_ENDPOINT"),
        aws_access_key_id=_env("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY"),
        region_name="garage",
        config=Config(
            signature_version="s3v4", s3={"addressing_style": "path"}
        ),
    )


def seed_bucket() -> None:
    client = _s3()
    for key, data in OBJECTS.items():
        client.put_object(Bucket=SOURCE_BUCKET, Key=key, Body=data)


def seed_repository(work: Path) -> str:
    origin = work / "shop.git-src"
    shutil.rmtree(origin, ignore_errors=True)
    git = ["git", "-c", "user.name=drill", "-c", "user.email=drill@cnp"]
    subprocess.run([*git, "init", "-q", "-b", "main", str(origin)], check=True)
    (origin / "deploy").mkdir()
    (origin / "deploy/values-backend.yaml").write_text(
        "image:\n  repository: ghcr.io/3-istor/shop/backend\n"
    )
    for args in (
        ["add", "."],
        ["commit", "-q", "-m", "first deploy"],
        ["branch", "staging"],
    ):
        subprocess.run([*git, "-C", str(origin), *args], check=True)
    return str(origin)


def seed_cmp(repo: str):
    load_models()
    engine = create_engine(f"sqlite:///{Path.cwd() / 'drill-cmp.db'}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Project(project_name=PROJECT, owner_username="owner"))
        session.add(
            Deployment(
                name="shop",
                template_id="k3s-gitops-app",
                project_id=PROJECT,
                status=DeploymentStatus.RUNNING,
                github_repo_url=repo,
                k8s_namespace=f"{PROJECT}-shop",
            )
        )
        session.add(
            AuditEvent(
                actor="owner",
                action="app.create",
                project=PROJECT,
                outcome="success",
                status_code=201,
            )
        )
        session.add(
            SecurityFinding(
                project=PROJECT,
                app="shop",
                fingerprint="f" * 32,
                source="trivy",
                category="vulnerability",
                tier="high",
                audience="dev",
                rule="CVE-2026-0001",
                title="Example",
                first_seen=datetime(2026, 11, 1),
                last_seen=datetime(2026, 11, 13),
            )
        )
        session.add(
            ProjectExport(
                id="drill",
                project=PROJECT,
                requested_by="owner",
                include_secret_values=False,
                status="pending",
                created_at=runner.utcnow(),
                problems="[]",
            )
        )
        session.commit()
    return factory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    work = parser.parse_args().work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)

    seed_database()
    seed_bucket()
    factory = seed_cmp(seed_repository(work))

    bucket = BucketRef(
        namespace=f"{PROJECT}-shop",
        claim=SOURCE_BUCKET,
        bucket=SOURCE_BUCKET,
        endpoint=_env("S3_ENDPOINT"),
        access_key=_env("S3_ACCESS_KEY_ID"),
        secret_key=_env("S3_SECRET_ACCESS_KEY"),
    )
    sources = Sources(
        cluster=FakeCluster(PROJECT),
        repos=GitRepoArchiver(),
        databases=PgDumper(lambda ref: _pg(ref.database)),
        buckets=S3BucketReader(lambda _project, _objects: [bucket]),
        secrets=FakeSecrets(),
        identity=FakeIdentity(),
        logs=FakeLogs(),
    )
    store = S3ExportStore(
        _env("S3_ENDPOINT"),
        EXPORT_BUCKET,
        _env("S3_ACCESS_KEY_ID"),
        _env("S3_SECRET_ACCESS_KEY"),
        "garage",
    )
    asyncio.run(
        runner.run_export(
            "drill",
            sources=sources,
            store=store,
            session_factory=factory,
            github_token=no_token,
        )
    )
    with factory() as session:
        row = session.get(ProjectExport, "drill")
        if row.status != "ready":
            sys.exit(f"export {row.status}: {row.problems}")
        print(
            json.dumps(
                {
                    "url": store.link(row.object_key, 3600),
                    "sha256": row.sha256,
                    "size": row.size,
                }
            )
        )


if __name__ == "__main__":
    main()
