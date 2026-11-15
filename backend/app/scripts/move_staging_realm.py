"""
Move the staging of an app that already runs onto its project's staging realm.

    python -m app.scripts.move_staging_realm <deployment_id>

Creates the realm, re-applies the staging module (the OIDC client is recreated
there), then commits the new realm into deploy/env/staging/*.yaml.
"""

import asyncio
import sys
from typing import Any

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.environment import AppEnvironment
from app.services.environments import activation, gitops, layout, staging_realm


def with_realm(values: dict[str, Any], realm: str) -> bool:
    """Point ingress.realm and auth.realm at *realm*, only where those blocks exist."""
    changed = False
    for block in ("ingress", "auth"):
        if isinstance(values.get(block), dict) and (
            values[block].get("realm") != realm
        ):
            values[block]["realm"] = realm
            changed = True
    return changed


async def move(deployment_id: int) -> None:
    with SessionLocal() as db:
        deployment = db.get(Deployment, deployment_id)
        row = db.get(AppEnvironment, (deployment_id, activation.STAGING))
    if deployment is None:
        raise SystemExit(f"No deployment {deployment_id}.")
    if row is None or row.state != "ready":
        raise SystemExit("Staging is not ready on this app: nothing to move.")
    variables = activation._variables(deployment)
    realm = variables["keycloak_realm_id"]

    await asyncio.to_thread(staging_realm.ensure, deployment.project_id)
    executor = activation._executor(deployment)
    await asyncio.to_thread(executor.init)
    await asyncio.to_thread(executor.apply, variables)

    kind = gitops.app_type(deployment)
    token = await gitops.token_for(deployment)

    async def build(head: str) -> dict[str, str]:
        files = {}
        for component in layout.components(kind):
            path = layout.env_values_file(activation.STAGING, component)
            values = await gitops.read_values(
                token, gitops.repo_of(deployment), path, head
            )
            if values is not None and with_realm(values, realm):
                files[path] = gitops.dump(values)
        return files

    sha = await gitops.commit(
        token,
        gitops.repo_of(deployment),
        build,
        "chore(env): move staging to its own realm via CMP",
    )
    print(f"{deployment.name} staging now uses {realm} ({sha}).")


if __name__ == "__main__":
    asyncio.run(move(int(sys.argv[1])))
