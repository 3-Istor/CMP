"""Read-only checks for the branch recipe. Never activates import or dispatches CI."""

import argparse
import asyncio
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi import HTTPException
from app.core.config import settings
from app.services.repository_imports import (
    require_gitops_consumer,
    require_workflow_configuration,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also check GitHub and Argo in read-only mode",
    )
    args = parser.parse_args()
    checks = {
        "IMPORT_WORKFLOW_SHA": bool(
            re.fullmatch(r"[0-9a-f]{40}", settings.IMPORT_WORKFLOW_SHA)
        ),
        "IMPORT_CHART_SHA": bool(
            re.fullmatch(r"[0-9a-f]{40}", settings.IMPORT_CHART_SHA)
        ),
        "IMPORT_CALLBACK_URL": settings.IMPORT_CALLBACK_URL.startswith("https://")
        and settings.IMPORT_CALLBACK_URL.rstrip("/").endswith("/api"),
        "IMPORT_ARGOCD_URL": settings.IMPORT_ARGOCD_URL.startswith("https://"),
        "IMPORT_ARGOCD_TOKEN": bool(settings.IMPORT_ARGOCD_TOKEN),
        "GITHUB_APP_PRIVATE_KEY": bool(settings.GITHUB_APP_PRIVATE_KEY),
        "GITHUB_INSTALLATION_ID": bool(settings.GITHUB_INSTALLATION_ID),
        "GITHUB_REGISTRY_TOKEN": bool(settings.GITHUB_REGISTRY_TOKEN),
        "VAULT_URL": bool(settings.VAULT_URL),
        "VAULT_TOKEN": bool(settings.VAULT_TOKEN),
        "TF_BACKEND_S3_ENABLED": settings.TF_BACKEND_S3_ENABLED,
        "IMPORT_ALLOWED_PROJECT": settings.CNP_REGISTRY_BRANCH == "main"
        or bool(settings.IMPORT_ALLOWED_PROJECT),
    }
    print("IMPORT_ENABLED=" + str(settings.IMPORT_ENABLED).lower())
    for field, ready in checks.items():
        print(("OK      " if ready else "MISSING ") + field)
    if not all(checks.values()):
        return 1
    if args.live:

        async def verify():
            await require_workflow_configuration()
            print("OK      workflow branch, exact SHA, callback and audience")
            await require_gitops_consumer()
            print(
                "OK      Argo revisions, registry contract and ImageUpdater protection"
            )

        try:
            asyncio.run(verify())
        except HTTPException as error:
            print("STOP    " + str(error.detail))
            return 1
        except Exception as error:
            print("STOP    " + type(error).__name__ + ": remote check unavailable")
            return 1
    print(
        "Readiness checks passed. No clone, dispatch, commit or deployment was performed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
