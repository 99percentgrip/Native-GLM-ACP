"""Hash-pinned declarative plugin packages; executable code is intentionally unsupported."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .config import config_dir

MAX_PLUGIN_FILES = 32
MAX_PLUGIN_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PUBLISHER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/-]{0,127}")
_PERMISSIONS = {"prompt_context", "policy_templates", "workflows"}
REQUIRE_SIGNED_ENV = "GLM_ACP_REQUIRE_SIGNED_PLUGINS"


class PluginError(RuntimeError):
    pass


def generate_signing_key(private_path: Path, public_path: Path, publisher: str) -> dict[str, str]:
    """Create one Ed25519 publisher keypair without overwriting existing files."""
    if not _PUBLISHER.fullmatch(publisher):
        raise PluginError("Publisher identity is invalid")
    if private_path.exists() or public_path.exists():
        raise PluginError("Signing key output already exists")
    private_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public_bytes).hexdigest()
    private_payload = {
        "schema": 1,
        "algorithm": "ed25519",
        "publisher": publisher,
        "key_id": key_id,
        "private_key": base64.b64encode(private_bytes).decode("ascii"),
    }
    public_payload = {
        "schema": 1,
        "algorithm": "ed25519",
        "publisher": publisher,
        "key_id": key_id,
        "public_key": base64.b64encode(public_bytes).decode("ascii"),
    }
    try:
        private_path.write_text(json.dumps(private_payload, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(private_path, 0o600)
        public_path.write_text(json.dumps(public_payload, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(public_path, 0o644)
    except OSError:
        private_path.unlink(missing_ok=True)
        public_path.unlink(missing_ok=True)
        raise
    return {"publisher": publisher, "key_id": key_id}


def sign_plugin_manifest(manifest_path: Path, private_path: Path) -> dict[str, str]:
    """Sign the canonical manifest payload; private key bytes never enter the manifest."""
    if manifest_path.is_symlink() or private_path.is_symlink():
        raise PluginError("Plugin manifest and signing key must not be symlinks")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        private_payload = json.loads(private_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PluginError(f"Could not read signing material: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(private_payload, dict):
        raise PluginError("Manifest and signing key must contain JSON objects")
    if private_payload.get("schema") != 1 or private_payload.get("algorithm") != "ed25519":
        raise PluginError("Signing key requires schema 1 Ed25519 format")
    try:
        private_bytes = base64.b64decode(str(private_payload["private_key"]), validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    except (KeyError, ValueError, TypeError) as error:
        raise PluginError("Signing private key is invalid") from error
    manifest.pop("signature", None)
    publisher = str(private_payload.get("publisher", ""))
    key_id = str(private_payload.get("key_id", ""))
    signature = private_key.sign(PluginRegistry._canonical_manifest(manifest))
    manifest["signature"] = {
        "algorithm": "ed25519",
        "publisher": publisher,
        "key_id": key_id,
        "value": base64.b64encode(signature).decode("ascii"),
    }
    temporary = manifest_path.with_name(f".{manifest_path.name}.signed.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"publisher": publisher, "key_id": key_id}


def read_public_key(path: Path) -> tuple[str, bytes]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        publisher = str(payload["publisher"])
        public_key = base64.b64decode(str(payload["public_key"]), validate=True)
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
        raise PluginError(f"Publisher public key is invalid: {error}") from error
    return publisher, public_key


class PluginRegistry:
    """Install and verify data-only packages containing prompts, policies, or workflows."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or config_dir() / "plugins"
        self.trust_path = self.base_dir.parent / "trusted-plugin-publishers.json"

    @staticmethod
    def _canonical_manifest(manifest: dict[str, Any]) -> bytes:
        payload = {key: value for key, value in manifest.items() if key != "signature"}
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _manifest(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginError(f"Invalid plugin manifest: {error}") from error
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise PluginError("Plugin manifest requires schema 1")
        plugin_id = str(payload.get("id", ""))
        if not _ID.fullmatch(plugin_id):
            raise PluginError(
                "Plugin id must use lowercase letters, digits, underscores, or dashes"
            )
        files = payload.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_PLUGIN_FILES:
            raise PluginError(f"Plugin files must contain 1-{MAX_PLUGIN_FILES} hash entries")
        permissions = payload.get("permissions", [])
        if not isinstance(permissions, list) or not all(
            isinstance(value, str) and value in _PERMISSIONS for value in permissions
        ):
            raise PluginError("Plugin permissions contain an unsupported capability")
        if payload.get("prompt_files") and "prompt_context" not in permissions:
            raise PluginError("prompt_files require the prompt_context permission")
        signature = payload.get("signature")
        if signature is not None:
            if not isinstance(signature, dict) or set(signature) != {
                "algorithm",
                "publisher",
                "key_id",
                "value",
            }:
                raise PluginError("Plugin signature has an invalid shape")
            if signature.get("algorithm") != "ed25519":
                raise PluginError("Only Ed25519 plugin signatures are supported")
            if not _PUBLISHER.fullmatch(str(signature.get("publisher", ""))):
                raise PluginError("Plugin signature publisher is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", str(signature.get("key_id", ""))):
                raise PluginError("Plugin signature key id is invalid")
        return payload

    def _trust_store(self) -> dict[str, Any]:
        if not self.trust_path.exists():
            return {"schema": 1, "publishers": {}}
        try:
            payload = json.loads(self.trust_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginError(f"Plugin publisher trust store is invalid: {error}") from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != 1
            or not isinstance(payload.get("publishers"), dict)
        ):
            raise PluginError("Plugin publisher trust store requires schema 1")
        return payload

    def _write_trust_store(self, payload: dict[str, Any]) -> None:
        self.trust_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.trust_path.with_name(f".{self.trust_path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.trust_path)
        finally:
            temporary.unlink(missing_ok=True)

    def trust_publisher(self, publisher: str, public_key: bytes) -> dict[str, str]:
        if not _PUBLISHER.fullmatch(publisher):
            raise PluginError("Publisher identity is invalid")
        if len(public_key) != 32:
            raise PluginError("Ed25519 public keys must contain exactly 32 raw bytes")
        try:
            Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError as error:
            raise PluginError("Ed25519 public key is invalid") from error
        key_id = hashlib.sha256(public_key).hexdigest()
        store = self._trust_store()
        store["publishers"][publisher] = {
            "algorithm": "ed25519",
            "key_id": key_id,
            "public_key": base64.b64encode(public_key).decode("ascii"),
        }
        self._write_trust_store(store)
        return {"publisher": publisher, "key_id": key_id}

    def untrust_publisher(self, publisher: str) -> bool:
        store = self._trust_store()
        removed = store["publishers"].pop(publisher, None) is not None
        if removed:
            self._write_trust_store(store)
        return removed

    def trusted_publishers(self) -> list[dict[str, str]]:
        store = self._trust_store()
        return [
            {"publisher": publisher, "key_id": str(value.get("key_id", ""))}
            for publisher, value in sorted(store["publishers"].items())
            if isinstance(value, dict)
        ]

    def _signature_status(self, manifest: dict[str, Any]) -> dict[str, Any]:
        signature = manifest.get("signature")
        require_signed = os.environ.get(REQUIRE_SIGNED_ENV, "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if signature is None:
            if require_signed:
                raise PluginError("Unsigned plugins are disabled by policy")
            return {"signed": False, "trust": "local-hash-only"}
        publisher = str(signature["publisher"])
        trusted = self._trust_store()["publishers"].get(publisher)
        if not isinstance(trusted, dict):
            raise PluginError(f"Plugin publisher is not trusted: {publisher}")
        if trusted.get("key_id") != signature.get("key_id"):
            raise PluginError("Plugin signing key does not match the trusted publisher key")
        try:
            public_bytes = base64.b64decode(str(trusted["public_key"]), validate=True)
            signature_bytes = base64.b64decode(str(signature["value"]), validate=True)
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                signature_bytes, self._canonical_manifest(manifest)
            )
        except (InvalidSignature, ValueError, TypeError, KeyError) as error:
            raise PluginError("Plugin signature verification failed") from error
        return {
            "signed": True,
            "trust": "trusted-publisher",
            "publisher": publisher,
            "key_id": str(signature["key_id"]),
        }

    @staticmethod
    def _verified_files(root: Path, manifest: dict[str, Any]) -> list[Path]:
        files: list[Path] = []
        total = 0
        for relative, expected in manifest["files"].items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise PluginError("Plugin file hashes must be string pairs")
            path = (root / relative).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError as error:
                raise PluginError(f"Plugin file escapes package: {relative}") from error
            if path.is_symlink() or not path.is_file():
                raise PluginError(f"Plugin file missing or unsafe: {relative}")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_PLUGIN_BYTES:
                raise PluginError("Plugin package exceeds 2 MiB")
            if hashlib.sha256(data).hexdigest() != expected.lower():
                raise PluginError(f"Plugin hash mismatch: {relative}")
            if path.suffix not in {".json", ".md", ".toml", ".yaml", ".yml"}:
                raise PluginError(f"Executable plugin content is not supported: {relative}")
            files.append(path)
        return files

    def install(self, manifest_path: Path) -> dict[str, Any]:
        manifest_path = manifest_path.resolve()
        manifest = self._manifest(manifest_path)
        signature = self._signature_status(manifest)
        source = manifest_path.parent
        files = self._verified_files(source, manifest)
        plugin_id = manifest["id"]
        target = self.base_dir / plugin_id
        temporary = self.base_dir / f".{plugin_id}.tmp"
        backup = self.base_dir / f".{plugin_id}.backup"
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.base_dir, 0o700)
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            for path in files:
                relative = path.relative_to(source)
                destination = temporary / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
                if os.name != "nt":
                    os.chmod(destination, 0o600)
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            (temporary / "plugin.json").write_bytes(manifest_bytes)
            (temporary / "manifest.sha256").write_text(
                hashlib.sha256(manifest_bytes).hexdigest() + "\n", encoding="ascii"
            )
            if os.name != "nt":
                os.chmod(temporary / "plugin.json", 0o600)
                os.chmod(temporary / "manifest.sha256", 0o600)
            if target.exists():
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(target, backup)
                try:
                    os.replace(temporary, target)
                except OSError:
                    os.replace(backup, target)
                    raise
                else:
                    shutil.rmtree(backup)
            else:
                os.replace(temporary, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return {
            "id": plugin_id,
            "version": manifest.get("version", "0"),
            "files": len(files),
            **signature,
        }

    def verify(self, plugin_id: str) -> dict[str, Any]:
        if not _ID.fullmatch(plugin_id):
            raise PluginError("Invalid plugin id")
        root = self.base_dir / plugin_id
        try:
            expected_manifest = (root / "manifest.sha256").read_text(encoding="ascii").strip()
            manifest_bytes = (root / "plugin.json").read_bytes()
        except OSError as error:
            raise PluginError(f"Plugin manifest pin is unavailable: {error}") from error
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest:
            raise PluginError("Plugin manifest hash mismatch")
        manifest = self._manifest(root / "plugin.json")
        if manifest.get("id") != plugin_id:
            raise PluginError("Plugin directory and manifest id differ")
        files = self._verified_files(root, manifest)
        signature = self._signature_status(manifest)
        return {
            "id": plugin_id,
            "version": manifest.get("version", "0"),
            "verified": True,
            "files": len(files),
            **signature,
        }

    def list(self) -> list[dict[str, Any]]:
        results = []
        if not self.base_dir.exists():
            return results
        for path in sorted(self.base_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                results.append(self.verify(path.name))
            except PluginError as error:
                results.append({"id": path.name, "verified": False, "error": str(error)})
        return results[:100]

    def prompt_fragments(self) -> str:
        """Load only verified, explicitly declared Markdown prompt fragments."""
        fragments: list[str] = []
        for item in self.list():
            if not item.get("verified"):
                continue
            root = self.base_dir / str(item["id"])
            manifest = self._manifest(root / "plugin.json")
            prompt_files = manifest.get("prompt_files", [])
            if not isinstance(prompt_files, list) or "prompt_context" not in manifest.get(
                "permissions", []
            ):
                continue
            verified = {
                path.relative_to(root).as_posix() for path in self._verified_files(root, manifest)
            }
            for relative in prompt_files[:8]:
                if relative in verified and str(relative).endswith(".md"):
                    fragments.append((root / str(relative)).read_text(encoding="utf-8")[:8_000])
        return "\n\n".join(fragments)[:32_000]
