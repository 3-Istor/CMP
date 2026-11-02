"""Read-only preflight. Never activates import, clones a source or dispatches CI."""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.github_account import check_oauth_configuration
from app.services.github_service import generate_jwt
from app.services.repository_imports import (
    github,
    import_state_configuration,
    require_gitops_consumer,
    require_workflow_configuration,
)


def configuration_checks():
    return {
        "IMPORT_DISPATCH_WORKFLOW": settings.IMPORT_DISPATCH_WORKFLOW
        in {"docker-image.yml", "import-project.yml"},
        "IMPORT_IMAGE_PREFIX": settings.IMPORT_IMAGE_PREFIX.startswith(
            "ghcr.io/"
        ),
        "IMPORT_WORKFLOW_SHA": bool(
            re.fullmatch(r"[0-9a-f]{40}", settings.IMPORT_WORKFLOW_SHA)
        ),
        "IMPORT_CHART_SHA": bool(
            re.fullmatch(r"[0-9a-f]{40}", settings.IMPORT_CHART_SHA)
        ),
        "IMPORT_CALLBACK_URL": settings.IMPORT_CALLBACK_URL.startswith(
            "https://"
        )
        and settings.IMPORT_CALLBACK_URL.rstrip("/").endswith("/api"),
        "IMPORT_OIDC_AUDIENCE": bool(settings.IMPORT_OIDC_AUDIENCE),
        "IMPORT_ARGOCD_URL": settings.IMPORT_ARGOCD_URL.startswith("https://"),
        "IMPORT_ARGOCD_TOKEN": bool(settings.IMPORT_ARGOCD_TOKEN),
        **check_oauth_configuration(),
        "GITHUB_APP_PRIVATE_KEY": bool(settings.GITHUB_APP_PRIVATE_KEY),
        "GITHUB_INSTALLATION_ID": bool(settings.GITHUB_INSTALLATION_ID),
        "GITHUB_REGISTRY_TOKEN": bool(settings.GITHUB_REGISTRY_TOKEN),
        "VAULT_URL": bool(settings.VAULT_URL),
        "VAULT_TOKEN": bool(settings.VAULT_TOKEN),
        **import_state_configuration(),
        "CNP_REGISTRY_BRANCH_MAIN": settings.CNP_REGISTRY_BRANCH == "main",
    }


def report(checks):
    for field, ready in checks.items():
        print(("OK      " if ready else "MISSING ") + field)
    return all(checks.values())


async def github_linking_checks():
    """Check this App's OAuth configuration without any Keycloak federation."""
    checks = check_oauth_configuration()
    if not all(checks.values()):
        return checks
    app = await github("GET", "/app", generate_jwt())
    return {
        **checks,
        "CNP_PORTAL_APP": app.get("slug") == "cnp-portal"
        and app.get("owner", {}).get("login", "").lower() == "3-istor"
        and app.get("client_id") == settings.GITHUB_OAUTH_CLIENT_ID,
    }


async def live_checks():
    # Browser OAuth and current user/repository access are checked by the portal.
    # No GitHub provider or federation is required in Keycloak.
    token = await require_workflow_configuration()
    print("OK      workflow branch, exact SHA, callback and audience")
    chart = await github(
        "GET",
        "/repos/3-Istor/infra-templates/commits/" + settings.IMPORT_CHART_SHA,
        token,
    )
    if chart.get("sha") != settings.IMPORT_CHART_SHA:
        raise HTTPException(503, "Approved chart commit is unavailable")
    print("OK      approved chart commit exists in GitHub")
    await require_gitops_consumer()
    print(
        "OK      Argo revisions, registry contract and ImageUpdater protection"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            settings.VAULT_URL.rstrip("/") + "/v1/auth/token/lookup-self",
            headers={"X-Vault-Token": settings.VAULT_TOKEN},
        )
        response.raise_for_status()
    print("OK      Vault token accepted (no application secret read)")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--live",
        action="store_true",
        help="Also read GitHub, Argo and Vault configuration",
    )
    group.add_argument(
        "--github",
        action="store_true",
        help="Check GitHub linking prerequisites independently of CI configuration",
    )
    args = parser.parse_args(argv)
    print("IMPORT_ENABLED=" + str(settings.IMPORT_ENABLED).lower())
    try:
        if args.github:
            ready = report(asyncio.run(github_linking_checks()))
        else:
            ready = report(configuration_checks())
            if ready and args.live:
                ready = asyncio.run(live_checks())
    except Exception as error:
        # HTTP bodies and error details can contain upstream secrets: never print them.
        response = getattr(error, "response", None)
        status = getattr(
            response, "status_code", getattr(error, "status_code", None)
        )
        suffix = (
            " (HTTP " + str(status) + ")" if isinstance(status, int) else ""
        )
        print(
            "STOP    "
            + type(error).__name__
            + suffix
            + ": read-only check unavailable"
        )
        return 1
    if not ready:
        return 1
    print(
        "Preflight passed. User OAuth and real import remain to be tested; S3 access is checked by Terraform init/plan."
    )
    print(
        "No clone, dispatch, commit, state write or deployment was performed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
