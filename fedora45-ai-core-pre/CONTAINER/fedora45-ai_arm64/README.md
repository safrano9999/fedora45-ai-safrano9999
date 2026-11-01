# Fedora 45 AI ARM64 Core-pre

This directory is the ARM64-only Core-pre recipe for the Fedora cascade.
It is built on a GitHub-hosted `ubuntu-24.04-arm` runner and published to
GHCR; no ARM layers or build artifacts are stored on the local host.

The recipe uses the official Fedora 45 ARM64 base, Node.js 26.12.1 ARM64,
the DIESDAS ARM64 Electrum/Agave release, and architecture-specific LND,
Geth, and cloudflared binaries. OpenClaw remains pinned to `2026.9.4`.
