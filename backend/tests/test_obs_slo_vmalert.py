"""
Replays synthetic Envoy series through the generated SLO rules with the real
vmalert-tool (VictoriaMetrics), so the PromQL is evaluated, not just read.
Skipped when vmalert-tool is not installed (CI only lints for now):
nix build -o vm nixpkgs#victoriametrics, then VMALERT_TOOL=vm/bin/vmalert-tool.
"""

import json
import os
import shutil
import subprocess

import pytest

from app.services.observability import slo

VMALERT_TOOL = os.environ.get("VMALERT_TOOL") or shutil.which("vmalert-tool")
pytestmark = pytest.mark.skipif(
    not VMALERT_TOOL, reason="vmalert-tool is not installed"
)

APP = slo.AppSlo(
    project="shop", app="web", app_id=12, namespace="shop-web", period_days=28
)
ROUTE = (
    'namespace="envoy-gateway-system", container="envoy", '
    'envoy_cluster_name="httproute/shop-web/shop-web-route/rule/0", '
    'cluster="onprem"'
)
MINUTES = 120


def _rules_without_annotations() -> dict:
    # Annotations are templated ($value): the test checks expressions only.
    groups = slo.groups(APP)
    for group in groups:
        for rule in group["rules"]:
            rule.pop("annotations", None)
    return {"groups": groups}


def _series(code_class: str, per_minute: float, route: str = ROUTE) -> dict:
    return {
        "series": (
            f"envoy_cluster_upstream_rq_xx{{{route}, "
            f'envoy_response_code_class="{code_class}"}}'
        ),
        "values": f"0+{per_minute}x{MINUTES}",
    }


def _alert(severity: str, window: str) -> dict:
    return {
        "exp_labels": {
            "cluster": "onprem",
            "cnp_project": "shop",
            "cnp_app": "web",
            "cnp_slo": "availability",
            "namespace": "shop-web",
            "severity": severity,
            "slo_window": window,
        }
    }


def _run(tmp_path, series: list[dict], expected: list[dict]) -> str:
    return _unittest(
        tmp_path,
        series,
        {
            "alert_rule_test": [
                {
                    "eval_time": "90m",
                    "alertname": "SloAvailabilityBurnRate",
                    "exp_alerts": expected,
                }
            ]
        },
    )


def _budget_left(tmp_path, series: list[dict], expected: list[dict]) -> str:
    return _unittest(
        tmp_path,
        series,
        {
            "metricsql_expr_test": [
                {
                    "expr": (
                        "slo:period_error_budget_remaining:ratio"
                        '{cnp_slo="availability"}'
                    ),
                    "eval_time": "92m",
                    "exp_samples": expected,
                }
            ]
        },
    )


def _left(value: float) -> dict:
    return {
        "labels": (
            'slo:period_error_budget_remaining:ratio{cluster="onprem", '
            'cnp_project="shop", cnp_app="web", cnp_slo="availability", '
            'namespace="shop-web"}'
        ),
        "value": value,
    }


def _unittest(tmp_path, series: list[dict], checks: dict) -> str:
    (tmp_path / "rules.json").write_text(
        json.dumps(_rules_without_annotations())
    )
    (tmp_path / "test.json").write_text(
        json.dumps(
            {
                "rule_files": [str(tmp_path / "rules.json")],
                "evaluation_interval": "1m",
                "tests": [
                    {"interval": "1m", "input_series": series, **checks}
                ],
            }
        )
    )
    result = subprocess.run(
        [
            VMALERT_TOOL,
            "unittest",
            "--disableAlertgroupLabel",
            f"--files={tmp_path / 'test.json'}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result.stdout + result.stderr if result.returncode else ""


def test_fast_burn_fires_just_above_14_4_times_the_budget(tmp_path):
    # 8 % errors: above 14.4 x 0.5 % = 7.2 %, so every tier fires.
    failure = _run(
        tmp_path,
        [_series("2", 920), _series("5", 80)],
        [
            _alert("critical", "1h"),
            _alert("warning", "6h"),
            _alert("info", "3d"),
        ],
    )

    assert failure == ""


def test_fast_burn_stays_silent_below_14_4_times_the_budget(tmp_path):
    # 6.5 % errors: above 6x (3 %), below 14.4x (7.2 %).
    failure = _run(
        tmp_path,
        [_series("2", 935), _series("5", 65)],
        [_alert("warning", "6h"), _alert("info", "3d")],
    )

    assert failure == ""


def test_nothing_fires_under_the_traffic_threshold(tmp_path):
    # Half the requests fail, but 30 requests an hour is below the minimum.
    failure = _run(tmp_path, [_series("2", 0.25), _series("5", 0.25)], [])

    assert failure == ""


def test_a_sleeping_app_does_not_burn_its_budget(tmp_path):
    asleep = {
        "series": (
            'offhours_target_asleep{namespace="shop-system", '
            'exported_namespace="shop-web", '
            'target="Deployment/shop-web-app-cnp-generic-app"}'
        ),
        "values": f"1x{MINUTES}",
    }

    failure = _run(tmp_path, [_series("5", 100), asleep], [])

    assert failure == ""


def test_another_apps_errors_are_not_counted(tmp_path):
    other_route = ROUTE.replace("shop-web", "shopping-web")

    failure = _run(
        tmp_path,
        [_series("2", 1000), _series("5", 1000, route=other_route)],
        [],
    )

    assert failure == ""


def test_the_budget_left_is_recorded_from_the_period_error_ratio(tmp_path):
    # 0.1 % errors on a 0.5 % budget: a fifth of the budget is spent.
    failure = _budget_left(
        tmp_path, [_series("2", 999), _series("5", 1)], [_left(0.8)]
    )

    assert failure == ""


def test_an_app_without_requests_keeps_its_whole_budget(tmp_path):
    failure = _budget_left(tmp_path, [_series("2", 0)], [_left(1)])

    assert failure == ""


VMALERT = os.environ.get("VMALERT") or shutil.which("vmalert")


@pytest.mark.skipif(not VMALERT, reason="vmalert is not installed")
def test_the_full_rule_file_with_its_templates_passes_vmalert_dry_run(
    tmp_path,
):
    (tmp_path / "rules.json").write_text(
        json.dumps({"groups": slo.groups(APP)})
    )

    result = subprocess.run(
        [VMALERT, "-dryRun", f"-rule={tmp_path / 'rules.json'}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]


def test_latency_quantiles_are_recorded_in_seconds_per_app(tmp_path):
    buckets = [
        {
            "series": (
                f"envoy_cluster_upstream_rq_time_bucket{{{ROUTE}, "
                f'le="{le}"}}'
            ),
            "values": f"0+{count}x{MINUTES}",
        }
        for le, count in (("100", 0), ("200", 100), ("+Inf", 100))
    ]

    failure = _unittest(
        tmp_path,
        buckets,
        {
            "metricsql_expr_test": [
                {
                    "expr": "cnp_slo:latency_seconds:p50_5m",
                    "eval_time": "60m",
                    "exp_samples": [
                        {
                            "labels": (
                                "cnp_slo:latency_seconds:p50_5m"
                                '{cluster="onprem", cnp_project="shop", '
                                'cnp_app="web", namespace="shop-web"}'
                            ),
                            "value": 0.15,
                        }
                    ],
                }
            ]
        },
    )

    assert failure == ""
