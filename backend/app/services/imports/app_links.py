"""
Links between two apps of a project, drawn on the topology (CNP-240, CNP-242).

A link is a ``dependsOn`` entry of the calling app in the registry record:
the caller's chart renders an ExternalName Service named after the alias,
pointing at the target app. The record is the source of truth; the import
row of the caller keeps a copy so a rebuild writes the link again.

v1 links imported apps only (types service and backing): the cnp-projects
schema refuses ``dependsOn`` on static and fullstack apps, and its validator
refuses a link whose target is one of them.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.imports import ImportService, ImportSource
from app.services import topology_cache
from app.services.imports import generate
from app.services.imports.registry_commit import commit_record_change
from app.services.imports.service import ImportRefused, _active_run

logger = logging.getLogger(__name__)

LINKABLE_TYPES = ("service", "backing")
MAX_ALIAS = 63
# The chart names a canary app's Services <app>-stable and <app>-canary.
OWN_SERVICE_SUFFIXES = ("", "-stable", "-canary")


def _import_row(db: Session, project: str, app: str) -> ImportService | None:
    return (
        db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.app_name == app,
        )
        .first()
    )


def target_ports(db: Session, project: str, app: str) -> list[int]:
    row = _import_row(db, project, app)
    if row is None:
        return []
    return [int(p["port"]) for p in row.spec.get("ports") or []]


def check_alias(source: str, alias: str) -> list[str]:
    """The alias is a Service name in the caller's namespace (D18)."""
    if len(alias) > MAX_ALIAS or not generate.IMPORTED_NAME.match(alias):
        return [
            f"alias '{alias}' is not a DNS-1035 label: lowercase letters, "
            "digits and '-', starting with a letter, at most 63 characters"
        ]
    if alias in generate.RESERVED_ALIASES:
        return [f"alias '{alias}' is reserved by the platform"]
    if alias in {source + suffix for suffix in OWN_SERVICE_SUFFIXES}:
        return [f"alias '{alias}' is a Service of '{source}' itself"]
    return []


def _apps(record: dict) -> dict[str, dict]:
    return {
        app["name"]: app
        for app in (record.get("spec") or {}).get("apps") or []
        if isinstance(app, dict) and "name" in app
    }


def _linkable(apps: dict[str, dict], name: str) -> dict:
    app = apps.get(name)
    if app is None:
        raise ImportRefused(f"'{name}' is not an app of this project", 404)
    if app.get("type") not in LINKABLE_TYPES:
        raise ImportRefused(
            f"'{name}' is a {app.get('type')} app: links connect imported "
            "apps (service or backing) only",
            422,
        )
    return app


def _no_import_running(db: Session, project: str, app: str) -> None:
    """A deploy rewrites the caller's entry from its spec: wait for it."""
    row = _import_row(db, project, app)
    if row is not None and _active_run(row.source) is not None:
        raise ImportRefused(
            f"An import of '{app}' is running: link it once it is done"
        )


async def add_link(
    db: Session,
    project: str,
    source: str,
    target: str,
    port: int,
    alias: str | None,
) -> dict:
    """
    Write ``source`` dependsOn ``target`` in one registry commit.

    Raises:
        ImportRefused: 404 for an unknown app, 409 when the alias is taken,
            422 for anything the registry would refuse.
    """
    alias = alias or target
    if source == target:
        raise ImportRefused("An app cannot depend on itself", 422)
    problems = check_alias(source, alias)
    if problems:
        raise ImportRefused("; ".join(problems), 422)
    ports = target_ports(db, project, target)
    if port not in ports:
        exposed = ", ".join(str(p) for p in ports) or "none known"
        raise ImportRefused(
            f"'{target}' does not expose port {port} ({exposed})", 422
        )
    _no_import_running(db, project, source)
    link = {"app": target, "port": port, "alias": alias}

    async def change(record: dict, head: str) -> dict[str, str | None]:
        del head
        apps = _apps(record)
        caller = _linkable(apps, source)
        _linkable(apps, target)
        current = caller.setdefault("dependsOn", [])
        for existing in current:
            if (existing.get("alias") or existing.get("app")) == alias:
                raise ImportRefused(
                    f"'{source}' already resolves '{alias}' to "
                    f"'{existing.get('app')}'",
                    409,
                )
        entry = {"app": target, "port": port}
        if alias != target:
            entry["alias"] = alias
        current.append(entry)
        return {}

    commit = await commit_record_change(
        project, change, f"feat(registry): link {source} to {target}:{port}"
    )
    topology_cache.invalidate(project)

    def update(spec: dict) -> list[dict]:
        return [
            d for d in spec.get("depends_on") or [] if d["alias"] != alias
        ] + [{**link, "manual": True}]

    _update_row(db, project, source, update, commit)
    return {"from": source, **link, "commit": commit}


