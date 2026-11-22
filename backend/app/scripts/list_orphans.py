"""
List what deleted apps and projects left behind, as JSON. Never deletes.

    python -m app.scripts.list_orphans

Runs in the CMP pod (it needs the CMP's database, Vault, cluster and state
bucket access). Exits 1 when something is found or a source could not be
read, so a scheduled run shows up as failed.
"""

import asyncio
import json
import sys

from app.services import orphans


def main() -> int:
    result = asyncio.run(orphans.find())
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if result["orphans"] or result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
