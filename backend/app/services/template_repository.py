"""
Template Repository Manager

Handles cloning, syncing, and reading templates from the public Git repository.
Templates are cached and synced every 24 hours.
"""

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import git

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/3-Istor/app-templates.git"
REPO_LOCAL_PATH = Path("./data/templates")
SYNC_INTERVAL_HOURS = 24


class TemplateRepository:
    """Manages the local clone of the template repository."""

    def __init__(self):
        self.repo_path = REPO_LOCAL_PATH
        self.last_sync: datetime | None = None
        self._ensure_repo()

    def _ensure_repo(self) -> None:
        """Clone the repository if it doesn't exist, or pull latest changes."""
        if not self.repo_path.exists():
            logger.info("Cloning template repository from %s", REPO_URL)
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)
            git.Repo.clone_from(REPO_URL, self.repo_path)
            self.last_sync = datetime.now()
        else:
            self._sync_if_needed()

    def _sync_if_needed(self) -> None:
        """Pull latest changes if sync interval has passed."""
        if self.last_sync and datetime.now() - self.last_sync < timedelta(
            hours=SYNC_INTERVAL_HOURS
        ):
            return

        try:
            logger.info("Syncing template repository...")
            repo = git.Repo(self.repo_path)
            origin = repo.remotes.origin
            origin.pull()
            self.last_sync = datetime.now()
            logger.info("Template repository synced successfully")
        except Exception as exc:
            logger.error("Failed to sync template repository: %s", exc)

    def get_available_templates(self) -> list[dict[str, Any]]:
        """
        Scan the templates directory and return all enabled templates
        with valid manifests.
        """
        self._sync_if_needed()
        templates = []
        templates_dir = self.repo_path / "templates"

        if not templates_dir.exists():
            logger.warning("Templates directory not found: %s", templates_dir)
            return []

        for template_dir in templates_dir.iterdir():
            if not template_dir.is_dir():
                continue

            manifest_path = template_dir / "manifest.json"
            if not manifest_path.exists():
                logger.debug(
                    "Skipping %s: no manifest.json", template_dir.name
                )
                continue

            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                # Only include enabled templates
                if not manifest.get("enabled", False):
                    logger.debug(
                        "Skipping %s: not enabled", template_dir.name
                    )
                    continue

                # Add template path for Terraform execution
                manifest["_template_path"] = str(template_dir)

                # Handle icon image path
                if "image_path" in manifest:
                    image_full_path = template_dir / manifest["image_path"]
                    if image_full_path.exists():
                        manifest["_image_full_path"] = str(image_full_path)

                templates.append(manifest)
                logger.debug("Loaded template: %s", manifest.get("id"))

            except Exception as exc:
                logger.error(
                    "Failed to load manifest from %s: %s",
                    template_dir.name,
                    exc,
                )

        return templates

    def get_template_by_id(self, template_id: str) -> dict[str, Any] | None:
        """Get a specific template by ID."""
        templates = self.get_available_templates()
        return next((t for t in templates if t["id"] == template_id), None)

    def get_template_path(self, template_id: str) -> Path | None:
        """Get the filesystem path to a template directory."""
        template = self.get_template_by_id(template_id)
        if not template:
            return None
        return Path(template["_template_path"])

    def force_sync(self) -> None:
        """Force an immediate sync of the repository."""
        self.last_sync = None
        self._sync_if_needed()


# Global singleton instance
_repository: TemplateRepository | None = None


def get_repository() -> TemplateRepository:
    """Get or create the global repository instance."""
    global _repository
    if _repository is None:
        _repository = TemplateRepository()
    return _repository
