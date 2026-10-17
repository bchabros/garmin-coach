#!/usr/bin/env python3
"""Exercise an actual release bundle in temporary storage without developer dependencies."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile

from garmin_coach import distribution


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
        distribution.check_runtime_protocol(release, launcher=root / "bin" / "garmin-coach-mcp")
        distribution.verify_release_inventory(release)
        distribution.prepare_release(manifest, root)


if __name__ == "__main__":
    main()
