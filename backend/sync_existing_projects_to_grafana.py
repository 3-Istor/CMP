#!/usr/bin/env python3
"""
Script to sync existing project members to Grafana.

This script fetches all projects from the database and syncs their members
to their respective Grafana organizations.

Usage:
    poetry run python sync_existing_projects_to_grafana.py
"""

import asyncio
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def main():
    """Sync all existing projects to Grafana."""
    print("\n" + "=" * 80)
    print("SYNC EXISTING PROJECTS TO GRAFANA")
    print("=" * 80)

    from app.core.database import SessionLocal
    from app.models.project import ProjectOwner
    from app.services.grafana_service import add_user_to_project_org
    from app.services.keycloak_service import list_project_members

    db = SessionLocal()

    try:
        # Get all projects that have owners
        project_owners = db.query(ProjectOwner).all()

        print(f"\nFound {len(project_owners)} projects with owners")

        for owner_record in project_owners:
            project_name = owner_record.project_name
            print(f"\n{'='*60}")
            print(f"Project: {project_name}")
            print(f"{'='*60}")

            # Get all members of this project
            try:
                members = list_project_members(project_name)
                print(f"  Members: {len(members)}")

                for member in members:
                    username = member["username"]
                    role = member["role"]

                    print(f"\n  Syncing: {username} ({role})...")

                    success = await add_user_to_project_org(
                        project_name, username, role
                    )

                    if success:
                        print(f"    ✅ Synced successfully")
                    else:
                        print(
                            f"    ⚠️  Sync failed (may not have logged in to Grafana yet)"
                        )

            except Exception as exc:
                print(f"  ❌ Error syncing project {project_name}: {exc}")
                logger.exception(f"Error syncing project {project_name}")

    finally:
        db.close()

    print("\n" + "=" * 80)
    print("SYNC COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
