"""The local auth launcher never rotates an existing OAuth encryption key."""

import base64
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def local_setup(tmp_path):
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "CMP"
    (root / "scripts").mkdir(parents=True)
    (root / "backend/data").mkdir(parents=True)
    (root / "frontend").mkdir()
    (tmp_path / "app-templates").mkdir()
    for relative in [
        "scripts/import-local.sh",
        "backend/.env.import-local.example",
        "frontend/.env.local.example",
    ]:
        shutil.copyfile(source / relative, root / relative)
    (root / "scripts/import-local.sh").chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ["git", "node", "npm", "poetry"]:
        path = tools / name
        path.write_text(
            "#!/bin/sh\n"
            + (
                "printf 'import-fichier-joe-raph\\n'\n"
                if name == "git"
                else "exit 0\n"
            )
        )
        path.chmod(0o755)
    return root, {"PATH": str(tools) + os.pathsep + os.defpath}


def setup(local_setup):
    root, environment = local_setup
    return subprocess.run(
        [str(root / "scripts/import-local.sh"), "setup"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_new_local_setup_generates_one_key_and_preserves_it_on_the_next_run(
    local_setup,
):
    root, _ = local_setup
    assert setup(local_setup).returncode == 0
    path = root / "backend/.env"
    first = path.read_text()
    key = next(
        line.split("=", 1)[1]
        for line in first.splitlines()
        if line.startswith("GITHUB_OAUTH_TOKEN_KEY=")
    )
    assert len(base64.urlsafe_b64decode(key)) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    second = setup(local_setup)
    assert second.returncode == 0 and path.read_text() == first
    assert key not in second.stdout and key not in second.stderr


def test_existing_credentials_without_the_original_key_block_setup(
    local_setup,
):
    root, _ = local_setup
    with sqlite3.connect(root / "backend/data/import-local.db") as connection:
        connection.execute(
            "CREATE TABLE github_oauth_credentials (user_sub TEXT)"
        )
        connection.execute(
            "INSERT INTO github_oauth_credentials VALUES ('test-user')"
        )
    result = setup(local_setup)
    assert result.returncode != 0
    assert "original encryption key" in result.stderr
    assert not (root / "backend/.env").exists()
