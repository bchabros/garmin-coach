#!/usr/bin/env python3
"""Exercise an actual release bundle in temporary storage without developer dependencies."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import tempfile

from garmin_coach import distribution


PROBE = """
import asyncio
import pathlib
import sys
from garmin_coach.core import db
from garmin_coach.marts import snapshot
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
home = pathlib.Path(sys.argv[1])
home.mkdir()
(home / '.env').write_text('DB_PATH=./data/garmin.db\\nDATA_START_DATE=2026-06-08\\n')
(home / 'data').mkdir()
conn = db.connect(str(home / 'data' / 'garmin.db'))
db.bootstrap(conn)
db.upsert_daily(conn, 'daily_metrics', {'date': '2026-07-03', 'hrv': 61, 'load_day': 80})
snapshot.rollup(conn)
conn.commit()
conn.close()
async def probe():
    params = StdioServerParameters(command=sys.argv[2], args=['--home', str(home)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool in ('get_digest', 'get_snapshot'):
                result = await session.call_tool(tool, {})
                assert not result.isError, result
asyncio.run(probe())
print('packaged stdio digest and snapshot: passed; client_acceptance: not_run')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="coach-runtime-smoke-") as temporary:
        folder = pathlib.Path(temporary)
        metadata = json.loads(args.manifest.read_text())
        metadata["bundle"]["url"] = args.bundle.resolve().as_uri()
        manifest = folder / "manifest.json"
        manifest.write_text(json.dumps(metadata))
        root = folder / "runtime with spaces"
        release = distribution.prepare_release(manifest, root)
        distribution.activate_release(release, root)
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                "PYTHONPATH",
                "VIRTUAL_ENV",
                "GARMIN_COACH_HOME",
                "DB_PATH",
                "PLANS_DIR",
                "LOG_PATH",
                "GARMINTOKENS",
                "GARMIN_EMAIL",
                "GARMIN_PASSWORD",
            )
        }
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        subprocess.run(
            [
                str(release / "venv" / "bin" / "python"),
                "-I",
                "-c",
                PROBE,
                str(folder / "athlete"),
                str(root / "bin" / "garmin-coach-mcp"),
            ],
            cwd=folder,
            env=env,
            check=True,
            timeout=120,
        )
        distribution.verify_release_inventory(release)
        distribution.prepare_release(manifest, root)


if __name__ == "__main__":
    main()
