import copy
import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from app.services.imports import generate
from app.services.imports.generate import PlanError
from app.services.imports.plan_model import ImportPlan

PLAN_FILE = (
    Path(__file__).parent / "fixtures" / "imports" / "epitweet-plan.json"
)
SHA = "0123456789abcdef0123456789abcdef01234567"
PREFIX = "ghcr.io/3-istor/imports"
REPO = "3-Istor/epitweet"


def _plan() -> dict:
    return ImportPlan.model_validate_json(PLAN_FILE.read_text()).model_dump(
        by_alias=True, mode="json"
    )


def _digest(n: int) -> str:
    return "sha256:" + f"{n:x}" * 64


def _record(*apps: dict) -> dict:
    return {
        "apiVersion": "cnp.3istor.com/v1alpha1",
        "kind": "ProjectRecord",
        "metadata": {"name": "epitweet"},
        "spec": {"targetCloud": "onprem", "apps": list(apps)},
    }


def _services(specs):
    return [s.name for s in specs if s.kind == "service"]


def test_resolve_keeps_every_epitweet_app():
    specs = generate.resolve(_plan())

    assert sorted(s.name for s in specs) == sorted(
        [
            "mongodb",
            "redis",
            "neo4j",
            "elasticsearch",
            "user-service",
            "post-service",
            "like-service",
            "social-graph-service",
            "timeline-service",
            "frontend",
            "search-service",
        ]
    )


def test_resolve_fills_redacted_env_from_links_and_credentials():
    specs = {s.name: s for s in generate.resolve(_plan())}

    graph = specs["social-graph-service"]

    assert graph.secret_env == [
        {
            "env": "QUARKUS_NEO4J_AUTHENTICATION_USERNAME",
            "backing": "neo4j",
            "key": "username",
        },
        {
            "env": "QUARKUS_NEO4J_AUTHENTICATION_PASSWORD",
            "backing": "neo4j",
            "key": "password",
        },
        {
            "env": "QUARKUS_MONGODB_CONNECTION_STRING",
            "backing": "mongodb",
            "key": "url",
            "value": "mongodb://mongodb:27017",
        },
    ]
    assert not [
        e for e in graph.env if e["name"].startswith("QUARKUS_NEO4J_A")
    ]
    assert graph.warnings == []


def test_connection_string_follows_a_renamed_backing_app():
    specs = {
        s.name: s
        for s in generate.resolve(_plan(), {"rename": {"mongodb": "store"}})
    }

    item = specs["user-service"].secret_env[0]

    assert (item["backing"], item["value"]) == (
        "store",
        "mongodb://mongodb:27017",
    )


def test_resolve_applies_exclude_rename_public_and_memory_edits():
    edits = {
        "exclude": ["search-service", "elasticsearch"],
        "rename": {"frontend": "web"},
        "public": {"web": True, "user-service": True},
        "memory": {
            "user-service": {"requests_mem_mib": 256, "limits_mem_mib": 512}
        },
    }

    specs = {s.name: s for s in generate.resolve(_plan(), edits)}

    assert "search-service" not in specs and "elasticsearch" not in specs
    assert specs["web"].public and specs["user-service"].public
    assert specs["user-service"].requests_mem_mib == 256
    assert {d["app"] for d in specs["web"].depends_on} == {
        "like-service",
        "post-service",
        "social-graph-service",
        "timeline-service",
        "user-service",
    }


def test_rename_keeps_the_alias_the_code_was_written_with():
    specs = {
        s.name: s
        for s in generate.resolve(_plan(), {"rename": {"mongodb": "store"}})
    }

    link = next(
        d for d in specs["user-service"].depends_on if d["app"] == "store"
    )

    assert link["alias"] == "mongodb"


