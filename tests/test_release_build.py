"""Release publication checks reject files outside the tracked program sources."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import zipfile

import pytest


@pytest.fixture
def release_builder():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"
    spec = importlib.util.spec_from_file_location("release_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wheel_audit_rejects_untracked_private_skill_resources(tmp_path, release_builder):
    wheel = tmp_path / "release.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("skills/coach/references/private-athlete-context.md", "Athlete facts")
    with pytest.raises(ValueError, match="tracked|Unapproved|unapproved"):
        release_builder.audit_wheel(wheel)


@pytest.mark.skipif(sys.platform != "darwin", reason="Generated installer requires native macOS.")
@pytest.mark.parametrize("corrupt", [False, True])
def test_rendered_installer_checks_assets_and_runs_real_private_cli(
    tmp_path, release_builder, corrupt
):
    import json
    import os
    import shutil
    import subprocess
    from tests.test_distribution import _release_fixture

    manifest = _release_fixture(tmp_path)
    metadata = json.loads(manifest.read_text())
    metadata["architecture"] = os.uname().machine
    metadata["python"]["url"] = (tmp_path / "unpublished-python.tar.gz").as_uri()
    metadata["bundle"]["url"] = (tmp_path / "unpublished-bundle.tar.gz").as_uri()
    manifest.write_text(json.dumps(metadata))
    installer = release_builder._installer(tmp_path, manifest, metadata, "https://example.invalid")
    cache = tmp_path / "asset-cache"
    cache.mkdir()
    for name in ("python.tar.gz", "bundle.tar.gz", "manifest.json"):
        shutil.copyfile(tmp_path / name, cache / name)
    if corrupt:
        (cache / "python.tar.gz").write_text("corrupted bytes")
    home, root = tmp_path / "isolated-home", tmp_path / "runtime with spaces"
    home.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "GARMIN_COACH_HOME", "DB_PATH", "GARMINTOKENS")
    }
    environment.update(
        HOME=str(home), GARMIN_COACH_ASSET_CACHE=str(cache), PATH="/usr/bin:/bin:/usr/sbin:/sbin"
    )
    result = subprocess.run(
        ["/bin/sh", str(installer), "--prepare-only", "--runtime-root", str(root)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if corrupt:
        assert result.returncode != 0
        assert not root.exists()
    else:
        assert result.returncode == 0, result.stderr
        cli = subprocess.run(
            [str(root / "bin" / "garmin-coach"), "--help"],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert cli.returncode == 0, cli.stderr
        assert "usage:" in cli.stdout
    assert not (home / "GarminCoach").exists()
    assert not (home / "Library" / "LaunchAgents").exists()


def test_bootstrap_imports_and_reports_help_without_site_packages(tmp_path):
    import os
    import subprocess
    import sys

    bootstrap = pathlib.Path(__file__).resolve().parents[1] / "src/garmin_coach/distribution.py"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(bootstrap), "--help"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--manifest" in result.stdout


def test_plain_wheel_build_excludes_private_source_skill_files(tmp_path):
    import os
    import shutil
    import subprocess

    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("building the package needs Poetry")
    source = pathlib.Path(__file__).resolve().parents[1]
    checkout = tmp_path / "source"
    checkout.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(source / name, checkout / name)
    for name in ("src", "skills"):
        shutil.copytree(
            source / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    private = checkout / "skills/coach/references/context.md"
    private.write_text("Synthetic private athlete marker")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(
        [poetry, "build", "--format", "wheel", "--output", str(tmp_path / "dist")],
        cwd=checkout,
        env=environment,
        check=True,
        capture_output=True,
        timeout=120,
    )
    (wheel,) = (tmp_path / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert "skills/coach/references/context.md" not in archive.namelist()
        assert {name for name in archive.namelist() if name.startswith("skills/")} == {
            "skills/coach/SKILL.md",
            "skills/coach/references/report.md",
            "skills/coach/references/planning.md",
            "skills/coach/references/authoring.md",
        }
