from unittest import mock

import pytest

from app.services.alerts_catalog import BY_ID, namespace_matcher

NS = namespace_matcher(["shop-web"])


@pytest.mark.parametrize(
    "alert, expected",
    [
        (
            "error_logs",
            "sum by (namespace) (sum_over_time("
            'cnp_log_error_lines:count1m{namespace=~"shop-web"}[5m])) > 20',
        ),
        (
            "log_volume",
            "sum by (namespace) (sum_over_time("
            'cnp_log_lines:count1m{namespace=~"shop-web"}[10m])) > 30000',
        ),
    ],
)
def test_victorialogs_counters_are_summed_per_minute_samples(alert, expected):
    template = BY_ID[alert]

    with mock.patch("app.services.alerts_catalog.settings.LOGS_BACKEND", "vl"):
        expr = template.expr(NS, template.defaults())

    assert expr == expected


def test_loki_stays_the_default_until_the_switch():
    template = BY_ID["error_logs"]

    expr = template.expr(NS, template.defaults())

    assert "increase(loki_process_custom_log_error_lines_total" in expr
