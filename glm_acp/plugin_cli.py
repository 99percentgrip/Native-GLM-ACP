"""CLI-only publisher key and plugin trust management."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .plugins import (
    PluginError,
    PluginRegistry,
    generate_signing_key,
    read_public_key,
    sign_plugin_manifest,
)


def add_plugin_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plugin", help="sign plugins and manage trusted publishers")
    actions = parser.add_subparsers(dest="plugin_action", required=True)
    keygen = actions.add_parser("keygen", help="create an Ed25519 publisher keypair")
    keygen.add_argument("--publisher", required=True)
    keygen.add_argument("--private-key", required=True, type=Path)
    keygen.add_argument("--public-key", required=True, type=Path)
    sign = actions.add_parser("sign", help="sign a plugin.json manifest")
    sign.add_argument("manifest", type=Path)
    sign.add_argument("--private-key", required=True, type=Path)
    trust = actions.add_parser("trust", help="trust an Ed25519 publisher public key")
    trust.add_argument("public_key", type=Path)
    untrust = actions.add_parser("untrust", help="remove one trusted publisher")
    untrust.add_argument("publisher")
    actions.add_parser("publishers", help="list trusted publisher identities")


def run_plugin_command(args: argparse.Namespace) -> int:
    try:
        if args.plugin_action == "keygen":
            value = generate_signing_key(args.private_key, args.public_key, args.publisher)
        elif args.plugin_action == "sign":
            value = sign_plugin_manifest(args.manifest, args.private_key)
        elif args.plugin_action == "trust":
            publisher, public_key = read_public_key(args.public_key)
            value = PluginRegistry().trust_publisher(publisher, public_key)
        elif args.plugin_action == "untrust":
            value = {
                "publisher": args.publisher,
                "removed": PluginRegistry().untrust_publisher(args.publisher),
            }
        elif args.plugin_action == "publishers":
            value = PluginRegistry().trusted_publishers()
        else:  # pragma: no cover - argparse constrains this
            raise PluginError("Unknown plugin command")
    except PluginError as error:
        print(f"Plugin command failed: {error}")
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0