@pytest.mark.parametrize(
    "edits, problem",
    [
        ({"rename": {"frontend": "Web"}}, "not a valid app name"),
        ({"rename": {"frontend": "9web"}}, "not a valid app name"),
        ({"rename": {"frontend": "w" * 41}}, "not a valid app name"),
        ({"rename": {"frontend": "redis"}}, "two apps are named 'redis'"),
        ({"exclude": ["nope"]}, "'nope' is not an app of the plan"),
        ({"public": {"redis": True}}, "only an imported service is public"),
        (
            {
                "memory": {
                    "frontend": {
                        "requests_mem_mib": 512,
                        "limits_mem_mib": 256,
                    }
                }
            },
            "at most the limit",
        ),
    ],
)
def test_resolve_refuses_bad_edits(edits, problem):
    with pytest.raises(PlanError) as refused:
        generate.resolve(_plan(), edits)

    assert any(problem in p for p in refused.value.problems)


def test_resolve_refuses_a_backing_service_outside_the_catalogue():
    plan = _plan()
    plan["backing"].append(
        {
            "name": "pg",
            "kind": "postgres",
            "image": "postgres:16",
            "supported": False,
            "port": 5432,
            "resources": {"requests_mem_mib": 256, "limits_mem_mib": 512},
            "credentials_found": [],
        }
    )

    with pytest.raises(PlanError) as refused:
        generate.resolve(plan)

    assert "not in the catalogue" in refused.value.problems[0]


def test_reserved_alias_falls_back_to_the_target_name():
    plan = _plan()
    for link in plan["links"]:
        if link["from"] == "user-service" and link["to"] == "redis":
            link["alias"] = "vault"

    specs = {s.name: s for s in generate.resolve(plan)}
    link = next(
        d for d in specs["user-service"].depends_on if d["app"] == "redis"
    )

    assert link["alias"] == "redis"


def test_capacity_refuses_an_app_over_its_namespace_quota():
    specs = generate.resolve(
        _plan(),
        {
            "memory": {
                "frontend": {"requests_mem_mib": 4096, "limits_mem_mib": 4096}
            }
        },
    )

    problems = generate.check_capacity(specs, 100000, 2048, 1000)

    assert problems == [
        "app 'frontend' requests 4096 MiB, its namespace quota is 2048 MiB"
    ]


def test_capacity_counts_the_project_s_other_imports():
    specs = generate.resolve(_plan())

    problems = generate.check_capacity(specs, 4096, 2048, 1000, 2000)

    assert "the project may use 4096 MiB" in problems[0]


def test_record_gets_every_app_with_links_only_to_present_apps():
    specs = generate.resolve(_plan())
    digests = {name: _digest(i + 1) for i, name in enumerate(_services(specs))}
    del digests["social-graph-service"]
    record = _record({"name": "docs", "type": "static"})

    files = generate.apply_to_record(
        record, "epitweet", specs, digests, REPO, SHA, PREFIX
    )

    names = [a["name"] for a in record["spec"]["apps"]]
    assert "social-graph-service" not in names
    assert names[0] == "docs"
    links = {
        a["name"]: [d["app"] for d in a.get("dependsOn", [])]
        for a in record["spec"]["apps"]
    }
    assert "social-graph-service" not in links["post-service"]
    assert len(files) == 10


def test_record_entries_follow_the_registry_schema_shape():
    specs = generate.resolve(_plan())
    digests = {name: _digest(1) for name in _services(specs)}
    record = _record()

    generate.apply_to_record(
        record, "epitweet", specs, digests, REPO, SHA, PREFIX
    )

    apps = {a["name"]: a for a in record["spec"]["apps"]}
    assert apps["mongodb"] == {"name": "mongodb", "type": "backing"}
    assert apps["frontend"]["public"] is True
    assert apps["frontend"]["hostnames"] == {
        "prod": "frontend-epitweet.3istor.com"
    }
    assert apps["user-service"]["source"] == {
        "repo": "https://github.com/3-Istor/epitweet",
        "path": "tinyx/user-service",
        "sha": SHA,
    }
    assert "hostnames" not in apps["user-service"]
    assert {"app": "mongodb", "port": 27017} in apps["user-service"][
        "dependsOn"
    ]


