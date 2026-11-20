from app.services.imports import standard_repo


def test_copied_template_workflows_run_only_on_the_import_runners():
    # Arrange
    workflow = (
        "jobs:\n"
        "  scan:\n"
        '    runs-on: ${{ fromJSON(vars.CNP_CI_RUNS_ON || \'["self-hosted","Linux","X64"]\') }}\n'
        "  report:\n"
        "    runs-on: ubuntu-latest\n"
    )

    # Act
    rewritten = standard_repo.on_import_runners(workflow)

    # Assert
    assert (
        rewritten.count(
            "runs-on: ${{ fromJSON(vars.CNP_IMPORT_RUNS_ON || "
            '\'["self-hosted","cnp-import-ephemeral"]\') }}'
        )
        == 2
    )