async def remove_link(
    db: Session,
    project: str,
    source: str,
    target: str,
    alias: str | None,
) -> str:
    """Drop the matching dependsOn entries of ``source``; the commit SHA."""
    _no_import_running(db, project, source)

    def matches(entry: dict) -> bool:
        return entry.get("app") == target and (
            alias is None or (entry.get("alias") or target) == alias
        )

    async def change(record: dict, head: str) -> dict[str, str | None]:
        del head
        caller = _apps(record).get(source)
        links = (caller or {}).get("dependsOn") or []
        kept = [d for d in links if not matches(d)]
        if caller is None or len(kept) == len(links):
            raise ImportRefused(f"'{source}' has no link to '{target}'", 404)
        if kept:
            caller["dependsOn"] = kept
        else:
            del caller["dependsOn"]
        return {}

    commit = await commit_record_change(
        project, change, f"feat(registry): unlink {source} from {target}"
    )
    topology_cache.invalidate(project)

    def update(spec: dict) -> list[dict]:
        return [
            d
            for d in spec.get("depends_on") or []
            if not (
                d["app"] == target and (alias is None or d["alias"] == alias)
            )
        ]

    _update_row(db, project, source, update, commit)
    return commit


def _update_row(db: Session, project: str, app: str, links, commit: str):
    """
    Copy a committed link change to the caller's import row. The registry
    already holds it: a failure here is logged, not raised, and
    :func:`reconcile_rows` copies the record's links on the next read.
    """
    try:
        row = _import_row(db, project, app)
        if row is None:
            return
        row.spec = {**row.spec, "depends_on": links(row.spec)}
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Links of %s/%s committed to the registry (%s) but not to its "
            "import row: the next topology read reconciles it",
            project,
            app,
            commit,
        )


def reconcile_rows(
    db: Session, project: str, record_apps: dict[str, dict]
) -> None:
    """
    Make the manual links of each idle imported service match its record
    entry, the source of truth: a link drawn on the topology whose row update
    was lost is added, one removed from the record is dropped. Links from the
    analysis are left alone, the next build writes them from the plan.
    """
    rows = (
        db.query(ImportService)
        .join(ImportSource)
        .filter(
            ImportSource.project_name == project,
            ImportService.kind == "service",
        )
        .all()
    )
    changed = False
    for row in rows:
        app = record_apps.get(row.app_name)
        if app is None or _active_run(row.source) is not None:
            continue
        current = list(row.spec.get("depends_on") or [])
        wanted = _reconciled_links(current, app.get("dependsOn") or [])
        if wanted != current:
            row.spec = {**row.spec, "depends_on": wanted}
            changed = True
    if not changed:
        return
    try:
        db.commit()
        logger.warning("Import rows of %s reconciled with the record", project)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Import rows of %s could not be reconciled", project)


def _reconciled_links(current: list[dict], record_links: list) -> list[dict]:
    in_record = {}
    for entry in record_links:
        if isinstance(entry, dict) and entry.get("app"):
            alias = entry.get("alias") or entry["app"]
            in_record[alias] = {
                "app": entry["app"],
                "port": entry.get("port"),
                "alias": alias,
            }
    kept = [
        link
        for link in current
        if not link.get("manual") or link.get("alias") in in_record
    ]
    known = {link.get("alias") for link in kept}
    added = [
        {**link, "manual": True}
        for alias, link in in_record.items()
        if alias not in known
    ]
    return kept + added
