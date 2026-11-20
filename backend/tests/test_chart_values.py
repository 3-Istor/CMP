from app.services import chart_values

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "replicaCount": {"type": "integer", "maximum": 10},
        "db": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "storage": {"type": "string"},
            },
        },
    },
}
DEFAULTS = {"replicaCount": 1, "db": {"enabled": False, "storage": "1Gi"}}


def test_coalesce_merges_nested_maps_over_the_defaults():
    merged = chart_values.coalesce(DEFAULTS, {"db": {"enabled": True}})

    assert merged == {
        "replicaCount": 1,
        "db": {"enabled": True, "storage": "1Gi"},
    }


def test_coalesce_removes_a_default_set_to_null_like_helm():
    merged = chart_values.coalesce(DEFAULTS, {"db": {"storage": None}})

    assert merged["db"] == {"enabled": False}


def test_schema_errors_accepts_values_that_fit():
    errors = chart_values.schema_errors(SCHEMA, DEFAULTS, {"replicaCount": 3})

    assert errors == []


def test_schema_errors_names_the_path_of_a_value_out_of_bounds():
    errors = chart_values.schema_errors(
        SCHEMA, DEFAULTS, {"db": {"storage": 5}}
    )

    assert errors == ["db.storage: 5 is not of type 'string'"]


def test_schema_errors_refuses_a_key_the_chart_does_not_know():
    errors = chart_values.schema_errors(SCHEMA, DEFAULTS, {"replicas": 2})

    assert len(errors) == 1 and "'replicas' was unexpected" in errors[0]
