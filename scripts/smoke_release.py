#!/usr/bin/env python3
"""Exercise an actual release bundle in temporary storage without developer dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.request
import pathlib
import tempfile

from garmin_coach import distribution


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    parser.add_argument("--installer", type=pathlib.Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="coach-runtime-smoke-") as temporary:
        folder = pathlib.Path(temporary)
        home = folder / "system-home"
        home.mkdir()
        environment = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        cache = folder / "assets"
        cache.mkdir()
        metadata = json.loads(args.manifest.read_text())
        with urllib.request.urlopen(metadata["python"]["url"], timeout=60) as response:
            (cache / "python.tar.gz").write_bytes(response.read())
        shutil.copyfile(args.bundle, cache / "bundle.tar.gz")
        shutil.copyfile(args.manifest, cache / "manifest.json")
        root = folder / "runtime with spaces"
        subprocess.run(
            [
                "/bin/sh",
                str(args.installer.resolve()),
                "--prepare-only",
                "--runtime-root",
                str(root),
            ],
            cwd=folder,
            env={**environment, "GARMIN_COACH_ASSET_CACHE": str(cache)},
            check=True,
        )
        state = distribution.read_state(root)
        release = pathlib.Path(state["release_dir"])
        distribution.check_runtime_protocol(release, launcher=root / "bin" / "garmin-coach-mcp")
        distribution.verify_release_inventory(release)
        subprocess.run(
            [str(root / "bin" / "garmin-coach"), "--help"], cwd=folder, env=environment, check=True
        )
        assert not (home / "GarminCoach").exists()
        assert not (home / "Library" / "LaunchAgents").exists()


if __name__ == "__main__":
    main()
