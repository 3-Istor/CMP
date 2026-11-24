"""
The managed service that replaces an imported backing datastore as is.

The analyzer also suggests rewriting an engine onto Postgres (Elasticsearch
full text, MongoDB JSONB, Neo4j link tables): a migration of the app, not an
equivalent, so the CMP never offers it as the managed alternative.
"""

# MySQL moves to Postgres only when the analyzer found the app's SQL
# portable, which it says by recommending it.
TO_POSTGRES = ("postgres", "mysql")


def only_equivalents(view: dict) -> dict:
    """*view* of recommendations.for_project, without the rewrites."""
    kinds = {b["app"]: b["kind"] for b in view["backing"]}
    found = [
        r
        for r in view["recommendations"]
        if r["target"] != "postgres" or kinds.get(r["app"]) in TO_POSTGRES
    ]
    backing = []
    for item in view["backing"]:
        advised = {
            r["target"]
            for r in found
            if r["app"] == item["app"] and not r["dismissed"]
        }
        if item["kind"] == "postgres" or "postgres" in advised:
            alternative = "postgres"
        elif "bucket" in advised:
            alternative = "bucket"
        else:
            alternative = None
        backing.append({**item, "managed_alternative": alternative})
    return {**view, "recommendations": found, "backing": backing}
