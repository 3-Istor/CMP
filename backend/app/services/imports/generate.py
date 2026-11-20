"""
From an approved ImportPlan to the cnp-projects commit (D1, D2, D16).

Pure functions: the plan and the user's edits go in, the registry entries and
the values files of apps/<project>/<app>/values.yaml come out. Values follow
the presets of infra-templates tests/backing and tests/imported-service.yaml;
what the cnp-apps ApplicationSet adds (names, links, ingress, pull secret) is
never written here.
"""

import copy
import re
from dataclasses import dataclass, field, fields
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from app.models.project import TargetCloud
from app.services.app_security_data import BackupUpdate, backup_patch
from app.services.imports.secret_scan import is_emittable_value
from app.services.registry_names import HOSTNAME_DOMAIN

CATALOGUE_DIR = Path(__file__).parent / "catalogue"
SUPPORTED_BACKING = ("mongodb", "redis", "neo4j", "elasticsearch")
# Backing kinds the platform runs as a managed service of the app linking to
# them instead of as a container (the plan's BackingPlan.managed).
MANAGED_BACKING = ("postgres",)
# What cnp-generic-app sets when db.enabled; an imported variable of the same
# name would shadow it.
CHART_DB_ENV = frozenset(
    {"DB_TYPE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"}
)
# Prefixes of the variables a Postgres client reads without naming the link.
POSTGRES_ENV_PREFIXES = ("POSTGRES", "PG", "DATABASE_", "SPRING_DATASOURCE_")
# Kubernetes expands $(VAR) from the variables listed before it: the chart's
# DB_* come first, so the password never leaves the synced secret.
POSTGRES_URL = (
    "postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)"
)
JDBC_URL = "jdbc:postgresql://$(DB_HOST):$(DB_PORT)/$(DB_NAME)"
IMPORTED_NAME = re.compile(r"[a-z]([-a-z0-9]*[a-z0-9])?")
MAX_IMPORTED_NAME = 40
# <name>-<project> is the project's own auth, status and offhours hostname.
RESERVED_APP_NAMES = frozenset({"auth", "status", "offhours", "system"})
# Same list as cnp-projects validate-registry.py and infra-templates links.yaml.
RESERVED_ALIASES = frozenset(
    {"kubernetes", "default", "vault", "keycloak", "argocd"}
)
SHA = re.compile(r"[0-9a-f]{40}")
# How a service reaches a catalogue app without credentials (v1: only neo4j
# has generated credentials, the others stay reachable inside the project).
URL_SCHEMES = {
    "mongodb": "mongodb://{host}:{port}",
    "redis": "redis://{host}:{port}",
    "neo4j": "bolt://{host}:{port}",
    "elasticsearch": "http://{host}:{port}",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
PORT_NAME = re.compile(r"[a-z]([-a-z0-9]{0,13}[a-z0-9])?")
# Pasted into probe specs and into the monitoring annotation URLs of the
# chart: no quote, space, query or fragment can reach them.
PROBE_PATH = re.compile(r"/[A-Za-z0-9._~/-]{0,200}")
USER_LINE = re.compile(r"^\s*USER\s+(\d+)(?::\d+)?\s*$", re.MULTILINE)

# The schedule of a template app (app-templates values-backend.yaml.tpl).
OFFHOURS_SLEEP_HOUR = 1
OFFHOURS_WAKE_HOUR = 7
OFFHOURS_TIMEZONE = "Europe/Paris"
# Minutes between two deploy waves: an app wakes after, and sleeps before,
# the apps it links to.
OFFHOURS_STEP_MINUTES = 5
OFFHOURS_LAST_WAVE = 6

VALUES_HEADER = (
    "# Written by the CMP from the import of {repo} at {sha}.\n"
    "# Change it from the CMP: the next build rewrites this file.\n"
)

# Words YAML 1.1 reads as booleans. Helm parses values as YAML 1.1, ruamel
# writes YAML 1.2, which leaves them bare: redis' "--appendonly yes" would
# reach the chart as a boolean.
YAML11_BOOLEANS = frozenset(
    {"y", "yes", "n", "no", "on", "off", "true", "false"}
)


def _represent_str(representer, value: str):
    style = '"' if value.lower() in YAML11_BOOLEANS else None
    return representer.represent_scalar(
        "tag:yaml.org,2002:str", value, style=style
    )


_yaml = YAML()
_yaml.default_flow_style = False
_yaml.width = 4096
_yaml.representer.add_representer(str, _represent_str)


class PlanError(Exception):
    """The plan, with the user's edits, cannot be deployed."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class AppSpec:
    """One app of an approved import, as stored in import_services.spec."""

    name: str
    kind: str
    path: str = ""
    plan_name: str = ""
    backing_kind: str = ""
    ports: list[dict] = field(default_factory=list)
    probe: dict | None = None
    env: list[dict] = field(default_factory=list)
    public: bool = False
    requests_mem_mib: int = 0
    limits_mem_mib: int = 0
    cpu: str = "100m"
    language: str = ""
    run_as_user: int | None = None
    depends_on: list[dict] = field(default_factory=list)
    # Env names filled from a generated backing credential, as
    # {"env": NAME, "backing": app, "key": "password"}.
    secret_env: list[dict] = field(default_factory=list)
    # The CNPG database of a service, as {"backing": plan name, "pooler":
    # bool}, for a managed backing app it links to.
    database: dict | None = None
    # Sleeps at night with the project's other apps (offhours-guard).
    offhours: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSpec":
        names = {f.name for f in fields(cls)}
        known = {k: v for k, v in data.items() if k in names}
        return cls(**copy.deepcopy(known))


def load_preset(kind: str) -> dict:
    with (CATALOGUE_DIR / f"{kind}.yaml").open(encoding="utf-8") as handle:
        return dict(YAML(typ="safe").load(handle))


def preset_requests_mib(kind: str) -> int:
    return mib(load_preset(kind)["resources"]["requests"]["memory"])


def mib(quantity: str) -> int:
    quantity = str(quantity)
    if quantity.endswith("Gi"):
        return int(float(quantity[:-2]) * 1024)
    if quantity.endswith("Mi"):
        return int(float(quantity[:-2]))
    raise ValueError(f"unsupported memory quantity {quantity}")


def millicpu(quantity: str) -> int:
    quantity = str(quantity)
    if quantity.endswith("m"):
        return int(quantity[:-1])
    return int(float(quantity) * 1000)


def _run_as_user(build: dict) -> int | None:
    """The numeric USER of a Dockerfile the analyzer generated, if any."""
    generated = build.get("generated_dockerfile") or ""
    users = USER_LINE.findall(generated)
    if users and int(users[-1]) > 0:
        return int(users[-1])
    return None


def scrub_env(env: list[dict]) -> tuple[list[dict], list[str]]:
    """
    The env with every value that may be a credential (URL userinfo, token
    pattern, secret-looking name or random-looking value) dropped and the
    variable marked secret, and one warning per value dropped.
    """
    kept: list[dict] = []
    warnings: list[str] = []
    for item in env:
        value = item.get("value")
        if value is not None and not is_emittable_value(
            str(item.get("name", "")), str(value)
        ):
            item = {**item, "value": None, "secret": True}
            warnings.append(
                f"value of {item.get('name')} looks like a credential: "
                "dropped, set it as a secret of the app"
            )
        kept.append(item)
    return kept, warnings


def check_name(name: str) -> list[str]:
    if len(name) > MAX_IMPORTED_NAME or not IMPORTED_NAME.fullmatch(name):
        return [
            f"'{name}' is not a valid app name: lowercase letters, digits "
            f"and '-', starting with a letter, at most {MAX_IMPORTED_NAME} "
            "characters"
        ]
    if name in RESERVED_APP_NAMES:
        return [f"'{name}' is reserved by the platform for the project itself"]
    return []


def _checked_probe(
    probe: dict | None, ports: list[dict], warnings: list[str]
) -> dict | None:
    """
    The probe if it is a TCP probe or a safe HTTP probe on the first port,
    else None (a TCP probe on the first port) with a warning. The monitoring
    of the chart checks the probe path on the first port only.
    """
    if not probe or probe.get("kind") != "http" or not ports:
        return probe
    path = probe.get("path")
    if not isinstance(path, str) or not PROBE_PATH.fullmatch(path):
        warnings.append(
            f"HTTP probe path {str(path)[:80]!r} refused: only '/' followed by "
            "letters, digits and . _ ~ / - is kept, probing TCP instead"
        )
        return None
    if probe.get("port") != ports[0]["port"]:
        warnings.append(
            f"HTTP probe on port {probe.get('port')} refused: only the first "
            f"port ({ports[0]['port']}) is probed over HTTP, probing TCP "
            "instead"
        )
        return None
    return probe


def is_managed(backing: dict) -> bool:
    return bool(
        backing.get("supported")
        and backing.get("managed") in MANAGED_BACKING
        and backing.get("kind") == backing.get("managed")
    )


def resolve(plan: dict, edits: dict | None = None) -> list[AppSpec]:
    """
    Apply the user's edits to a plan and return the apps to deploy.

    Edits: ``exclude`` (plan names), ``rename`` ({plan name: app name}),
    ``public`` ({app: bool}, services only), ``memory`` ({app:
    {requests_mem_mib, limits_mem_mib}}), ``port`` ({app: port}, the
    service's first port), ``probe`` ({app: {kind, path, port}}),
    ``drop_links`` ([{from, to}], plan names), ``pooler`` ({app: bool},
    a service with a managed database) and ``offhours`` ({app: bool}, false
    keeps the app awake at night).

    Raises:
        PlanError: With every problem found, not only the first.
    """
    edits = edits or {}
    problems: list[str] = []
    excluded = set(edits.get("exclude") or [])
    rename: dict[str, str] = dict(edits.get("rename") or {})
    public_edits: dict[str, bool] = dict(edits.get("public") or {})
    memory_edits: dict[str, dict] = dict(edits.get("memory") or {})
    port_edits: dict[str, int] = dict(edits.get("port") or {})
    probe_edits: dict[str, dict] = dict(edits.get("probe") or {})
    pooler_edits: dict[str, bool] = dict(edits.get("pooler") or {})
    offhours_edits: dict[str, bool] = dict(edits.get("offhours") or {})
    dropped = {
        (link.get("from"), link.get("to"))
        for link in edits.get("drop_links") or []
    }

    plan_names = {s["name"] for s in plan["services"]} | {
        b["name"] for b in plan["backing"]
    }
    for name in excluded | set(rename):
        if name not in plan_names:
            problems.append(f"'{name}' is not an app of the plan")
    planned_links = {(link["from"], link["to"]) for link in plan["links"]}
    for source, target in dropped - planned_links:
        problems.append(f"'{source}' to '{target}' is not a link of the plan")

    def final(name: str) -> str:
        return rename.get(name, name)

    specs: list[AppSpec] = []
    # plan name -> (old port, new port) of a service whose port was edited.
    port_moves: dict[str, tuple[int, int]] = {}
    managed: set[str] = set()
    for backing in plan["backing"]:
        if backing["name"] in excluded:
            continue
        if is_managed(backing):
            managed.add(backing["name"])
            continue
        if backing["kind"] not in SUPPORTED_BACKING or not backing.get(
            "supported"
        ):
            problems.append(
                f"backing service '{backing['name']}' ({backing['kind']}) "
                "is not in the catalogue: exclude it to import the rest"
            )
            continue
        preset = load_preset(backing["kind"])
        specs.append(
            AppSpec(
                name=final(backing["name"]),
                plan_name=backing["name"],
                kind="backing",
                backing_kind=backing["kind"],
                ports=[
                    {"name": p["name"], "port": p["port"]}
                    for p in preset["service"]["ports"]
                ],
                requests_mem_mib=mib(
                    preset["resources"]["requests"]["memory"]
                ),
                limits_mem_mib=mib(preset["resources"]["limits"]["memory"]),
                cpu=str(preset["resources"]["requests"]["cpu"]),
            )
        )

    for service in plan["services"]:
        if service["name"] in excluded:
            continue
        name = final(service["name"])
        memory = memory_edits.get(name) or {}
        requests = int(
            memory.get(
                "requests_mem_mib", service["resources"]["requests_mem_mib"]
            )
        )
        limits = int(
            memory.get(
                "limits_mem_mib", service["resources"]["limits_mem_mib"]
            )
        )
        if not 16 <= requests <= limits:
            problems.append(
                f"app '{name}': memory requests must be at least 16 MiB and "
                "at most the limit"
            )
        warnings: list[str] = []
        ports = []
        for port in service["ports"]:
            port_name = port["name"].lower()
            if not PORT_NAME.fullmatch(port_name):
                port_name = f"p{port['port']}"
            ports.append({"name": port_name, "port": port["port"]})
        if not ports:
            problems.append(
                f"app '{name}' exposes no port: the platform cannot route "
                "to it"
            )
        env, env_warnings = scrub_env(
            [dict(e) for e in service.get("env") or []]
        )
        warnings += env_warnings
        probe = service.get("probes")
        if name in port_edits and ports:
            moved = _move_port(ports, int(port_edits[name]))
            if moved is None:
                problems.append(f"app '{name}': port must be 1 to 65535")
            else:
                port_moves[service["name"]] = moved
                if probe and probe.get("port") == moved[0]:
                    probe = {**probe, "port": moved[1]}
        if name in probe_edits:
            probe, probe_problems = _edited_probe(
                name, probe_edits[name], ports
            )
            problems += probe_problems
        run_as = _run_as_user(service["build"])
        if run_as is None:
            warnings.append(
                "image runs as root: kept, with privilege escalation off"
            )
        probe = _checked_probe(probe, ports, warnings)
        specs.append(
            AppSpec(
                name=name,
                plan_name=service["name"],
                kind="service",
                path=service["path"],
                ports=ports,
                probe=probe,
                env=env,
                public=bool(public_edits.get(name, service.get("public"))),
                requests_mem_mib=requests,
                limits_mem_mib=limits,
                cpu=service["resources"].get("cpu") or "100m",
                language=service["stack"]["language"],
                run_as_user=run_as,
                warnings=warnings,
            )
        )
    links = [
        link
        for link in plan["links"]
        if (link["from"], link["to"]) not in dropped
    ]
    problems += _attach_databases(specs, links, managed, pooler_edits)
    for edited, what in (
        (public_edits, "is public"),
        (port_edits, "has an editable port"),
        (probe_edits, "has an editable probe"),
        (pooler_edits, "with a managed database has a pooler"),
    ):
        for name in edited:
            if not any(
                s.name == name
                and s.kind == "service"
                and (edited is not pooler_edits or s.database)
                for s in specs
            ):
                problems.append(f"'{name}': only an imported service {what}")

    for name, enabled in offhours_edits.items():
        spec = next((s for s in specs if s.name == name), None)
        if spec is None:
            problems.append(f"'{name}': not an app of the import")
        else:
            spec.offhours = bool(enabled)
    names = [s.name for s in specs]
    for spec in specs:
        problems += check_name(spec.name)
        if names.count(spec.name) > 1:
            problems.append(f"two apps are named '{spec.name}'")
    by_plan_name = {s.plan_name: s for s in specs}
    _link(
        [link for link in links if link["to"] not in managed],
        specs,
        by_plan_name,
        final,
        port_moves,
    )
    _wire_credentials(specs, by_plan_name)
    if problems:
        raise PlanError(sorted(set(problems)))
    return specs


def _attach_databases(
    specs: list[AppSpec],
    links: list[dict],
    managed: set[str],
    pooler_edits: dict[str, bool],
) -> list[str]:
    """
    Give each managed backing app to the one service linking to it. The
    database lives in that service's namespace: two services cannot share it.
    """
    problems = []
    services = {s.plan_name: s for s in specs if s.kind == "service"}
    for backing in sorted(managed):
        owners = sorted(
            {
                link["from"]
                for link in links
                if link["to"] == backing and link["from"] in services
            }
        )
        if len(owners) > 1:
            problems.append(
                f"managed database '{backing}' is used by {', '.join(owners)}:"
                " a managed database belongs to one app, keep the link of one"
            )
            continue
        if not owners:
            continue
        spec = services[owners[0]]
        if spec.database:
            problems.append(
                f"app '{spec.name}' links to two managed databases, it can "
                "have one"
            )
            continue
        spec.database = {
            "backing": backing,
            "pooler": bool(pooler_edits.get(spec.name, False)),
        }
    return problems


def _move_port(ports: list[dict], new: int) -> tuple[int, int] | None:
    if not 1 <= new <= 65535:
        return None
    old = ports[0]["port"]
    ports[0] = {"name": ports[0]["name"], "port": new}
    return old, new


def _edited_probe(
    name: str, edit: dict, ports: list[dict]
) -> tuple[dict | None, list[str]]:
    """The probe a user set; only a path and a port, never a host (D16)."""
    kind = edit.get("kind")
    port = edit.get("port")
    path = edit.get("path") or None
    problems = []
    if kind not in ("tcp", "http"):
        problems.append(f"app '{name}': probe kind must be tcp or http")
    if port not in {p["port"] for p in ports}:
        problems.append(f"app '{name}': the probe port is not a port of it")
    if kind == "http" and (path is None or not PROBE_PATH.fullmatch(path)):
        problems.append(
            f"app '{name}': an HTTP probe needs a path such as /health"
        )
    if kind == "http" and ports and port != ports[0]["port"]:
        problems.append(
            f"app '{name}': an HTTP probe must use the first port "
            f"({ports[0]['port']}), the one the monitoring checks"
        )
    if problems:
        return None, problems
    return {
        "kind": kind,
        "path": path if kind == "http" else None,
        "port": port,
    }, []


def _link(
    links: list[dict],
    specs: list[AppSpec],
    by_plan_name: dict,
    final,
    port_moves: dict[str, tuple[int, int]],
) -> None:
    for link in links:
        source = by_plan_name.get(link["from"])
        target = by_plan_name.get(link["to"])
        if source is None or source.kind != "service":
            continue
        if target is None:
            source.warnings.append(
                f"link to '{link['to']}' dropped: that app is not imported"
            )
            continue
        port = link.get("port") or target.ports[0]["port"]
        moved = port_moves.get(link["to"])
        if moved and port == moved[0]:
            port = moved[1]
        # The code resolves the alias it was written with; the link points it
        # at the app under its final name.
        alias = str(link.get("alias") or link["to"])
        if (
            alias in RESERVED_ALIASES
            or alias == source.name
            or not IMPORTED_NAME.fullmatch(alias)
            or len(alias) > 63
        ):
            source.warnings.append(
                f"link alias '{alias}' refused: reserved or not a DNS label"
            )
            alias = target.name
        if any(d["alias"] == alias for d in source.depends_on):
            continue
        source.depends_on.append(
            {"app": final(link["to"]), "port": int(port), "alias": alias}
        )


def _wire_credentials(specs: list[AppSpec], by_plan_name: dict) -> None:
    """
    Fill the secret env the analyzer redacted from what the platform knows.

    A connection string of a linked backing app is rebuilt from its alias
    and port; the neo4j user and password come from the generated
    credential. Values the repository hardcoded are never used. Everything
    else stays unset, with a warning.
    """
    for spec in specs:
        if spec.kind != "service":
            continue
        if spec.database:
            _wire_database(spec)
        links = {d["app"]: d for d in spec.depends_on}
        backing = {
            s.name: s.backing_kind
            for s in by_plan_name.values()
            if s.kind == "backing" and s.name in links
        }
        neo4j = [app for app, kind in backing.items() if kind == "neo4j"]
        kept = []
        for env in spec.env:
            if not env.get("secret"):
                kept.append(env)
                continue
            name = env["name"].upper()
            target = by_plan_name.get(env.get("from_link") or "")
            if (
                target is not None
                and target.name in backing
                and backing[target.name] in URL_SCHEMES
            ):
                link = links[target.name]
                spec.secret_env.append(
                    {
                        "env": env["name"],
                        "backing": target.name,
                        "key": "url",
                        "value": URL_SCHEMES[backing[target.name]].format(
                            host=link["alias"], port=link["port"]
                        ),
                    }
                )
            elif neo4j and "PASSWORD" in name:
                spec.secret_env.append(
                    {
                        "env": env["name"],
                        "backing": neo4j[0],
                        "key": "password",
                    }
                )
            elif neo4j and "USER" in name:
                spec.secret_env.append(
                    {
                        "env": env["name"],
                        "backing": neo4j[0],
                        "key": "username",
                    }
                )
            else:
                spec.warnings.append(
                    f"secret {env['name']} is not set: add it from the CMP"
                )
        spec.env = kept


def _postgres_value(name: str, value: str | None) -> str | None:
    """What a Postgres variable becomes on the managed database, by its name."""
    upper = name.upper()
    if value and value.startswith("jdbc:"):
        return JDBC_URL
    if (value and "://" in value) or any(
        word in upper for word in ("URL", "URI", "DSN", "CONNECTION")
    ):
        return POSTGRES_URL
    for words, reference in (
        (("PASS",), "$(DB_PASSWORD)"),
        (("USER",), "$(DB_USER)"),
        (("HOST", "SERVER"), "$(DB_HOST)"),
        (("PORT",), "$(DB_PORT)"),
        (("DB", "DATABASE", "NAME"), "$(DB_NAME)"),
    ):
        if any(word in upper for word in words):
            return reference
    return None


def _wire_database(spec: AppSpec) -> None:
    """
    Point the service's Postgres variables at its managed database. The
    chart injects DB_HOST, DB_USER, DB_PASSWORD and the rest; the variables
    the code reads are rebuilt from them, so it runs unchanged.
    """
    backing = spec.database["backing"]
    kept = []
    for env in spec.env:
        name = env["name"]
        if name.upper() in CHART_DB_ENV:
            continue
        linked = env.get("from_link") == backing or name.upper().startswith(
            POSTGRES_ENV_PREFIXES
        )
        if not linked:
            kept.append(env)
            continue
        value = _postgres_value(name, env.get("value"))
        if value is None:
            spec.warnings.append(
                f"{name} looks like a setting of the database '{backing}': "
                "dropped, the app reads DB_HOST, DB_PORT, DB_NAME, DB_USER "
                "and DB_PASSWORD"
            )
            continue
        kept.append({**env, "value": value, "secret": False})
    spec.env = kept


def check_capacity(
    specs: list[AppSpec],
    max_project_mib: int,
    namespace_mib: int,
    namespace_millicpu: int,
    already_mib: int = 0,
) -> list[str]:
    """Each app fits its namespace quota; all together fit the project cap."""
    problems = []
    total = already_mib
    for spec in specs:
        total += spec.requests_mem_mib
        if spec.requests_mem_mib > namespace_mib:
            problems.append(
                f"app '{spec.name}' requests {spec.requests_mem_mib} MiB, "
                f"its namespace quota is {namespace_mib} MiB"
            )
        if millicpu(spec.cpu) > namespace_millicpu:
            problems.append(
                f"app '{spec.name}' requests {spec.cpu} CPU, its namespace "
                f"quota is {namespace_millicpu}m"
            )
    if total > max_project_mib:
        problems.append(
            f"the imported apps request {total} MiB of memory in all, the "
            f"project may use {max_project_mib} MiB"
        )
    return problems


def hostname(project: str, app: str) -> str:
    return f"{app}-{project}.{HOSTNAME_DOMAIN}"


def secret_path(app: str) -> str:
    """
    KV path, in mount project-<p>, of an app's generated environment. Under
    the app's own path: cnp-generic-app refuses a vaultPath outside
    project-<p>/<app> (infra-templates#26).
    """
    return f"{app}/imports"


def needs_secret(spec: AppSpec) -> bool:
    return (
        bool(spec.secret_env)
        or spec.backing_kind == "neo4j"
        or bool(spec.database)
    )


def database_values(
    spec: AppSpec, cloud: TargetCloud = TargetCloud.ONPREM
) -> dict:
    """
    db of cnp-generic-app, as a template app's backend gets it: backed up
    to the RGW of the project's cloud, its exporter scraped. The initdb
    secret is the app's own (``secret_path``), username app.
    """
    return {
        "enabled": True,
        "name": spec.name,
        "metrics": True,
        "backup": backup_patch(BackupUpdate(enabled=True), cloud)["db"][
            "backup"
        ],
        "pooler": {"enabled": bool(spec.database.get("pooler"))},
    }


def _http_probe_path(spec: AppSpec) -> str | None:
    """Specs stored before the probe checks of resolve() are checked again."""
    if _checked_probe(spec.probe, spec.ports, []) is None:
        return None
    probe = spec.probe or {}
    return probe.get("path") if probe.get("kind") == "http" else None


def _probes(spec: AppSpec) -> dict:
    first_port = spec.ports[0]["port"]
    path = _http_probe_path(spec)
    if path:
        handler = {"httpGet": {"path": path, "port": first_port}}
    elif spec.probe and spec.probe.get("kind") == "tcp":
        handler = {"tcpSocket": {"port": spec.probe["port"]}}
    else:
        handler = {"tcpSocket": {"port": first_port}}
    probes = {
        "liveness": {**copy.deepcopy(handler), "periodSeconds": 10},
        "readiness": {**copy.deepcopy(handler), "periodSeconds": 5},
    }
    # The startup probe holds the liveness probe back while a JVM warms up.
    probes["startup"] = {
        **copy.deepcopy(handler),
        "periodSeconds": 5,
        "failureThreshold": 36 if spec.language in ("java", "kotlin") else 12,
    }
    return probes


def service_values(
    project: str,
    spec: AppSpec,
    image_prefix: str,
    digest: str,
    cloud: TargetCloud = TargetCloud.ONPREM,
) -> dict:
    jvm = spec.language in ("java", "kotlin", "scala")
    values: dict = {
        "image": {
            "repository": f"{image_prefix}/{project}/{spec.name}",
            "digest": digest,
        },
        "service": {
            "ports": [
                {"name": p["name"], "port": p["port"], "targetPort": p["port"]}
                for p in spec.ports
            ]
        },
        "resources": {
            "requests": {
                "cpu": spec.cpu,
                "memory": f"{spec.requests_mem_mib}Mi",
            },
            "limits": {
                "cpu": "1" if jvm else "500m",
                "memory": f"{spec.limits_mem_mib}Mi",
            },
        },
    }
    probe_path = _http_probe_path(spec)
    if probe_path:
        values["monitoring"] = {"enabled": True, "path": probe_path}
    elif spec.public:
        values["monitoring"] = {"enabled": True, "path": "/"}
    else:
        values["monitoring"] = {"enabled": False}
    if spec.env:
        values["env"] = [
            {
                "name": e["name"],
                "value": "" if e["value"] is None else e["value"],
            }
            for e in spec.env
        ]
    if needs_secret(spec):
        values["secrets"] = _secrets(project, spec.name)
    if spec.database:
        values["db"] = database_values(spec, cloud)
    workload: dict = {"probes": _probes(spec)}
    if spec.run_as_user is not None:
        workload["podSecurityContext"] = {
            "runAsNonRoot": True,
            "runAsUser": spec.run_as_user,
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        workload["securityContext"] = {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }
        workload["tmpDirs"] = ["/tmp"]
    else:
        workload["podSecurityContext"] = {
            "seccompProfile": {"type": "RuntimeDefault"}
        }
        workload["securityContext"] = {"allowPrivilegeEscalation": False}
    values["workload"] = workload
    return values


def _secrets(project: str, app: str) -> dict:
    # No vaultRole: cnp-generic-app derives the app's own role,
    # <project>-<app>-role, which deploy creates (app_role_name).
    return {
        "enabled": True,
        "vaultPath": f"project-{project}/{secret_path(app)}",
    }


def app_role_name(project: str, app: str) -> str:
    return f"{project}-{app}-role"


def app_policy_name(project: str, app: str) -> str:
    return f"project-{project}-app-{app}-policy"


def app_policy(project: str, app: str) -> str:
    """
    Read on the app's own secrets only: the role of one app no longer reads
    the other apps of its project. People keep the project-wide policies.
    """
    return "".join(
        f'path "project-{project}/{kind}/{app}/*" {{\n'
        '  capabilities = ["read"]\n'
        "}\n"
        for kind in ("data", "metadata")
    )


def callers(record: dict, app: str) -> list[str]:
    """The apps of the record linking to *app*, the only ones it admits."""
    return sorted(
        {
            entry["name"]
            for entry in (record.get("spec") or {}).get("apps") or []
            if isinstance(entry, dict)
            and "name" in entry
            and any(
                isinstance(link, dict) and link.get("app") == app
                for link in entry.get("dependsOn") or []
            )
        }
    )


def with_allow_from(values_text: str, allowed: list[str]) -> str:
    """A values file of the CMP with its network.allowFrom replaced."""
    header = "".join(
        line
        for line in values_text.splitlines(keepends=True)
        if line.startswith("#")
    )
    values = YAML(typ="safe").load(values_text) or {}
    values["network"] = {"allowFrom": list(allowed)}
    return dump_yaml(values, header)


def offhours_values(spec: AppSpec, wave: int) -> dict:
    """
    The prod schedule of a template app, staggered by deploy wave: backing
    apps wake first and sleep last, so an app never wakes before what it
    calls.
    """
    if not spec.offhours:
        return {"enabled": False}
    wave = min(wave, OFFHOURS_LAST_WAVE)
    sleep = (OFFHOURS_LAST_WAVE - wave) * OFFHOURS_STEP_MINUTES
    wake = wave * OFFHOURS_STEP_MINUTES
    return {
        "enabled": True,
        "sleepAt": f"{sleep} {OFFHOURS_SLEEP_HOUR} * * *",
        "wakeAt": f"{wake} {OFFHOURS_WAKE_HOUR} * * *",
        "timezone": OFFHOURS_TIMEZONE,
    }


def backing_values(project: str, spec: AppSpec) -> dict:
    values = load_preset(spec.backing_kind)
    # The generic alerts of backing containers select on it (CNP-302).
    values["podLabels"] = {"cnp.3istor.com/role": "backing"}
    if spec.backing_kind == "neo4j":
        for env in values.get("env") or []:
            ref = (env.get("valueFrom") or {}).get("secretKeyRef")
            if ref and env["name"] == "NEO4J_AUTH":
                ref["name"] = f"{spec.name}-secrets"
        values["secrets"] = _secrets(project, spec.name)
    return values


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}"


def _from_repo(entry: dict, repo: str) -> bool:
    source = entry.get("source")
    return (
        isinstance(source, dict)
        and str(source.get("repo", "")).lower() == repo_url(repo).lower()
    )


def owned_by_import(
    record: dict, repo: str, services: set[str], deployed_backing: set[str]
) -> set[str]:
    """
    Names of the record's apps this import may remove: its services, built
    from its repository, and the backing apps it deployed. A template app or
    an app of another import under the same name is never one of them.
    """
    owned = set()
    for app in (record.get("spec") or {}).get("apps") or []:
        if not isinstance(app, dict):
            continue
        name = app.get("name")
        if app.get("type") == "service" and name in services:
            if _from_repo(app, repo):
                owned.add(name)
        elif app.get("type") == "backing" and name in deployed_backing:
            owned.add(name)
    return owned


def registry_entry(spec: AppSpec, project: str, repo: str, sha: str) -> dict:
    entry: dict = {"name": spec.name, "type": spec.kind}
    if spec.kind == "service":
        entry["public"] = spec.public
        if spec.public:
            entry["hostnames"] = {"prod": hostname(project, spec.name)}
        entry["source"] = {
            "repo": repo_url(repo),
            "path": spec.path,
            "sha": sha,
        }
    return entry


def _depends_on(spec: AppSpec, present: set[str]) -> list[dict]:
    links = []
    for link in spec.depends_on:
        if link["app"] not in present:
            continue
        item = {"app": link["app"], "port": link["port"]}
        if link["alias"] != link["app"]:
            item["alias"] = link["alias"]
        links.append(item)
    return links


def dump_yaml(data: dict, header: str = "") -> str:
    buffer = StringIO()
    _yaml.dump(data, buffer)
    return header + buffer.getvalue()


def values_path(project: str, app: str) -> str:
    return f"apps/{project}/{app}/values.yaml"


def apply_to_record(  # pylint: disable=too-many-arguments
    record: dict,
    project: str,
    specs: list[AppSpec],
    digests: dict[str, str],
    repo: str,
    sha: str,
    image_prefix: str,
    cloud: TargetCloud = TargetCloud.ONPREM,
    wake_order: dict[str, int] | None = None,
) -> dict[str, str]:
    """
    Add or refresh the imported apps in *record* (mutated) and return the
    values files to write with it, path -> content.

    A service enters the record only with a digest; a link to an app that is
    not in the record is left out, the validator refuses dangling links.

    Raises:
        PlanError: On a digest or commit that is not well formed, or an app
            name already used by a template app of the project.
    """
    problems = []
    if not SHA.fullmatch(sha):
        problems.append(f"source commit '{sha}' is not a full SHA")
    for app, digest in digests.items():
        if not DIGEST.fullmatch(digest):
            problems.append(f"digest of '{app}' is not sha256:<64 hex>")
    apps: list = record.setdefault("spec", {}).setdefault("apps", [])
    existing = {
        a.get("name"): a for a in apps if isinstance(a, dict) and "name" in a
    }
    deployable = [s for s in specs if s.kind == "backing" or s.name in digests]
    for spec in deployable:
        current = existing.get(spec.name)
        if current is None:
            continue
        if current.get("type") != spec.kind:
            problems.append(
                f"app '{spec.name}' already exists in project '{project}' "
                f"as a {current.get('type')} app"
            )
        elif spec.kind == "service" and not _from_repo(current, repo):
            problems.append(
                f"app '{spec.name}' already exists in project '{project}', "
                "imported from another repository"
            )
    if problems:
        raise PlanError(problems)

    present = {
        name
        for name, app in existing.items()
        if app.get("type") in ("service", "backing")
    } | {s.name for s in deployable}
    header = VALUES_HEADER.format(repo=repo, sha=sha)
    files: dict[str, str] = {}
    entries = []
    for spec in deployable:
        entry = registry_entry(spec, project, repo, sha)
        links = _depends_on(spec, present)
        if links:
            entry["dependsOn"] = links
        if spec.name in existing:
            index = apps.index(existing[spec.name])
            apps[index] = entry
        else:
            apps.append(entry)
        values = (
            backing_values(project, spec)
            if spec.kind == "backing"
            else service_values(
                project, spec, image_prefix, digests[spec.name], cloud
            )
        )
        values["offhours"] = offhours_values(
            spec, (wake_order or {}).get(spec.name, 0)
        )
        entries.append((spec.name, values))
    # Once every entry is in: an app admits the callers of the whole record.
    for name, values in entries:
        values["network"] = {"allowFrom": callers(record, name)}
        files[values_path(project, name)] = dump_yaml(values, header)
    return files


def remove_from_record(record: dict, project: str, names: set[str]) -> dict:
    """Drop the apps and their values files; links to them go with them."""
    spec = record.setdefault("spec", {})
    apps = [
        a
        for a in spec.get("apps") or []
        if not (isinstance(a, dict) and a.get("name") in names)
    ]
    for app in apps:
        if isinstance(app, dict) and app.get("dependsOn"):
            app["dependsOn"] = [
                d for d in app["dependsOn"] if d.get("app") not in names
            ]
            if not app["dependsOn"]:
                del app["dependsOn"]
    spec["apps"] = apps
    return {values_path(project, name): None for name in names}
