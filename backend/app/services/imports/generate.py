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

from app.services.imports.secret_scan import is_emittable_value
from app.services.registry_names import HOSTNAME_DOMAIN

CATALOGUE_DIR = Path(__file__).parent / "catalogue"
SUPPORTED_BACKING = ("mongodb", "redis", "neo4j", "elasticsearch")
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
    return _mib(load_preset(kind)["resources"]["requests"]["memory"])


def _mib(quantity: str) -> int:
    quantity = str(quantity)
    if quantity.endswith("Gi"):
        return int(float(quantity[:-2]) * 1024)
    if quantity.endswith("Mi"):
        return int(float(quantity[:-2]))
    raise ValueError(f"unsupported memory quantity {quantity}")


def _millicpu(quantity: str) -> int:
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


def resolve(plan: dict, edits: dict | None = None) -> list[AppSpec]:
    """
    Apply the user's edits to a plan and return the apps to deploy.

    Edits: ``exclude`` (plan names), ``rename`` ({plan name: app name}),
    ``public`` ({app: bool}, services only), ``memory`` ({app:
    {requests_mem_mib, limits_mem_mib}}).

    Raises:
        PlanError: With every problem found, not only the first.
    """
    edits = edits or {}
    problems: list[str] = []
    excluded = set(edits.get("exclude") or [])
    rename: dict[str, str] = dict(edits.get("rename") or {})
    public_edits: dict[str, bool] = dict(edits.get("public") or {})
    memory_edits: dict[str, dict] = dict(edits.get("memory") or {})

    plan_names = {s["name"] for s in plan["services"]} | {
        b["name"] for b in plan["backing"]
    }
    for name in excluded | set(rename):
        if name not in plan_names:
            problems.append(f"'{name}' is not an app of the plan")

    def final(name: str) -> str:
        return rename.get(name, name)

    specs: list[AppSpec] = []
    for backing in plan["backing"]:
        if backing["name"] in excluded:
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
                requests_mem_mib=_mib(
                    preset["resources"]["requests"]["memory"]
                ),
                limits_mem_mib=_mib(preset["resources"]["limits"]["memory"]),
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
        run_as = _run_as_user(service["build"])
        if run_as is None:
            warnings.append(
                "image runs as root: kept, with privilege escalation off"
            )
        probe = _checked_probe(service.get("probes"), ports, warnings)
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
    for name in public_edits:
        if not any(s.name == name and s.kind == "service" for s in specs):
            problems.append(f"'{name}': only an imported service is public")

    names = [s.name for s in specs]
    for spec in specs:
        problems += check_name(spec.name)
        if names.count(spec.name) > 1:
            problems.append(f"two apps are named '{spec.name}'")
    by_plan_name = {s.plan_name: s for s in specs}
    _link(plan, specs, by_plan_name, final)
    _wire_credentials(specs, by_plan_name)
    if problems:
        raise PlanError(sorted(set(problems)))
    return specs


def _link(plan: dict, specs: list[AppSpec], by_plan_name: dict, final) -> None:
    for link in plan["links"]:
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
        if _millicpu(spec.cpu) > namespace_millicpu:
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
    """KV path, in mount project-<p>, of an app's generated environment."""
    return f"system/imports/{app}"


def needs_secret(spec: AppSpec) -> bool:
    return bool(spec.secret_env) or spec.backing_kind == "neo4j"


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
    project: str, spec: AppSpec, image_prefix: str, digest: str
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
    # The project's system role reads project-<p>/system/*: imported apps get
    # no per-app Terraform and so no per-app role (D9).
    return {
        "enabled": True,
        "vaultPath": f"project-{project}/{secret_path(app)}",
        "vaultRole": f"project-{project}-system-role",
    }


def backing_values(project: str, spec: AppSpec) -> dict:
    values = load_preset(spec.backing_kind)
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


def apply_to_record(
    record: dict,
    project: str,
    specs: list[AppSpec],
    digests: dict[str, str],
    repo: str,
    sha: str,
    image_prefix: str,
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
                project, spec, image_prefix, digests[spec.name]
            )
        )
        files[values_path(project, spec.name)] = dump_yaml(values, header)
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
