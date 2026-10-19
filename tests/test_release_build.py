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
    checkout = _source_checkout(tmp_path)
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
            "skills/coach/references/saved-context.md",
        }
        assert "garmin_coach/coach_resources.json" in archive.namelist()


def test_release_bundle_excludes_even_tracked_private_skill_resources(
    tmp_path, release_builder, monkeypatch
):
    import json
    import shutil
    import subprocess
    import tarfile

    source = pathlib.Path(__file__).resolve().parents[1]
    checkout = tmp_path / "source"
    public = {
        "SKILL.md",
        "references/report.md",
        "references/planning.md",
        "references/authoring.md",
        "references/saved-context.md",
    }
    for name in public:
        target = checkout / "skills/coach" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "skills/coach" / name, target)
    private = checkout / "skills/coach/references/context.md"
    private.write_text("Synthetic private athlete facts")
    for name in ("distribution.py", "coach_resources.json"):
        target = checkout / "src/garmin_coach" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "src/garmin_coach" / name, target)
    shutil.copyfile(source / "pyproject.toml", checkout / "pyproject.toml")
    (checkout / "scripts").mkdir()
    shutil.copyfile(source / "scripts/install.sh.in", checkout / "scripts/install.sh.in")
    tracked = [
        *[f"skills/coach/{name}" for name in public],
        "skills/coach/references/context.md",
    ]

    def run(command, **kwargs):
        if command[:2] == ["poetry", "build"]:
            output = pathlib.Path(command[-1])
            output.mkdir()
            with zipfile.ZipFile(output / "garmin_coach-0.1.1-py3-none-any.whl", "w") as archive:
                archive.writestr("garmin_coach-0.1.1.dist-info/METADATA", "Name: garmin-coach")
        elif command[:2] == ["poetry", "export"]:
            pathlib.Path(command[-1]).write_text("")
        else:
            assert command[1:4] == ["-m", "pip", "download"]
        return subprocess.CompletedProcess(command, 0)

    def check_output(command, **kwargs):
        if command[:2] == ["git", "rev-parse"]:
            return "synthetic-source-commit\n"
        if "-z" in command:
            return "\0".join(tracked).encode()
        return "\n".join(tracked)

    monkeypatch.setattr(release_builder, "ROOT", checkout)
    monkeypatch.setattr(release_builder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(release_builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(release_builder.subprocess, "run", run)
    monkeypatch.setattr(release_builder.subprocess, "check_output", check_output)
    output = tmp_path / "release"

    release_builder.build(output, "v0.1.1", "https://example.invalid/v0.1.1")

    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        resources = {name for name in archive.getnames() if name.startswith("skills/")}
        assert {name for name in resources if name.endswith(".md")} == {
            f"skills/coach/{name}" for name in public
        }
    assert json.loads(next(output.glob("manifest-*.json")).read_text())["version"] == "0.1.1"


def test_wheel_audit_rejects_private_skill_even_if_tracked(tmp_path, release_builder, monkeypatch):
    name = "skills/coach/references/context.md"
    wheel = tmp_path / "release.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(name, "Synthetic private athlete facts")
    monkeypatch.setattr(
        release_builder.subprocess, "check_output", lambda *args, **kwargs: name.encode()
    )
    with pytest.raises(ValueError, match="Unapproved|unapproved"):
        release_builder.audit_wheel(wheel)


def _source_checkout(tmp_path):
    import json
    import shutil

    source = pathlib.Path(__file__).resolve().parents[1]
    checkout = tmp_path / "source"
    checkout.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(source / name, checkout / name)
    shutil.copytree(source / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
    public = json.loads((source / "src/garmin_coach/coach_resources.json").read_text())
    for name in public:
        target = checkout / "skills/coach" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "skills/coach" / name, target)
    return checkout


def test_release_check_accepts_this_checkout(release_builder):
    """The explicit wheel and sdist includes in pyproject.toml match the public manifest."""
    release_builder.check_public_resources(pathlib.Path(__file__).resolve().parents[1])


@pytest.mark.parametrize("format", ["wheel", "sdist"])
def test_release_check_refuses_a_private_include_for_either_format(
    tmp_path, release_builder, format
):
    checkout = _source_checkout(tmp_path)
    config = checkout / "pyproject.toml"
    config.write_text(
        config.read_text().replace(
            "include = [\n",
            'include = [\n    { path = "skills/coach/references/private.md", '
            f'format = ["{format}"] }},\n',
            1,
        )
    )
    with pytest.raises(ValueError, match=f"{format} includes must match"):
        release_builder.check_public_resources(checkout)


def test_release_wheel_build_refuses_a_symlinked_public_resource_before_poetry_runs(
    tmp_path, release_builder, monkeypatch
):
    checkout = _source_checkout(tmp_path)
    reference = checkout / "skills/coach/references/report.md"
    private = reference.with_name("private.md")
    private.write_text("Synthetic private athlete facts")
    reference.unlink()
    reference.symlink_to(private.name)
    commands = []
    monkeypatch.setattr(release_builder, "ROOT", checkout)
    monkeypatch.setattr(
        release_builder.subprocess, "run", lambda command, **_: commands.append(command)
    )
    with pytest.raises(ValueError, match="Symlinked public coach resource"):
        release_builder._build_wheel(tmp_path / "staging")
    assert commands == []
