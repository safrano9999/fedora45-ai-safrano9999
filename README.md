# Fedora AI Safrano9999

This repository contains one Fedora45 build chain. The current source directories
and image names have no channel suffix:

```text
fedora45-ai-core-pre → fedora45-ai-core → fedora45-ai-base
  → fedora45-ai-kachelmann → fedora45-ai-safrano9999
  → fedora45-ai-safrano9999-full (optional, adds VikAI)
```

- Each published image receives a fixed `YYYY.M.N` tag, for example `2026.9.1`.
- `N` advances within the UTC month across the chain; existing version tags are never overwritten.
- `latest` always points to the most recently published build.
- `stable` moves only when explicitly selected, including the documented pre-update baseline step.
- A cascade passes its fixed version to every dependent build and uses that version for its parent.

The previous source directories and workflows are preserved in
[MUSEUM](https://github.com/safrano9999/MUSEUM/tree/main/CONTAINER).
Existing deployed image references and historical baseline manifests retain their
original names until the corresponding deployment is explicitly migrated.

## Layout and setup

Clone this repository directly to `~/safrano9999/fedora45-ai-safrano9999`.
Run `fedora45-ai-safrano9999/setup.sh` for Safrano without VikAI, or use
`fedora45-ai-safrano9999-full/setup.sh` for the optional Full extension.
KACHELMANN is inherited by both. The shared `config.sh` remains the setup source
of truth; layer setup defaults to the matching GHCR image with the `latest` tag.

The setup scripts are architecture-aware and use the host architecture by
default. On x86_64 they select the regular image names; on aarch64/ARM64 they
select the `fedora45-ai-arm64-*` aliases. Override explicitly when preparing a
deployment for another host:

```bash
./setup.sh --arch arm64 --pull
./setup.sh --arch amd64 --config-only
```

The common resolver lives in `scripts/fedora-architecture.sh`; all layer
wrappers use the shared `scripts/layer-setup.sh`. After both architecture
cascades publish the same version, dispatch
`.github/workflows/fedora45-ai-multiarch-manifest.yml` to publish the generic
multi-architecture tags. `IMAGE_ARCH=multi` then selects those manifests
without duplicating setup logic.

## Sources

Core-pre runs the shared `tailscale-serve-reset.sh` helper after Tailscale up.
Only `TAILSCALE_SERVE_RESET=1` resets all Serve/Funnel routes; unset/empty/0
leaves them untouched. Set it in the instance configuration when rebuilding
routes after bootstrap is desired. It is independent of Citadel's scan flag
and does not run when only the Citadel WebUI restarts. Script and systemd
drop-in have their SOT in `SCRIPTS/safrano9999-lib/tailscale/`; dev-init restores
their hardlinks. The Citadel runtime orders its WebUI after Tailscale up.

- x86_64 workflow SOT: [SCRIPTS/githubactions/fedora45-ai-safrano9999](https://github.com/safrano9999/SCRIPTS/tree/main/githubactions/fedora45-ai-safrano9999).
- ARM64 workflow SOT: [SCRIPTS/githubactions/fedora45-ai-safrano9999-arm64](https://github.com/safrano9999/SCRIPTS/tree/main/githubactions/fedora45-ai-safrano9999-arm64).
- [Official OpenClaw](https://github.com/openclaw/openclaw); [Deterministic](https://github.com/safrano9999/openclaw-deterministic-latest) is retained only for historical recipes.
- [openclaw-ephemeral](https://github.com/safrano9999/openclaw-ephemeral).
- [hermes-ephemeral](https://github.com/safrano9999/hermes-ephemeral).
- [opencode-ephemeral](https://github.com/safrano9999/opencode-ephemeral).
- Additional package pins: [build.conf](fedora45-ai-core-pre/build.conf).
- Foundation build: [Containerfile](fedora45-ai-core-pre/Containerfile).
- Core integration: [Containerfile](fedora45-ai-core/Containerfile).

The normal line targets **2026.11.1**. Core-pre installs the official
OpenClaw **2026.11.1** npm release once, verified against its release manifest.
Core inherits that installation and independently installs matching Brave and
Codex plugins, MAI Transcribe 0.1.1, Speaches, and the Ephemeral adapters.
The existing TTS fallback-order integration is retained separately.
Deterministic and NOTE are no longer inputs to this line; the historical
`Containerfile2026.9.17` recipes remain available with their original pins.

Standard OpenClaw accepts private MCP connections to the explicitly configured
server origin. Ephemeral retains the per-server private-network option for older
runtimes. Its GitHub integration job verifies generated configuration, real model
catalog entries, agent filters, and a private MCP tool call with OpenClaw 2026.11.1.

Hermes continues on its existing `main` branch, captured at
`63f6852249f6ce79a6e84f7d64fd4486facce5d1` (0.23.0 + 774 commits).
Its upstream PM/uv/pnpm locks define the isolated application environment.
The profile-store symlink correction is retained. Node stays on the official
26 LTS archive, currently 26.13.0. All Safrano Git contributions follow their
existing default branches.

| Component | Selected version |
|---|---|
| OpenClaw / Brave / Codex plugin | 2026.11.1 |
| OpenCode | 1.19.5 |
| OpenCode Telegram bridge | 0.27.0 |
| Codex CLI | 0.167.0 |
| Claude Code | 2.1.319 |
| npm | 12.2.2 |
| Electrum (amd64 and arm64) | 4.8.4 |
| Agave / Solana | 4.3.2 |

The complete stable-channel audit, hashes and source ownership are recorded in
[`upgrade-loop/evidence/2026.11.1-refresh`](upgrade-loop/evidence/2026.11.1-refresh).
Core-pre refreshes Fedora 45 packages. Upper layers keep satisfied inherited
Python dependencies and install only missing requirements; the resolver sees
all version constraints and `pip check` verifies the result. No upper layer
reinstalls OpenClaw, Node, Hermes or OpenCode.

All image builds and runtime tests run on GitHub-hosted Actions runners through
workflows maintained in SCRIPTS/githubactions. Version tags are tested before
promoting the complete chain to `latest`. Publication does not pull images,
restart containers, or change `images.json` on any deployment.

## Publishing and deployment

Each layer provides `Containerfile` for the normal line and
`Containerfile2026.9.17` for the pinned OpenClaw/Fedora line. SAFRANO_MCP accepts
`build_series="2026.9.17"`, reserves one next revision (for example `.10` → `.11`),
and passes the same series, tag and matching parent tags through Core-pre → Full.
Historical recipes keep their existing Deterministic, OpenClaw and Fedora pins.
The normal line selects the official OpenClaw release and the foundation inputs
documented above; it does not request a Deterministic build.

The planned n8n workflow for repeatable builds, regression tests and corrections
across the entire Fedora45 image chain is described in
[Fedora45 Build and Test Loop](upgrade-loop/README.md).

Use scoped `fire.sh REPOSITORY...` publication and `fire-example-chain.sh 45`
for the single example chain. The build selector exposes the same six layers.
GitHub Actions assigns the next `YYYY.M.N` version when `image_tag` is empty;
an explicit `image_tag` must be an unused fixed version. Each build publishes
that fixed tag; `push_latest=true` also moves `latest` and is the normal default.
The upgrade loop uses `push_latest=false` until required tests pass.
`push_stable=true` additionally moves `stable`; it defaults to false. Both
publishing flags are passed through the cascade.

The canonical image packages are private. Use the existing Smart1 workflow for
pulls. Full remains optional; changes to deployed catalog entries, instance
paths, and Quadlets are separate deployment operations.

Before migrating an existing instance, retain its exact previous image digest
and a consistent copy of its persistent data. New OpenClaw may migrate SQLite
state; reverting only the image is not a complete data rollback.

OpenClaw 2026.9.2 requires an explicit `openclaw doctor --fix` migration for
older agent databases. Run it with writers stopped and a recoverable snapshot.
The new example adds `state/` and `session-sqlite-migration-runs/` to the existing
OPENCLAW named volume using the unchanged `#named-volume` mechanism. Doctor
moves ChatGPT credentials into shared state; migration receipts and the import
archives under the already-persisted complete agents tree must also survive
container replacement.
Keep image-owned plugin files outside that mount; refresh their registry with
the supported CLI, never overwrite the credential-bearing SQLite database.

For deployment, check the new image version, service readiness, Citadel and
the configured MCP connections. The Telegram-agent setup inherits
`OPENCLAW_TELEGRAM_HEARTBEAT_MINUTES=0` (disabled) from Ephemeral; positive
values enable an interval in minutes for that configured agent.

### Pinned 2026.9.17.12 runtime refresh

The complete six-stage cascade keeps OpenClaw **2026.9.4** and the approved
Fedora **45 Beta-1.3** base. Core-pre installs official Node.js **26.12.1**
directly from its SHA-256-pinned Linux x64 archive rather than DNF. Existing
`/usr/bin/node`, `npm` and `npx` paths remain available through links, and the
existing npm release pin remains in effect. Each subsequent series recipe
uses its parent at **2026.9.17.12**; build retries reuse this exact version.
GitHub Actions verifies the runtime and the absence of Fedora Node packages.

### Fedora 45 Stable refresh: 2026.9.17.28

All six layers share the `2026.9.17.28` version on native GitHub AMD64 and
ARM64 runners. The foundation uses the digest-pinned official Fedora 45 final
multiarch index. OpenClaw remains **2026.9.4**, including its deterministic
artifact. Node follows the 26 LTS line at **26.13.0**. The series Hermes source
follows `main` at `5b526a53189072c34349eb357803f4cc039505a7` and runs with
its own PM-locked Python/uv toolchain and application `.venv` under
`/usr/local/lib/hermes-runtime/hermes-agent`. The same directory contains the
independent `pm-runtime` required by dashboard chat and the prebuilt Node/TUI.
Dependencies follow upstream `uv.lock`; system services keep Fedora's Python.
GitHub runners open the real dashboard chat WebSocket with networking disabled.

Core-pre installs native Electrum, Agave/Solana, LND and Geth on both
architectures. Base selects the matching native Nextcloud 35.0.2 runtime.
Application requirements are upgraded within their declared version limits
under `/usr/local`. The build installer hides RPM-managed site-packages only
from pip, so Fedora packages remain intact and subsequent application upgrades
replace their own previous pip installations normally.
The dependency audit, channel decisions and SHA256 values are recorded under
[`upgrade-loop/evidence`](upgrade-loop/evidence/).

Publish the generic multiarch manifests after both complete cascades pass.
Local rollout stops affected services, completes Smart1, then starts those
services again. Image builds and runtime smoke tests run exclusively on
GitHub-hosted runners.