def test_a_template_app_of_the_same_name_is_refused():
    specs = generate.resolve(_plan())
    record = _record({"name": "frontend", "type": "static"})

    with pytest.raises(PlanError) as refused:
        generate.apply_to_record(
            record,
            "epitweet",
            specs,
            {"frontend": _digest(1)},
            REPO,
            SHA,
            PREFIX,
        )

    assert "already exists" in refused.value.problems[0]


@pytest.mark.parametrize(
    "sha, digest",
    [("abc", _digest(1)), (SHA, "sha256:short"), (SHA, "md5:" + "a" * 64)],
)
def test_malformed_sha_or_digest_is_refused(sha, digest):
    specs = generate.resolve(_plan())

    with pytest.raises(PlanError):
        generate.apply_to_record(
            _record(),
            "epitweet",
            specs,
            {"frontend": digest},
            REPO,
            sha,
            PREFIX,
        )


def _values(files, app):
    return YAML(typ="safe").load(files[f"apps/epitweet/{app}/values.yaml"])


def test_service_values_pin_the_digest_and_run_as_the_image_user():
    specs = generate.resolve(_plan())
    digests = {name: _digest(2) for name in _services(specs)}

    files = generate.apply_to_record(
        _record(), "epitweet", specs, digests, REPO, SHA, PREFIX
    )
    values = _values(files, "user-service")

    assert values["image"] == {
        "repository": "ghcr.io/3-istor/imports/epitweet/user-service",
        "digest": _digest(2),
    }
    assert values["workload"]["podSecurityContext"]["runAsUser"] == 185
    assert values["workload"]["securityContext"]["capabilities"] == {
        "drop": ["ALL"]
    }
    assert values["service"]["ports"] == [
        {"name": "http", "port": 8081, "targetPort": 8081}
    ]
    assert values["secrets"]["vaultPath"] == (
        "project-epitweet/user-service/imports"
    )


def test_root_image_keeps_running_without_privilege_escalation():
    specs = generate.resolve(_plan())
    digests = {name: _digest(3) for name in _services(specs)}

    files = generate.apply_to_record(
        _record(), "epitweet", specs, digests, REPO, SHA, PREFIX
    )
    values = _values(files, "frontend")

    assert values["workload"]["securityContext"] == {
        "allowPrivilegeEscalation": False
    }
    assert "runAsNonRoot" not in values["workload"]["podSecurityContext"]


def test_neo4j_and_its_consumer_read_the_generated_secret_from_vault():
    specs = generate.resolve(_plan())
    digests = {name: _digest(4) for name in _services(specs)}

    files = generate.apply_to_record(
        _record(), "epitweet", specs, digests, REPO, SHA, PREFIX
    )
    neo4j = _values(files, "neo4j")
    graph = _values(files, "social-graph-service")

    expected = {
        "enabled": True,
        "vaultRole": "project-epitweet-system-role",
    }
    assert neo4j["secrets"] == {
        **expected,
        "vaultPath": "project-epitweet/neo4j/imports",
    }
    assert graph["secrets"] == {
        **expected,
        "vaultPath": "project-epitweet/social-graph-service/imports",
    }
    auth = next(e for e in neo4j["env"] if e["name"] == "NEO4J_AUTH")
    assert auth["valueFrom"]["secretKeyRef"]["name"] == "neo4j-secrets"
    assert "hunter2" not in json.dumps(files)


def test_backing_values_are_the_catalogue_presets():
    specs = generate.resolve(_plan())

    files = generate.apply_to_record(
        _record(), "epitweet", specs, {}, REPO, SHA, PREFIX
    )

    assert _values(files, "mongodb") == generate.load_preset("mongodb")
    assert sorted(files) == [
        "apps/epitweet/elasticsearch/values.yaml",
        "apps/epitweet/mongodb/values.yaml",
        "apps/epitweet/neo4j/values.yaml",
        "apps/epitweet/redis/values.yaml",
    ]


