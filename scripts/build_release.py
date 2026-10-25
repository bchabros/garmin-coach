#!/usr/bin/env python3
"""Build one native macOS versioned wheelhouse and verified terminal installer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON_RELEASE = "20261013"
PYTHON_VERSION = "3.13.16"
PYTHON_HASHES = {
    "arm64": "ca0e6a70fb75a0984751bd75493e3de77680aef7fbd0fea9b903c299537b3423",
    "x86_64": "d1f2e7b5f0cfa25fc856fea819890bbe722f47a692e5ce2552636b5240930b38",
}


def _load_coach_resources():
    """Load the package's public resource selection by path, without its dependencies."""
    path = ROOT / "src/garmin_coach/coach_resources.py"
    spec = importlib.util.spec_from_file_location("coach_resources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coach_resources = _load_coach_resources()


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_wheel(path: pathlib.Path) -> None:
    """Allow only tracked program/skill files and standard generated wheel metadata."""
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files", "-z", "src/garmin_coach", "skills/coach", "LICENSE"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    public_skills = {"skills/coach/" + name for name in coach_resources.public_names()}
    metadata_names = {"METADATA", "WHEEL", "RECORD", "entry_points.txt"}
    machine_path = re.compile(rb"(?:/Users/[^/\s]+/|/home/[^/\s]+/|[A-Za-z]:\\Users\\)")
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = item.filename
            parts = pathlib.PurePosixPath(name).parts
            source = "src/" + name if parts[0] == "garmin_coach" else name
            generated = (
                len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in metadata_names
            )
            license_file = (
                len(parts) == 3
                and parts[0].endswith(".dist-info")
                and parts[1:] == ("licenses", "LICENSE")
                and "LICENSE" in tracked
            )
            if name.startswith("skills/") and name not in public_skills:
                raise ValueError(f"Unapproved coach resource in release wheel: {name}")
            if source not in tracked and not generated and not license_file:
                raise ValueError(f"Unapproved or untracked file in release wheel: {name}")
            if machine_path.search(archive.read(name)):
                raise ValueError(f"Machine-specific path found in release wheel: {name}")


def check_public_resources(root: pathlib.Path) -> None:
    """Refuse a release whose skill includes or public files break the resource manifest.

    Args:
        root: Source checkout holding ``pyproject.toml`` and ``skills/coach``.

    Raises:
        ValueError: The wheel or sdist include list differs from the public manifest, or a
            public resource is missing or reached through a symlink.
    """
    expected = {"skills/coach/" + name for name in coach_resources.public_names()}
    config = tomllib.loads((root / "pyproject.toml").read_text())
    for distribution_format in ("wheel", "sdist"):
        included = {
            item["path"]
            for item in config["tool"]["poetry"]["include"]
            if item["path"].startswith("skills/")
            and distribution_format in item.get("format", ["sdist"])
        }
        if included != expected:
            raise ValueError(
                f"The {distribution_format} includes must match the public coach resource manifest."
            )
    coach_resources.read_public(root / "skills/coach")


def _build_wheel(staging: pathlib.Path) -> pathlib.Path:
    check_public_resources(ROOT)
    subprocess.run(
        ["poetry", "build", "--format", "wheel", "--output", str(staging / "dist")],
        cwd=ROOT,
        check=True,
    )
    (wheel,) = (staging / "dist").glob("*.whl")
    audit_wheel(wheel)
    return wheel


def _locked_requirements(staging: pathlib.Path) -> pathlib.Path:
    requirements = staging / "locked.txt"
    subprocess.run(
        [
            "poetry",
            "export",
            "--only",
            "main",
            "--format",
            "requirements.txt",
            "--output",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    return requirements


def _wheelhouse(
    bundle: pathlib.Path, wheel: pathlib.Path, locked: pathlib.Path, version: str
) -> None:
    wheels = bundle / "wheels"
    wheels.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(locked),
            "--dest",
            str(wheels),
        ],
        check=True,
    )
    shutil.copy2(wheel, wheels / wheel.name)
    (bundle / "requirements.txt").write_text(
        locked.read_text() + f"\ngarmin-coach=={version} --hash=sha256:{digest(wheel)}\n"
    )


def build_bootstrap(target: pathlib.Path) -> None:
    """Bundle canonical stdlib modules into one isolated executable Python archive."""
    with tempfile.TemporaryDirectory(prefix="bootstrap-") as temporary:
        package = pathlib.Path(temporary) / "garmin_coach"
        package.mkdir()
        (package / "__init__.py").write_text("")
        for name in ("distribution.py", "program_receipt.py"):
            shutil.copyfile(ROOT / "src" / "garmin_coach" / name, package / name)
        (pathlib.Path(temporary) / "__main__.py").write_text(
            "from garmin_coach.distribution import main\nraise SystemExit(main())\n"
        )
        zipapp.create_archive(temporary, target=target)


def _canonical_assets(bundle: pathlib.Path) -> None:
    build_bootstrap(bundle / "bootstrap.py")
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files", "skills/coach"], cwd=ROOT, text=True
        ).splitlines()
    )
    try:
        public = coach_resources.read_public(ROOT / "skills/coach")
    except ValueError as error:
        raise ValueError("Release coach resources must be tracked regular files.") from error
    for name, content in public.items():
        relative = "skills/coach/" + name
        if relative not in tracked:
            raise ValueError("Release coach resources must be tracked regular files.")
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _bundle_archive(bundle: pathlib.Path, archive: pathlib.Path) -> None:
    with tarfile.open(archive, "w:gz") as content:
        for path in sorted(bundle.iterdir()):
            content.add(path, arcname=path.name)


