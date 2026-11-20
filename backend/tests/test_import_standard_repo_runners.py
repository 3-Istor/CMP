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


def test_copied_security_workflow_skips_the_docker_image_scan():
    # Arrange
    workflow = (
        "  trivy-image:\n"
        "    needs: discover-images\n"
        "    if: needs.discover-images.outputs.matrix != '[]'\n"
    )

    # Act
    rewritten = standard_repo.on_import_runners(workflow)

    # Assert
    assert "    if: false" in rewritten


def test_generated_ci_builds_at_most_two_images_at_a_time():
    # Act
    workflow = standard_repo.ci_workflow([])

    # Assert
    assert "max-parallel: 2" in workflow