def test_remove_drops_apps_files_and_links_to_them():
    record = _record(
        {"name": "web", "type": "static"},
        {
            "name": "api",
            "type": "service",
            "dependsOn": [{"app": "db", "port": 1}],
        },
        {"name": "db", "type": "backing"},
    )
    keep = copy.deepcopy(record)

    files = generate.remove_from_record(record, "epitweet", {"db"})

    assert files == {"apps/epitweet/db/values.yaml": None}
    assert record["spec"]["apps"] == [
        keep["spec"]["apps"][0],
        {"name": "api", "type": "service"},
    ]


@pytest.mark.parametrize("name", ["auth", "status", "offhours", "system"])
def test_names_of_the_project_itself_are_refused(name):
    edits = {"rename": {"frontend": name}}

    with pytest.raises(generate.PlanError) as exc:
        generate.resolve(_plan(), edits)

    assert f"'{name}' is reserved" in str(exc.value)


def test_credential_values_never_reach_the_spec():
    plan = _plan()
    plan["services"][0]["env"].append(
        {"name": "MONGO_URL", "value": "mongodb://root:s3cr3tPass@mongo:27017"}
    )

    specs = generate.resolve(plan)

    spec = next(s for s in specs if s.plan_name == plan["services"][0]["name"])
    assert "MONGO_URL" not in {e["name"] for e in spec.env}
    assert any("MONGO_URL looks like a credential" in w for w in spec.warnings)


def test_port_edit_moves_the_probe_and_the_links_to_the_new_port():
    specs = {
        s.name: s
        for s in generate.resolve(_plan(), {"port": {"user-service": 9090}})
    }

    user = specs["user-service"]
    to_user = [
        d
        for d in specs["timeline-service"].depends_on
        if d["app"] == "user-service"
    ]
    assert (user.ports[0]["port"], user.probe["port"], to_user[0]["port"]) == (
        9090,
        9090,
        9090,
    )


def test_probe_edit_sets_an_http_probe_on_a_port_of_the_app():
    edits = {
        "probe": {
            "user-service": {"kind": "http", "path": "/q/health", "port": 8081}
        }
    }

    specs = {s.name: s for s in generate.resolve(_plan(), edits)}

    assert specs["user-service"].probe == {
        "kind": "http",
        "path": "/q/health",
        "port": 8081,
    }


def test_dropped_link_is_not_deployed():
    edits = {"drop_links": [{"from": "user-service", "to": "redis"}]}

    specs = {s.name: s for s in generate.resolve(_plan(), edits)}

    assert [d["app"] for d in specs["user-service"].depends_on] == ["mongodb"]


@pytest.mark.parametrize(
    "edits, problem",
    [
        ({"port": {"user-service": 70000}}, "port must be 1 to 65535"),
        ({"port": {"redis": 6380}}, "only an imported service has an"),
        (
            {"probe": {"user-service": {"kind": "http", "port": 8081}}},
            "an HTTP probe needs a path",
        ),
        (
            {
                "probe": {
                    "user-service": {
                        "kind": "http",
                        "path": "http://evil/",
                        "port": 8081,
                    }
                }
            },
            "an HTTP probe needs a path",
        ),
        (
            {"probe": {"user-service": {"kind": "tcp", "port": 22}}},
            "the probe port is not a port of it",
        ),
        (
            {"drop_links": [{"from": "redis", "to": "frontend"}]},
            "is not a link of the plan",
        ),
    ],
)
def test_resolve_refuses_bad_port_probe_and_link_edits(edits, problem):
    with pytest.raises(PlanError) as error:
        generate.resolve(_plan(), edits)

    assert any(problem in p for p in error.value.problems)


def _with_probe(probe: dict) -> dict:
    plan = _plan()
    service = next(s for s in plan["services"] if s["name"] == "frontend")
    service["probes"] = probe
    return plan


def _frontend_values(plan: dict) -> tuple[generate.AppSpec, dict]:
    specs = generate.resolve(plan)
    digests = {name: _digest(3) for name in _services(specs)}
    files = generate.apply_to_record(
        _record(), "epitweet", specs, digests, REPO, SHA, PREFIX
    )
    spec = next(s for s in specs if s.name == "frontend")
    return spec, _values(files, "frontend")


