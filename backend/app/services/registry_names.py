"""
Registry name derivation and collision checks.

Mirrors ``scripts/validate-registry.py`` in 3-Istor/cnp-projects. The CMP writes
records with ``[skip ci]``, so that validator never sees them: the same rules
are applied here before a record is written. Keep both in sync.
"""

import re

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
MAX_LABEL_LENGTH = 63
FULLSTACK_COMPONENTS = ("frontend", "backend")
HOSTNAME_DOMAIN = "3istor.com"

DerivedName = tuple[str, str, str]


def project_names(project: str) -> list[DerivedName]:
    return [
        ("namespace", f"{project}-system", "the project system namespace"),
        ("appproject", project, "the project AppProject"),
        ("appproject", f"{project}-platform", "the platform AppProject"),
        ("application", f"{project}-appproject", "the appproject Application"),
        ("application", f"{project}-bootstrap", "the bootstrap Application"),
        (
            "hostname",
            f"status-{project}.{HOSTNAME_DOMAIN}",
            "the status hostname",
        ),
        (
            "hostname",
            f"offhours-{project}.{HOSTNAME_DOMAIN}",
            "the offhours hostname",
        ),
        (
            "hostname",
            f"auth-{project}.{HOSTNAME_DOMAIN}",
            "the auth hostname",
        ),
    ]


def app_names(project: str, app: dict) -> list[DerivedName]:
    app_name = app["name"]
    components = (
        FULLSTACK_COMPONENTS if app.get("type") == "fullstack" else ("app",)
    )
    names: list[DerivedName] = [
        (
            "namespace",
            f"{project}-{app_name}",
            f"the namespace of app '{app_name}'",
        )
    ]
    for component in components:
        names.append(
            (
                "application",
                f"{project}-{app_name}-{component}",
                f"the {component} Application of app '{app_name}'",
            )
        )
    for environment, hostname in (app.get("hostnames") or {}).items():
        names.append(
            (
                "hostname",
                str(hostname).lower(),
                f"the {environment} hostname of app '{app_name}'",
            )
        )
    return names


def usable_apps(record: dict) -> list[dict]:
    apps = (record.get("spec") or {}).get("apps", [])
    if not isinstance(apps, list):
        return []
    return [
        app
        for app in apps
        if isinstance(app, dict) and isinstance(app.get("name"), str)
    ]


def project_name_of(record: dict) -> str | None:
    name = (record.get("metadata") or {}).get("name")
    return name if isinstance(name, str) else None


def check_app_name(project: str, app_name: str) -> list[str]:
    errors: list[str] = []
    if app_name == "system":
        errors.append(
            f"app 'system' is reserved: namespace '{project}-system' "
            "belongs to the project itself"
        )
    namespace = f"{project}-{app_name}"
    if len(namespace) > MAX_LABEL_LENGTH:
        errors.append(
            f"app '{app_name}' gives namespace '{namespace}' of "
            f"{len(namespace)} characters, the limit is {MAX_LABEL_LENGTH}"
        )
    elif not DNS_LABEL.match(namespace):
        errors.append(
            f"app '{app_name}' gives namespace '{namespace}' which is "
            "not a valid DNS-1123 label (lowercase alphanumerics and '-' only)"
        )
    return errors


def _claims(
    records: list[dict], skip_project: str
) -> dict[tuple[str, str], tuple[str, str]]:
    claims: dict[tuple[str, str], tuple[str, str]] = {}
    for record in records:
        other = project_name_of(record)
        if other is None or other == skip_project:
            continue
        for kind, name, source in project_names(other) + [
            derived
            for app in usable_apps(record)
            for derived in app_names(other, app)
        ]:
            claims.setdefault((kind, name), (other, source))
    return claims


def _collisions(
    candidate: list[DerivedName],
    claims: dict[tuple[str, str], tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    for kind, name, source in candidate:
        owner = claims.get((kind, name))
        if owner is not None:
            other, other_source = owner
            errors.append(
                f"{kind} '{name}' from {source} collides with "
                f"{other_source} of project '{other}'"
            )
    return errors


def check_new_project(project: str, existing: list[dict]) -> list[str]:
    return _collisions(project_names(project), _claims(existing, project))


def check_app(project: str, app: dict, existing: list[dict]) -> list[str]:
    """
    Check an app about to be added to, or refreshed in, ``project``.

    Apps already in the project's record are not re-checked: only the new
    entry is, against every other project and against the project's other apps.
    """
    errors = check_app_name(project, app["name"])
    claims = _claims(existing, project)
    for record in existing:
        if project_name_of(record) != project:
            continue
        for other_app in usable_apps(record):
            if other_app["name"] == app["name"]:
                continue
            for kind, name, source in app_names(project, other_app):
                claims.setdefault((kind, name), (project, source))
    return errors + _collisions(app_names(project, app), claims)
