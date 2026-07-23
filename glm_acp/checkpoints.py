"""Deduplicated workspace checkpoints with conflict-aware rollback.

Checkpoint payloads are stored as compressed loose Git blob objects in a private
shadow object database.  Manifests are small JSON files, so identical content is
written once even across projects and checkpoint generations.  The store never
touches a workspace's own Git repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .config import config_dir

DEFAULT_CHECKPOINT_MAX_FILES = 20_000
DEFAULT_CHECKPOINT_MAX_MIB = 250
HARD_CHECKPOINT_MAX_FILES = 1_000_000
HARD_CHECKPOINT_MAX_MIB = 10_240
CHECKPOINT_MAX_FILES_ENV = "GLM_ACP_CHECKPOINT_MAX_FILES"
CHECKPOINT_MAX_MIB_ENV = "GLM_ACP_CHECKPOINT_MAX_MIB"
CHECKPOINT_LIMITS_FILENAME = "checkpoint-limits.json"
DEFAULT_AUTO_CHECKPOINT = False
AUTO_CHECKPOINT_FILENAME = "checkpoint-auto.json"
AUTO_CHECKPOINT_ENV = "GLM_ACP_AUTO_CHECKPOINT"

DEFAULT_STORE_MAX_MIB = 1024
DEFAULT_PROJECT_HISTORY = 10
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_FILE_MIB = 25
HARD_STORE_MAX_MIB = 10_240
HARD_PROJECT_HISTORY = 100
HARD_MAX_AGE_DAYS = 365
HARD_MAX_FILE_MIB = 1024
CHECKPOINT_STORAGE_FILENAME = "checkpoint-storage.json"
STORE_MAX_MIB_ENV = "GLM_ACP_CHECKPOINT_STORE_MAX_MIB"
PROJECT_HISTORY_ENV = "GLM_ACP_CHECKPOINT_HISTORY"
MAX_AGE_DAYS_ENV = "GLM_ACP_CHECKPOINT_MAX_AGE_DAYS"
MAX_FILE_MIB_ENV = "GLM_ACP_CHECKPOINT_MAX_FILE_MIB"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_IGNORED = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
_SENSITIVE_NAMES = {".env", "credentials.json", "id_rsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def _sensitive(path: Path) -> bool:
    return (
        ".ssh" in path.parts
        or path.name in _SENSITIVE_NAMES
        or path.name.startswith(".env.")
        or path.suffix.lower() in _SENSITIVE_SUFFIXES
    )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


class CheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckpointLimits:
    max_files: int
    max_mib: int
    files_source: str = "default"
    mib_source: str = "default"

    @property
    def max_bytes(self) -> int:
        return self.max_mib * 1024 * 1024


@dataclass(frozen=True)
class CheckpointStoragePolicy:
    store_max_mib: int = DEFAULT_STORE_MAX_MIB
    project_history: int = DEFAULT_PROJECT_HISTORY
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    max_file_mib: int = DEFAULT_MAX_FILE_MIB
    source: str = "default"

    @property
    def store_max_bytes(self) -> int:
        return self.store_max_mib * 1024 * 1024

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mib * 1024 * 1024


@dataclass(frozen=True)
class AutoCheckpointState:
    enabled: bool
    source: str = "default"


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
    raise CheckpointError(
        "Auto-checkpoint value must be one of: " + ", ".join(sorted(_TRUTHY | _FALSY))
    )


def _limit(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise CheckpointError(f"{name} must be a whole number from 1 to {maximum}")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as error:
        raise CheckpointError(f"{name} must be a whole number from 1 to {maximum}") from error
    if str(value).strip() != str(parsed) or not 1 <= parsed <= maximum:
        raise CheckpointError(f"{name} must be a whole number from 1 to {maximum}")
    return parsed


class CheckpointManager:
    """Private content-addressed checkpoint store; never mutates workspace Git state."""

    def __init__(
        self,
        base_dir: Path | None = None,
        limits_path: Path | None = None,
        auto_path: Path | None = None,
        storage_path: Path | None = None,
    ) -> None:
        self.base_dir = base_dir or config_dir() / "checkpoints"
        self.limits_path = limits_path or config_dir() / CHECKPOINT_LIMITS_FILENAME
        self.auto_path = auto_path or config_dir() / AUTO_CHECKPOINT_FILENAME
        self.storage_path = storage_path or config_dir() / CHECKPOINT_STORAGE_FILENAME

    @property
    def objects_dir(self) -> Path:
        return self.base_dir / "store" / "objects"

    @property
    def workspaces_dir(self) -> Path:
        return self.base_dir / "workspaces"

    def auto_checkpoint(self) -> AutoCheckpointState:
        if AUTO_CHECKPOINT_ENV in os.environ:
            try:
                enabled = _coerce_bool(os.environ[AUTO_CHECKPOINT_ENV])
            except CheckpointError:
                enabled = DEFAULT_AUTO_CHECKPOINT
            return AutoCheckpointState(enabled, "environment")
        try:
            payload = json.loads(self.auto_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AutoCheckpointState(DEFAULT_AUTO_CHECKPOINT, "default")
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"Cannot read auto-checkpoint state: {error}") from error
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise CheckpointError("Auto-checkpoint file must be a schema-1 JSON object")
        try:
            enabled = _coerce_bool(payload.get("enabled", DEFAULT_AUTO_CHECKPOINT))
        except CheckpointError as error:
            raise CheckpointError(f"Invalid auto-checkpoint state: {error}") from error
        return AutoCheckpointState(enabled, "profile")

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def set_auto_checkpoint(self, enabled: object) -> AutoCheckpointState:
        self._atomic_json(self.auto_path, {"schema": 1, "enabled": _coerce_bool(enabled)})
        return self.auto_checkpoint()

    def reset_auto_checkpoint(self) -> AutoCheckpointState:
        self.auto_path.unlink(missing_ok=True)
        return self.auto_checkpoint()

    def limits(self) -> CheckpointLimits:
        max_files, max_mib = DEFAULT_CHECKPOINT_MAX_FILES, DEFAULT_CHECKPOINT_MAX_MIB
        files_source = mib_source = "default"
        try:
            payload = json.loads(self.limits_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = None
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"Cannot read checkpoint limits: {error}") from error
        if payload is not None:
            if not isinstance(payload, dict) or payload.get("schema") != 1:
                raise CheckpointError("Checkpoint limits file must be a schema-1 JSON object")
            max_files = _limit(
                payload.get("max_files"), "Checkpoint max files", HARD_CHECKPOINT_MAX_FILES
            )
            max_mib = _limit(payload.get("max_mib"), "Checkpoint max MiB", HARD_CHECKPOINT_MAX_MIB)
            files_source = mib_source = "profile"
        if CHECKPOINT_MAX_FILES_ENV in os.environ:
            max_files = _limit(
                os.environ[CHECKPOINT_MAX_FILES_ENV],
                CHECKPOINT_MAX_FILES_ENV,
                HARD_CHECKPOINT_MAX_FILES,
            )
            files_source = "environment"
        if CHECKPOINT_MAX_MIB_ENV in os.environ:
            max_mib = _limit(
                os.environ[CHECKPOINT_MAX_MIB_ENV], CHECKPOINT_MAX_MIB_ENV, HARD_CHECKPOINT_MAX_MIB
            )
            mib_source = "environment"
        return CheckpointLimits(max_files, max_mib, files_source, mib_source)

    def configure_limits(self, max_files: object, max_mib: object) -> CheckpointLimits:
        files = _limit(max_files, "Checkpoint max files", HARD_CHECKPOINT_MAX_FILES)
        mib = _limit(max_mib, "Checkpoint max MiB", HARD_CHECKPOINT_MAX_MIB)
        self._atomic_json(self.limits_path, {"schema": 1, "max_files": files, "max_mib": mib})
        return self.limits()

    def reset_limits(self) -> CheckpointLimits:
        self.limits_path.unlink(missing_ok=True)
        return self.limits()

    def storage_policy(self) -> CheckpointStoragePolicy:
        values = {
            "store_max_mib": DEFAULT_STORE_MAX_MIB,
            "project_history": DEFAULT_PROJECT_HISTORY,
            "max_age_days": DEFAULT_MAX_AGE_DAYS,
            "max_file_mib": DEFAULT_MAX_FILE_MIB,
        }
        source = "default"
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = None
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"Cannot read checkpoint storage policy: {error}") from error
        maxima = {
            "store_max_mib": HARD_STORE_MAX_MIB,
            "project_history": HARD_PROJECT_HISTORY,
            "max_age_days": HARD_MAX_AGE_DAYS,
            "max_file_mib": HARD_MAX_FILE_MIB,
        }
        if payload is not None:
            if not isinstance(payload, dict) or payload.get("schema") != 1:
                raise CheckpointError("Checkpoint storage file must be a schema-1 JSON object")
            for key, maximum in maxima.items():
                values[key] = _limit(payload.get(key), key.replace("_", " "), maximum)
            source = "profile"
        envs = {
            "store_max_mib": STORE_MAX_MIB_ENV,
            "project_history": PROJECT_HISTORY_ENV,
            "max_age_days": MAX_AGE_DAYS_ENV,
            "max_file_mib": MAX_FILE_MIB_ENV,
        }
        for key, env in envs.items():
            if env in os.environ:
                values[key] = _limit(os.environ[env], env, maxima[key])
                source = "environment"
        return CheckpointStoragePolicy(**values, source=source)

    def configure_storage(
        self,
        store_max_mib: object,
        project_history: object,
        max_age_days: object,
        max_file_mib: object,
    ) -> CheckpointStoragePolicy:
        payload = {
            "schema": 1,
            "store_max_mib": _limit(store_max_mib, "Store max MiB", HARD_STORE_MAX_MIB),
            "project_history": _limit(project_history, "Project history", HARD_PROJECT_HISTORY),
            "max_age_days": _limit(max_age_days, "Max age days", HARD_MAX_AGE_DAYS),
            "max_file_mib": _limit(max_file_mib, "Max file MiB", HARD_MAX_FILE_MIB),
        }
        self._atomic_json(self.storage_path, payload)
        return self.storage_policy()

    def reset_storage(self) -> CheckpointStoragePolicy:
        self.storage_path.unlink(missing_ok=True)
        return self.storage_policy()

    def _workspace_identity(self, root: Path) -> str:
        return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]

    @contextmanager
    def _store_lock(self):
        """Serialize object and manifest mutation across local agent processes."""
        lock = self.base_dir / ".store-lock"
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        deadline = time.monotonic() + 10
        while True:
            try:
                lock.mkdir(mode=0o700)
                (lock / "owner.json").write_text(
                    json.dumps({"pid": os.getpid(), "created": time.time()}), "utf-8"
                )
                break
            except FileExistsError:
                stale = False
                try:
                    owner = json.loads((lock / "owner.json").read_text("utf-8"))
                    pid = int(owner.get("pid", 0))
                    created = float(owner.get("created", 0))
                    if pid <= 0 or time.time() - created > 86_400:
                        stale = True
                    else:
                        os.kill(pid, 0)
                except FileNotFoundError:
                    try:
                        stale = time.time() - lock.stat().st_mtime > 60
                    except OSError:
                        stale = False
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    stale = True
                if stale:
                    shutil.rmtree(lock, ignore_errors=True)
                    continue
                if time.monotonic() >= deadline:
                    raise CheckpointError("Checkpoint store is busy in another process")
                time.sleep(0.05)
        try:
            yield
        finally:
            shutil.rmtree(lock, ignore_errors=True)

    def _workspace_dir(self, root: Path) -> Path:
        return self.workspaces_dir / self._workspace_identity(root)

    def _legacy_workspace_dir(self, root: Path) -> Path:
        return self.base_dir / self._workspace_identity(root)

    @staticmethod
    def _files(root: Path):
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name
                for name in dirs
                if name not in _IGNORED and not name.startswith(".glm-acp-images")
            ]
            current_path = Path(current)
            for name in files:
                path = current_path / name
                if not path.is_symlink() and path.is_file():
                    yield path

    def _write_blob(self, data: bytes) -> tuple[str, int]:
        oid = _git_oid(data)
        target = self.objects_dir / oid[:2] / oid[2:]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
            temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
            temporary.write_bytes(zlib.compress(raw, 6))
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return oid, target.stat().st_size

    def _read_blob(self, oid: str) -> bytes:
        if len(oid) != 40 or any(ch not in "0123456789abcdef" for ch in oid):
            raise CheckpointError("Checkpoint object id is malformed")
        try:
            raw = zlib.decompress((self.objects_dir / oid[:2] / oid[2:]).read_bytes())
            header, data = raw.split(b"\0", 1)
        except (OSError, ValueError, zlib.error) as error:
            raise CheckpointError(f"Checkpoint object is unavailable: {oid}") from error
        if header != b"blob " + str(len(data)).encode("ascii") or _git_oid(data) != oid:
            raise CheckpointError(f"Checkpoint object integrity failure: {oid}")
        return data

    def create(self, cwd: str, label: str = "automatic") -> dict[str, object]:
        with self._store_lock():
            try:
                return self._create(cwd, label)
            except Exception:
                self._gc_objects()
                raise

    def _create(self, cwd: str, label: str) -> dict[str, object]:
        root = Path(cwd).resolve()
        if not root.is_dir():
            raise CheckpointError(f"Checkpoint workspace does not exist: {root}")
        limits, policy = self.limits(), self.storage_policy()
        checkpoint_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        target = self._workspace_dir(root) / f"{checkpoint_id}.json"
        manifest: dict[str, object] = {
            "schema": 2,
            "id": checkpoint_id,
            "cwd": str(root),
            "label": label[:200],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
            "agent_hashes": {},
            "excluded_sensitive_paths": [],
            "excluded_large_paths": [],
        }
        total = count = 0
        for path in self._files(root):
            relative = path.relative_to(root).as_posix()
            if _sensitive(path):
                manifest["excluded_sensitive_paths"].append(relative)  # type: ignore[union-attr]
                continue
            try:
                size = path.stat().st_size
            except OSError as error:
                raise CheckpointError(f"Cannot inspect checkpoint file: {relative}") from error
            if size > policy.max_file_bytes:
                manifest["excluded_large_paths"].append(relative)  # type: ignore[union-attr]
                continue
            data = path.read_bytes()
            count, total = count + 1, total + len(data)
            if count > limits.max_files or total > limits.max_bytes:
                raise CheckpointError(
                    "Workspace checkpoint exceeds "
                    f"{limits.max_files} files or {limits.max_mib} MiB. "
                    f"Change it with `/checkpoint limits <files> <MiB>` or the "
                    f"{CHECKPOINT_MAX_FILES_ENV}/{CHECKPOINT_MAX_MIB_ENV} environment variables"
                )
            oid, _ = self._write_blob(data)
            manifest["files"][relative] = {  # type: ignore[index]
                "oid": oid,
                "sha256": _hash(data),
                "size": len(data),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        self._atomic_json(target, manifest)
        self._prune()
        if not target.exists():
            raise CheckpointError(
                "Checkpoint exceeds the global storage ceiling after pruning. "
                "Increase it with `/checkpoint storage <store-MiB> <history> "
                "<days> <max-file-MiB>`."
            )
        return {
            "id": checkpoint_id,
            "files": count,
            "bytes": total,
            "label": label[:200],
            "excluded_sensitive_paths": len(manifest["excluded_sensitive_paths"]),  # type: ignore[arg-type]
            "excluded_large_paths": len(manifest["excluded_large_paths"]),  # type: ignore[arg-type]
        }

    def _load_v2(self, cwd: str, checkpoint_id: str) -> tuple[Path, dict[str, object]]:
        root = Path(cwd).resolve()
        target = self._workspace_dir(root) / f"{checkpoint_id}.json"
        try:
            manifest = json.loads(target.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"Checkpoint not found: {checkpoint_id}") from error
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != 2
            or manifest.get("cwd") != str(root)
        ):
            raise CheckpointError("Checkpoint workspace does not match the current workspace")
        return target, manifest

    def _load(self, cwd: str, checkpoint_id: str) -> tuple[Path, dict[str, object]]:
        if not checkpoint_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in checkpoint_id
        ):
            raise CheckpointError("Invalid checkpoint id")
        root = Path(cwd).resolve()
        if (self._workspace_dir(root) / f"{checkpoint_id}.json").exists():
            return self._load_v2(cwd, checkpoint_id)
        target = self._legacy_workspace_dir(root) / checkpoint_id
        try:
            manifest = json.loads((target / "manifest.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"Checkpoint not found: {checkpoint_id}") from error
        if not isinstance(manifest, dict) or manifest.get("cwd") != str(root):
            raise CheckpointError("Checkpoint workspace does not match the current workspace")
        return target, manifest

    def _baseline_hash(self, entry: object) -> str | None:
        return entry.get("sha256") if isinstance(entry, dict) else str(entry)

    def note_changes(self, cwd: str, checkpoint_id: str, paths: list[str]) -> None:
        with self._store_lock():
            self._note_changes(cwd, checkpoint_id, paths)

    def _note_changes(self, cwd: str, checkpoint_id: str, paths: list[str]) -> None:
        target, manifest = self._load(cwd, checkpoint_id)
        root = Path(cwd).resolve()
        hashes = manifest.setdefault("agent_hashes", {})
        if not isinstance(hashes, dict):
            raise CheckpointError("Checkpoint manifest is malformed")
        for raw in paths[: self.limits().max_files]:
            path = Path(raw).resolve()
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as error:
                raise CheckpointError(
                    f"Changed path is outside checkpoint workspace: {path}"
                ) from error
            hashes[relative] = (
                _hash(path.read_bytes()) if path.is_file() and not path.is_symlink() else None
            )
        destination = target if manifest.get("schema") == 2 else target / "manifest.json"
        self._atomic_json(destination, manifest)

    def note_workspace_changes(self, cwd: str, checkpoint_id: str) -> list[str]:
        with self._store_lock():
            return self._note_workspace_changes(cwd, checkpoint_id)

    def _note_workspace_changes(self, cwd: str, checkpoint_id: str) -> list[str]:
        limits, policy = self.limits(), self.storage_policy()
        _, manifest = self._load(cwd, checkpoint_id)
        root = Path(cwd).resolve()
        baseline = manifest.get("files", {})
        if not isinstance(baseline, dict):
            raise CheckpointError("Checkpoint manifest is malformed")
        excluded = set(manifest.get("excluded_large_paths", [])) | set(
            manifest.get("excluded_sensitive_paths", [])
        )
        current: dict[str, str] = {}
        count = total = 0
        for path in self._files(root):
            relative = path.relative_to(root).as_posix()
            if (
                _sensitive(path)
                or relative in excluded
                or path.stat().st_size > policy.max_file_bytes
            ):
                continue
            data = path.read_bytes()
            count, total = count + 1, total + len(data)
            if count > limits.max_files or total > limits.max_bytes:
                raise CheckpointError(
                    "Workspace grew beyond checkpoint tracking limits "
                    f"({limits.max_files} files/{limits.max_mib} MiB)"
                )
            current[relative] = _hash(data)
        changed = sorted(
            relative
            for relative in set(baseline) | set(current)
            if self._baseline_hash(baseline.get(relative)) != current.get(relative)
        )
        self._note_changes(cwd, checkpoint_id, [str(root / relative) for relative in changed])
        return changed

    def list(self, cwd: str) -> list[dict[str, object]]:
        root = Path(cwd).resolve()
        results: list[dict[str, object]] = []
        directory = self._workspace_dir(root)
        if directory.exists():
            for path in sorted(directory.glob("*.json"), reverse=True):
                try:
                    payload = json.loads(path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                results.append({key: payload.get(key) for key in ("id", "label", "created_at")})
        legacy = self._legacy_workspace_dir(root)
        if legacy.exists():
            for path in sorted((p for p in legacy.iterdir() if p.is_dir()), reverse=True):
                try:
                    payload = json.loads((path / "manifest.json").read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                results.append({key: payload.get(key) for key in ("id", "label", "created_at")})
        return sorted(results, key=lambda item: str(item.get("created_at", "")), reverse=True)[:100]

    def rollback(self, cwd: str, checkpoint_id: str) -> dict[str, object]:
        with self._store_lock():
            return self._rollback(cwd, checkpoint_id)

    def _rollback(self, cwd: str, checkpoint_id: str) -> dict[str, object]:
        target, manifest = self._load(cwd, checkpoint_id)
        root = Path(cwd).resolve()
        baseline, agent_hashes = manifest.get("files", {}), manifest.get("agent_hashes", {})
        if not isinstance(baseline, dict) or not isinstance(agent_hashes, dict):
            raise CheckpointError("Checkpoint manifest is malformed")
        conflicts: list[str] = []
        for relative, expected in agent_hashes.items():
            path = root / str(relative)
            try:
                path.resolve().relative_to(root)
            except ValueError:
                conflicts.append(str(relative))
                continue
            if path.is_symlink():
                conflicts.append(str(relative))
                continue
            current = _hash(path.read_bytes()) if path.is_file() else None
            if current != expected:
                conflicts.append(str(relative))
        if conflicts:
            return {"rolled_back": False, "conflicts": conflicts[:100], "restored": []}
        operations: list[tuple[Path, str, bytes | None, bytes | None, int | None]] = []
        for relative in agent_hashes:
            path = root / str(relative)
            current = path.read_bytes() if path.is_file() else None
            entry = baseline.get(relative)
            baseline_data = None
            mode = None
            if entry is not None:
                if isinstance(entry, dict):
                    baseline_data = self._read_blob(str(entry.get("oid", "")))
                    if _hash(baseline_data) != entry.get("sha256"):
                        raise CheckpointError(f"Checkpoint content integrity failure: {relative}")
                    mode = int(entry.get("mode", 0o600))
                else:
                    baseline_data = (target / "files" / str(relative)).read_bytes()
                    if _hash(baseline_data) != entry:
                        raise CheckpointError(f"Checkpoint content integrity failure: {relative}")
            operations.append((path, str(relative), current, baseline_data, mode))
        restored: list[str] = []
        committed: list[tuple[Path, bytes | None]] = []
        try:
            for path, relative, current, baseline_data, mode in operations:
                if baseline_data is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(baseline_data)
                    if mode is not None and os.name != "nt":
                        os.chmod(path, mode)
                elif path.exists():
                    if path.is_dir() or path.is_symlink():
                        raise OSError(f"Refusing to delete non-file rollback target: {relative}")
                    path.unlink()
                committed.append((path, current))
                restored.append(relative)
        except OSError as error:
            recovery_errors: list[str] = []
            for path, previous in reversed(committed):
                try:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(previous)
                except OSError:
                    recovery_errors.append(str(path))
            detail = (
                f"; recovery failed for {', '.join(recovery_errors)}" if recovery_errors else ""
            )
            raise CheckpointError(
                f"Rollback commit failed and was recovered{detail}: {error}"
            ) from error
        return {"rolled_back": True, "conflicts": [], "restored": restored}

    def _manifest_paths(self) -> list[Path]:
        return list(self.workspaces_dir.glob("*/*.json")) if self.workspaces_dir.exists() else []

    def _disk_usage(self, path: Path) -> int:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    def _gc_objects(self) -> int:
        referenced: set[str] = set()
        for path in self._manifest_paths():
            try:
                payload = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            files = payload.get("files", {})
            if isinstance(files, dict):
                referenced.update(
                    str(item.get("oid"))
                    for item in files.values()
                    if isinstance(item, dict) and item.get("oid")
                )
        removed = 0
        if self.objects_dir.exists():
            for directory in self.objects_dir.iterdir():
                if not directory.is_dir() or len(directory.name) != 2:
                    continue
                for path in directory.iterdir():
                    oid = directory.name + path.name
                    if path.name.startswith(".") or oid not in referenced:
                        removed += path.stat().st_size
                        path.unlink(missing_ok=True)
                if not any(directory.iterdir()):
                    directory.rmdir()
        return removed

    def prune(self, cwd: str | None = None) -> dict[str, int]:
        with self._store_lock():
            return self._prune(cwd)

    def _prune(self, cwd: str | None = None) -> dict[str, int]:
        policy = self.storage_policy()
        removed = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=policy.max_age_days)
        groups: dict[Path, list[tuple[datetime, Path]]] = {}
        for path in self._manifest_paths():
            try:
                payload = json.loads(path.read_text("utf-8"))
                created = datetime.fromisoformat(str(payload["created_at"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                created = datetime.min.replace(tzinfo=timezone.utc)
            groups.setdefault(path.parent, []).append((created, path))
        wanted = self._workspace_dir(Path(cwd).resolve()) if cwd else None
        for parent, items in groups.items():
            if wanted is not None and parent != wanted:
                continue
            for index, (created, path) in enumerate(sorted(items, reverse=True)):
                if index >= policy.project_history or created < cutoff:
                    removed += path.stat().st_size
                    path.unlink(missing_ok=True)
        removed += self._gc_objects()
        while self._disk_usage(self.base_dir / "store") > policy.store_max_bytes:
            candidates = []
            for path in self._manifest_paths():
                try:
                    created = datetime.fromisoformat(
                        str(json.loads(path.read_text("utf-8"))["created_at"])
                    )
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    created = datetime.min.replace(tzinfo=timezone.utc)
                candidates.append((created, path))
            if not candidates:
                break
            _, oldest = min(candidates)
            removed += oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            removed += self._gc_objects()
        return {
            "removed_bytes": removed,
            "remaining_checkpoints": len(self._manifest_paths()),
            "store_bytes": self._disk_usage(self.base_dir / "store"),
        }

    def storage_status(self, cwd: str | None = None) -> dict[str, object]:
        policy = self.storage_policy()
        root = Path(cwd).resolve() if cwd else None
        legacy_dir = self._legacy_workspace_dir(root) if root else self.base_dir
        legacy_bytes = 0
        if root and legacy_dir.exists():
            legacy_bytes = self._disk_usage(legacy_dir)
        return {
            "format": "git-loose-object-v2",
            "store_bytes": self._disk_usage(self.base_dir / "store"),
            "manifests_bytes": self._disk_usage(self.workspaces_dir),
            "checkpoints": len(self._manifest_paths()),
            "workspace_checkpoints": len(self.list(str(root))) if root else None,
            "legacy_bytes": legacy_bytes,
            "policy": asdict(policy),
        }

    def clear(self, cwd: str | None = None, *, include_legacy: bool = False) -> dict[str, int]:
        with self._store_lock():
            return self._clear(cwd, include_legacy=include_legacy)

    def _clear(self, cwd: str | None = None, *, include_legacy: bool = False) -> dict[str, int]:
        target = self._workspace_dir(Path(cwd).resolve()) if cwd else self.workspaces_dir
        removed = self._disk_usage(target)
        shutil.rmtree(target, ignore_errors=True)
        if include_legacy:
            legacy = self._legacy_workspace_dir(Path(cwd).resolve()) if cwd else None
            if legacy and legacy.exists():
                removed += self._disk_usage(legacy)
                shutil.rmtree(legacy, ignore_errors=True)
        removed += self._gc_objects()
        return {"removed_bytes": removed, "remaining_checkpoints": len(self._manifest_paths())}

    def migrate_legacy(self, cwd: str) -> dict[str, int]:
        with self._store_lock():
            return self._migrate_legacy(cwd)

    def _migrate_legacy(self, cwd: str) -> dict[str, int]:
        root = Path(cwd).resolve()
        legacy = self._legacy_workspace_dir(root)
        migrated = removed = 0
        if not legacy.exists():
            return {"migrated": 0, "removed_bytes": 0}
        for directory in sorted(path for path in legacy.iterdir() if path.is_dir()):
            try:
                manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            files = manifest.get("files", {})
            if not isinstance(files, dict):
                continue
            converted: dict[str, object] = {}
            valid = True
            for relative, expected in files.items():
                relative_path = PurePosixPath(str(relative))
                if relative_path.is_absolute() or ".." in relative_path.parts:
                    valid = False
                    break
                try:
                    data = (directory / "files" / relative_path).read_bytes()
                except OSError:
                    valid = False
                    break
                if _hash(data) != expected:
                    valid = False
                    break
                oid, _ = self._write_blob(data)
                converted[relative] = {
                    "oid": oid,
                    "sha256": expected,
                    "size": len(data),
                    "mode": 0o600,
                }
            if not valid:
                continue
            manifest["schema"] = 2
            manifest["files"] = converted
            manifest.setdefault("excluded_large_paths", [])
            destination = self._workspace_dir(root) / f"{manifest['id']}.json"
            self._atomic_json(destination, manifest)
            check = json.loads(destination.read_text("utf-8"))
            if check.get("id") != manifest.get("id"):
                continue
            removed += self._disk_usage(directory)
            shutil.rmtree(directory)
            migrated += 1
        if legacy.exists() and not any(legacy.iterdir()):
            legacy.rmdir()
        self._prune(cwd)
        return {"migrated": migrated, "removed_bytes": removed}