def test_a_safe_http_probe_on_the_first_port_is_kept_and_monitored():
    plan = _with_probe({"kind": "http", "path": "/api/health-1.0", "port": 80})

    spec, values = _frontend_values(plan)

    assert values["monitoring"] == {"enabled": True, "path": "/api/health-1.0"}
    assert values["workload"]["probes"]["liveness"]["httpGet"] == {
        "path": "/api/health-1.0",
        "port": 80,
    }


@pytest.mark.parametrize(
    "path",
    [
        '/x"} ,{"evil": "1',
        "/health\n",
        "/health\nfoo: bar",
        "/a b",
        "/health?full=1",
        "/health#frag",
        "health",
        "",
        None,
        "/" + "a" * 201,
    ],
)
def test_an_unsafe_http_probe_path_falls_back_to_tcp(path):
    plan = _with_probe({"kind": "http", "path": path, "port": 80})

    spec, values = _frontend_values(plan)

    assert spec.probe is None
    assert any("probe path" in w for w in spec.warnings)
    assert values["workload"]["probes"]["liveness"]["tcpSocket"] == {
        "port": 80
    }
    assert values["monitoring"] == {"enabled": True, "path": "/"}


def test_an_http_probe_on_another_port_than_the_first_falls_back_to_tcp():
    plan = _with_probe({"kind": "http", "path": "/health", "port": 9090})

    spec, values = _frontend_values(plan)

    assert any("port 9090 refused" in w for w in spec.warnings)
    assert values["workload"]["probes"]["readiness"]["tcpSocket"] == {
        "port": 80
    }
    assert values["monitoring"] == {"enabled": True, "path": "/"}


def test_a_stored_spec_with_an_unsafe_probe_path_is_checked_again():
    spec = generate.resolve(_plan())[-1]
    spec.probe = {"kind": "http", "path": '/"x', "port": spec.ports[0]["port"]}

    values = generate.service_values("epitweet", spec, PREFIX, _digest(4))

    assert "httpGet" not in values["workload"]["probes"]["liveness"]


@pytest.mark.parametrize("name", ["api\n", "api\n\n"])
def test_a_name_with_a_trailing_newline_is_refused(name):
    assert generate.check_name(name)


@pytest.mark.parametrize(
    "sha, digest", [(SHA + "\n", _digest(1)), (SHA, _digest(1) + "\n")]
)
def test_a_sha_or_digest_with_a_trailing_newline_is_refused(sha, digest):
    specs = generate.resolve(_plan())

    with pytest.raises(PlanError):
        generate.apply_to_record(
            _record(),
            "epitweet",
            specs,
            {name: digest for name in _services(specs)},
            REPO,
            sha,
            PREFIX,
        )


@pytest.mark.parametrize("path", ["/health\n", '/h"x', "/h?x=1", "/a b"])
def test_an_edited_http_probe_with_an_unsafe_path_is_refused(path):
    edits = {
        "probe": {"user-service": {"kind": "http", "path": path, "port": 8081}}
    }

    with pytest.raises(PlanError) as refused:
        generate.resolve(_plan(), edits)

    assert any("needs a path" in p for p in refused.value.problems)


def test_an_edited_http_probe_on_a_second_port_is_refused():
    plan = _plan()
    service = next(s for s in plan["services"] if s["name"] == "user-service")
    service["ports"].append({"name": "admin", "port": 9000})
    edits = {
        "probe": {
            "user-service": {"kind": "http", "path": "/health", "port": 9000}
        }
    }

    with pytest.raises(PlanError) as refused:
        generate.resolve(plan, edits)

    assert any("must use the first port" in p for p in refused.value.problems)


def test_an_edited_probe_overrides_an_unsafe_analyzer_probe():
    plan = _with_probe({"kind": "http", "path": '/"x', "port": 80})
    edits = {
        "probe": {"frontend": {"kind": "http", "path": "/ok", "port": 80}}
    }

    specs = {s.name: s for s in generate.resolve(plan, edits)}

    assert specs["frontend"].probe == {
        "kind": "http",
        "path": "/ok",
        "port": 80,
    }