def _metadata(
    version: str, architecture: str, source: str, archive: pathlib.Path, base: str
) -> dict:
    target = "aarch64" if architecture == "arm64" else "x86_64"
    python_url = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PYTHON_RELEASE}/cpython-{PYTHON_VERSION}%2B{PYTHON_RELEASE}-"
        f"{target}-apple-darwin-install_only.tar.gz"
    )
    return {
        "schema_version": 1,
        "version": version,
        "source_commit": source,
        "skill_version": version,
        "client_version": version,
        "platform": "darwin",
        "architecture": architecture,
        "min_macos": "15.0",
        "python": {
            "url": python_url,
            "sha256": PYTHON_HASHES[architecture],
            "version": PYTHON_VERSION,
        },
        "bundle": {"url": f"{base}/{archive.name}", "sha256": digest(archive)},
    }


def _installer(
    output: pathlib.Path, manifest: pathlib.Path, metadata: dict, base: str
) -> pathlib.Path:
    values = {
        "ARCH": metadata["architecture"],
        "VERSION": metadata["version"],
        "PYTHON_URL": metadata["python"]["url"],
        "PYTHON_SHA": metadata["python"]["sha256"],
        "BUNDLE_URL": metadata["bundle"]["url"],
        "BUNDLE_SHA": metadata["bundle"]["sha256"],
        "MANIFEST_URL": f"{base}/{manifest.name}",
        "MANIFEST_SHA": digest(manifest),
    }
    template = (ROOT / "scripts" / "install.sh.in").read_text()
    for key, value in values.items():
        if "'" in value or "\n" in value:
            raise ValueError("Unsafe release metadata value.")
        template = template.replace("@@" + key + "@@", value)
    installer = output / f"install-{metadata['architecture']}.sh"
    installer.write_text(template)
    installer.chmod(0o755)
    return installer


def build(output: pathlib.Path, tag: str, base: str) -> None:
    """Produce a release manifest, bundle and installer from locked Poetry dependencies."""
    architecture = platform.machine()
    if platform.system() != "Darwin" or architecture not in PYTHON_HASHES:
        raise ValueError("Release builds require a native supported macOS runner.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["poetry"]["version"]
    if tag != "v" + version:
        raise ValueError("Release tag must match the package version exactly.")
    output.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="coach-release-") as temporary:
        staging = pathlib.Path(temporary)
        bundle = staging / "bundle"
        _wheelhouse(bundle, _build_wheel(staging), _locked_requirements(staging), version)
        _canonical_assets(bundle)
        archive = output / f"garmin-coach-{version}-macos-{architecture}.tar.gz"
        _bundle_archive(bundle, archive)
    metadata = _metadata(version, architecture, source, archive, base)
    manifest = output / f"manifest-{architecture}.json"
    manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    installer = _installer(output, manifest, metadata, base)
    (output / f"SHA256SUMS-{architecture}.txt").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in (archive, manifest, installer))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-base", required=True)
    args = parser.parse_args()
    build(args.output.resolve(), args.tag, args.release_base.rstrip("/"))


if __name__ == "__main__":
    main()
