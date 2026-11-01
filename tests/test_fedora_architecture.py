#!/usr/bin/env python3
"""Regression tests for the architecture-aware Fedora image resolver."""

from __future__ import annotations

import shlex
import subprocess
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY / "scripts" / "fedora-architecture.sh"


def resolve(architecture: str, image: str, tag: str = "latest") -> str:
    command = "; ".join(
        (
            f"source {shlex.quote(str(HELPER))}",
            f"fedora_detect_arch {shlex.quote(architecture)}",
            f"fedora_registry_image {shlex.quote(image)} {shlex.quote(tag)}",
        )
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


class FedoraArchitectureTests(unittest.TestCase):
    def test_amd64_keeps_the_compatibility_name(self) -> None:
        self.assertEqual(
            resolve("x86_64", "fedora45-ai-base"),
            "ghcr.io/safrano9999/fedora45-ai-base:latest",
        )

    def test_arm64_uses_the_canonical_alias(self) -> None:
        self.assertEqual(
            resolve("aarch64", "fedora45-ai-safrano9999", "2026.9.17.6"),
            "ghcr.io/safrano9999/fedora45-ai-arm64-safrano9999:2026.9.17.6",
        )

    def test_arm64_full_uses_its_existing_alias(self) -> None:
        self.assertEqual(
            resolve("arm64", "fedora45-ai-safrano9999-full"),
            "ghcr.io/safrano9999/fedora45-ai-arm64-full:latest",
        )

    def test_multi_keeps_the_manifest_name(self) -> None:
        self.assertEqual(
            resolve("multi", "fedora45-ai-core"),
            "ghcr.io/safrano9999/fedora45-ai-core:latest",
        )


if __name__ == "__main__":
    unittest.main()
