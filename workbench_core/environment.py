"""Qt-free environment diagnosis for the configured workbench and solver."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from workbench_core.schemas.common import CodeIdentity, SoftwareIdentity


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def doctor(
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    prefix = Path(solver_prefix).resolve()
    conda = Path(conda_executable).resolve() if conda_executable else _find_conda()
    command = [
        str(conda),
        "run",
        "--no-capture-output",
        "-p",
        str(prefix),
        "python",
    ]
    checks = [
        Check("project_root", root.is_dir(), str(root)),
        Check("runner", (root / "runner.py").is_file(), str(root / "runner.py")),
        Check("solver_prefix", prefix.is_dir(), str(prefix)),
        Check("conda", conda.is_file(), str(conda)),
    ]
    checks.extend(_scientific_file_checks(root))
    checks.extend(_filesystem_checks(root))
    probe = _solver_probe(command, root) if all(item.ok for item in checks[:4]) else {}
    checks.append(Check("solver_import", probe.get("returncode") == 0, probe.get("detail", "not run")))
    inventory = _inventory(conda, prefix, root) if probe.get("returncode") == 0 else []
    export = _environment_export(conda, prefix, root) if conda.is_file() and prefix.is_dir() else ""
    identity = {
        "solver_environment_path": str(prefix),
        "launch_command": command,
        "python_version": probe.get("python_version"),
        "reaktoro_version": probe.get("reaktoro_version"),
        "package_inventory": inventory,
        "package_inventory_sha256": _hash_json(inventory),
        "environment_export": export,
        "environment_export_sha256": hashlib.sha256(export.encode()).hexdigest() if export else None,
    }
    return {
        "ready": all(item.ok or not item.blocking for item in checks),
        "checks": [asdict(item) for item in checks],
        "solver_environment_identity": identity,
        "code_identity": code_identity(root),
        "platform": os.name,
    }


def workbench_doctor(project_root: str | Path) -> dict[str, Any]:
    """Verify the exact current workbench interpreter and GUI-only dependencies."""
    import importlib.metadata as metadata

    root = Path(project_root).resolve()
    required = {
        "PySide6": "PySide6",
        "pyqtgraph": "pyqtgraph",
        "ruamel.yaml": "ruamel.yaml",
        "pandas": "pandas",
        "pyarrow": "pyarrow",
        "pydantic": "pydantic",
        "Markdown": "markdown",
        "reportlab": "reportlab",
    }
    checks = [
        Check("project_root", root.is_dir(), str(root)),
        Check("workbench_entrypoint", (root / "workbench" / "__main__.py").is_file(), str(root / "workbench" / "__main__.py")),
    ]
    versions = {}
    for distribution, module in required.items():
        try:
            __import__(module)
            versions[distribution] = metadata.version(distribution).strip()
        except (ImportError, metadata.PackageNotFoundError) as error:
            checks.append(Check(f"dependency:{distribution}", False, str(error)))
        else:
            checks.append(Check(f"dependency:{distribution}", True, versions[distribution]))
    try:
        metadata.version("reaktoro")
    except metadata.PackageNotFoundError:
        checks.append(Check("environment_separation", True, "Reaktoro is absent from the workbench environment"))
    else:
        checks.append(Check("environment_separation", False, "Reaktoro is installed in the workbench environment"))
    checks.extend(_filesystem_checks(root))
    environment_file = root / "environment-workbench.yml"
    checks.append(Check("environment_spec", environment_file.is_file(), str(environment_file)))
    return {
        "ready": all(item.ok or not item.blocking for item in checks),
        "checks": [asdict(item) for item in checks],
        "workbench_environment_identity": {
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "launch_command": [sys.executable, "-m", "workbench"],
            "packages": versions,
            "environment_spec_sha256": _sha256(environment_file) if environment_file.is_file() else None,
        },
        "code_identity": code_identity(root),
        "platform": os.name,
    }


def code_identity(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    relevant = [root / "runner.py"]
    relevant.extend(sorted((root / "batch_runner").rglob("*.py")))
    relevant = [path for path in relevant if path.is_file() and "__pycache__" not in path.parts]
    files = {path.relative_to(root).as_posix(): _sha256(path) for path in relevant}
    git = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(git.stdout.strip()),
        "status_manifest": git.stdout.splitlines(),
        "relevant_files": files,
        "relevant_tree_sha256": _hash_json(files),
    }


def workbench_software_identity(project_root: str | Path) -> SoftwareIdentity:
    """Identify the Qt-free services and GUI code used for a derived artifact."""
    root = Path(project_root).resolve()
    relevant = []
    for directory in (root / "workbench_core", root / "workbench"):
        if directory.is_dir():
            relevant.extend(directory.rglob("*.py"))
    if (root / "workbench_cli.py").is_file():
        relevant.append(root / "workbench_cli.py")
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(relevant)
        if "__pycache__" not in path.parts
    }
    git = code_identity(root)
    return SoftwareIdentity(
        workbench_version="1.0",
        python_version=sys.version.split()[0],
        code_identity=CodeIdentity(
            commit=git.get("commit") or "unversioned",
            dirty=git["dirty"],
            relevant_source_sha256=_hash_json(files),
        ),
    )


def _solver_probe(command: list[str], root: Path) -> dict[str, Any]:
    code = (
        "import json,sys,reaktoro; "
        "print(json.dumps({'python_version':sys.version.split()[0],"
        "'reaktoro_version':reaktoro.__version__}))"
    )
    completed = subprocess.run(
        [*command, "-c", code], cwd=root, capture_output=True, text=True, check=False, timeout=60
    )
    try:
        values = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        values = {}
    return {
        "returncode": completed.returncode,
        "detail": (completed.stderr or completed.stdout).strip() or "solver import passed",
        **values,
    }


def _inventory(conda: Path, prefix: Path, root: Path) -> list[dict[str, str]]:
    completed = subprocess.run(
        [str(conda), "list", "-p", str(prefix), "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return sorted(json.loads(completed.stdout), key=lambda item: item["name"].lower())


def _environment_export(conda: Path, prefix: Path, root: Path) -> str:
    completed = subprocess.run(
        [str(conda), "env", "export", "-p", str(prefix)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return completed.stdout if completed.returncode == 0 else ""


def _scientific_file_checks(root: Path) -> list[Check]:
    paths = [
        root / "data" / "thermo" / "Kinec_v3_4.dat",
        root / "data" / "kinetics" / "PalandriKharaka_local.yaml",
        root / "data" / "kinetics" / "kinec_rates_minimal.yaml",
        root / "batch_runner" / "simulator" / "kinetics" / "kinec.py",
    ]
    return [Check(f"scientific_file:{path.name}", path.is_file(), str(path)) for path in paths]


def _filesystem_checks(root: Path) -> list[Check]:
    free = shutil.disk_usage(root).free
    atomic_ok = False
    detail = ""
    try:
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            source = Path(temporary) / "source.tmp"
            target = Path(temporary) / "target.tmp"
            source.write_text("ok", encoding="utf-8")
            os.replace(source, target)
            atomic_ok = target.read_text(encoding="utf-8") == "ok"
    except OSError as error:
        detail = str(error)
    return [
        Check("filesystem_atomic_replace", atomic_ok, detail or "same-directory os.replace passed"),
        Check("disk_space", free > 0, f"free_bytes={free}", blocking=False),
        Check("windows_process_tree", os.name == "nt", "taskkill /T /F available" if os.name == "nt" else "not Windows", blocking=False),
    ]


def _find_conda() -> Path:
    value = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if not value:
        raise FileNotFoundError("Conda executable was not found")
    return Path(value).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
