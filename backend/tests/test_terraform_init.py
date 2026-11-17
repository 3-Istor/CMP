import pytest

from app.services.terraform_executor import TerraformExecutor


@pytest.fixture
def executor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    executor = TerraformExecutor(tmp_path, "app", "cnp/projects/p/app")
    executor.commands = []
    executor.fail_first = False

    def run(command, *args, **kwargs):
        executor.commands.append(command)
        if executor.fail_first and len(executor.commands) == 1:
            raise RuntimeError("Failed to query available provider packages")

    monkeypatch.setattr(executor, "_run_command", run)
    monkeypatch.setattr(executor, "_create_provider_override", lambda: None)
    (tmp_path / ".terraform.lock.hcl").write_text("# lock")
    return executor


def test_deploy_init_drops_the_lock_and_upgrades(executor, tmp_path):
    executor.init()

    assert not (tmp_path / ".terraform.lock.hcl").exists()
    assert "-upgrade" in executor.commands[0]


def test_destroy_init_reuses_the_lock_without_upgrade(executor, tmp_path):
    executor.init(reuse_lock=True)

    assert (tmp_path / ".terraform.lock.hcl").exists()
    assert len(executor.commands) == 1
    assert "-upgrade" not in executor.commands[0]


def test_destroy_init_falls_back_when_the_lock_is_stale(executor, tmp_path):
    executor.fail_first = True

    executor.init(reuse_lock=True)

    assert not (tmp_path / ".terraform.lock.hcl").exists()
    assert ["-upgrade" in c for c in executor.commands] == [False, True]
