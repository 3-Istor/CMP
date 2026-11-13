"""
Stand-ins for the platform behind the project export, shared by the tests
and the restore drill fixture (tests/export_drill/make_fixture.py).
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

from app.services.project_export.collectors import Sources
from app.services.project_export.sources import BucketRef, DatabaseRef


def sample_objects(project: str) -> dict[str, list[dict]]:
    """One app namespace as the cluster lists it: Argo output plus noise."""
    ns = f"{project}-shop"
    meta = {
        "namespace": ns,
        "uid": "u-1",
        "resourceVersion": "42",
        "creationTimestamp": "2026-11-01T00:00:00Z",
        "managedFields": [{"manager": "argocd"}],
        "labels": {"cnp.3istor.com/project": project},
    }
    return {
        ns: [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {**meta, "name": "shop-config"},
                "data": {"FEATURE_X": "on"},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {**meta, "name": "kube-root-ca.crt"},
                "data": {"ca.crt": "..."},
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {**meta, "name": "shop"},
                "spec": {
                    "clusterIP": "10.43.0.12",
                    "clusterIPs": ["10.43.0.12"],
                    "selector": {"app": "shop"},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
                "status": {"loadBalancer": {}},
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    **meta,
                    "name": "shop-db-rw",
                    "ownerReferences": [
                        {"kind": "Cluster", "name": "shop-db"}
                    ],
                },
                "spec": {"ports": [{"port": 5432}]},
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {**meta, "name": "shop"},
                "spec": {
                    "replicas": 0,
                    "selector": {"matchLabels": {"app": "shop"}},
                    "template": {
                        "metadata": {"labels": {"app": "shop"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "shop",
                                    "image": "ghcr.io/3-istor/shop/backend"
                                    "@sha256:" + "a" * 64,
                                }
                            ]
                        },
                    },
                },
            },
            {
                "apiVersion": "postgresql.cnpg.io/v1",
                "kind": "Cluster",
                "metadata": {**meta, "name": "shop-db"},
                "spec": {
                    "instances": 1,
                    "imageName": "ghcr.io/cloudnative-pg/postgresql:18.6@sha256:"
                    + "b" * 64,
                    "bootstrap": {
                        "initdb": {
                            "database": "shop",
                            "owner": "app",
                            "secret": {"name": "shop-secrets"},
                        }
                    },
                },
            },
            {
                "apiVersion": "objectbucket.io/v1alpha1",
                "kind": "ObjectBucketClaim",
                "metadata": {**meta, "name": "shop-media"},
                "spec": {"bucketName": "shop-media"},
            },
        ]
    }


class FakeCluster:
    def __init__(
        self,
        project: str,
        objects: dict[str, list[dict]] | None = None,
        applications: list[dict] | None = None,
    ) -> None:
        self.by_namespace = (
            objects if objects is not None else sample_objects(project)
        )
        self.apps = applications or [
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": f"{project}-shop",
                    "namespace": "argocd",
                    "uid": "a-1",
                },
                "spec": {"project": project},
                "status": {"sync": {"status": "Synced"}},
            }
        ]

    def namespaces(self, project: str) -> list[dict]:
        return [
            {
                "metadata": {
                    "name": ns,
                    "uid": "n-1",
                    "labels": {"cnp.3istor.com/project": project},
                },
                "status": {"phase": "Active"},
            }
            for ns in self.by_namespace
        ]

    def objects(self, namespace: str) -> list[dict]:
        return self.by_namespace.get(namespace, [])

    def secret_names(self, namespace: str) -> list[dict]:
        return [
            {"name": "shop-secrets", "type": "Opaque", "keys": ["password"]}
        ]

    def applications(self, project: str) -> list[dict]:
        return self.apps


class FakeRepos:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def bundle(self, url: str, dest: Path, token: str | None) -> dict:
        self.calls.append((url, token))
        dest.write_bytes(b"# v2 git bundle\n")
        return {"repository": url, "head": "0" * 40, "refs": 1}


class FakeDumper:
    def dump(self, ref: DatabaseRef, dest: Path) -> dict:
        dest.write_text("CREATE TABLE orders (id int);\n")
        return {"database": ref.database, "row_counts": {"public.orders": 0}}


class FakeBuckets:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = (
            objects if objects is not None else {"img/logo.png": b"\x89PNG"}
        )

    def buckets(self, project: str, objects: list[dict]) -> list[BucketRef]:
        return [
            BucketRef(
                namespace=f"{project}-shop",
                claim="shop-media",
                bucket="shop-media",
                endpoint="http://rgw",
                access_key="k",
                secret_key="s",
            )
        ]

    def download(self, ref: BucketRef, target) -> list[str]:
        written = []
        for key, data in self.objects.items():
            path = target(key)
            if path is None:
                continue
            path.write_bytes(data)
            written.append(key)
        return written


class FakeSecrets:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data if data is not None else {"shop": ["API_KEY"]}

    async def read(self, project: str) -> dict:
        return self.data


class FakeIdentity:
    def realm(self, name: str) -> tuple[dict, list[dict]] | None:
        if name.endswith("-staging"):
            return None
        return {"realm": name, "clients": []}, [{"username": "alice"}]

    def members(self, project: str) -> list[dict]:
        return [{"username": "alice", "role": "admin"}]


class FailingIdentity(FakeIdentity):
    def members(self, project: str) -> list[dict]:
        raise RuntimeError("Keycloak unreachable")


class FakeLogs:
    async def pages(
        self,
        project: str,
        namespaces: list[str],
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[list[dict]]:
        yield [
            {
                "time": end - timedelta(minutes=1),
                "labels": {"namespace": f"{project}-shop"},
                "line": "GET / 200",
            }
        ]


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, path: Path) -> None:
        self.objects[key] = path.read_bytes()

    def fetch(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])

    def link(self, key: str, seconds: int) -> str:
        return f"https://s3.test/{key}?X-Amz-Expires={seconds}"

    def upload_link(self, key: str, seconds: int) -> str:
        return f"put:{key}"

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def fake_sources(project: str, **overrides) -> Sources:
    defaults = {
        "cluster": FakeCluster(project),
        "repos": FakeRepos(),
        "databases": FakeDumper(),
        "buckets": FakeBuckets(),
        "secrets": FakeSecrets(),
        "identity": FakeIdentity(),
        "logs": FakeLogs(),
    }
    return Sources(**{**defaults, **overrides})


async def no_token(_repositories: list[str]) -> None:
    return None


class FakeKube:
    """
    The API server and the dump Job: creating the Job "runs" it, writing
    what the real script would upload through the two URLs in the Secret.
    """

    def __init__(self, store: FakeStore, fail_with: str | None = None) -> None:
        self.store = store
        self.fail_with = fail_with
        self.created: list[dict] = []
        self.deleted: list[tuple[str, dict]] = []
        self.status: dict[str, dict] = {}

    def create(self, path: str, body: dict) -> dict:
        self.created.append(body)
        if body["kind"] == "Secret":
            self.urls = body["stringData"]
        if body["kind"] == "Job":
            name = body["metadata"]["name"]
            if self.fail_with:
                self.status[name] = {"failed": 1}
            else:
                put = {
                    var: url.removeprefix("put:")
                    for var, url in self.urls.items()
                }
                self.store.objects[put["DUMP_URL"]] = (
                    b"CREATE TABLE t (id int);\n"
                )
                self.store.objects[put["COUNTS_URL"]] = b"public.t\t3\n"
                self.status[name] = {"succeeded": 1}
        return body

    def get(self, path: str, params: dict | None = None) -> dict | None:
        if path.endswith("/pods"):
            message = self.fail_with or ""
            return {
                "items": [
                    {
                        "status": {
                            "containerStatuses": [
                                {"state": {"terminated": {"message": message}}}
                            ]
                        }
                    }
                ]
            }
        return {"status": self.status.get(path.rsplit("/", 1)[-1], {})}

    def delete_collection(self, path: str, params: dict) -> None:
        self.deleted.append((path, params))
