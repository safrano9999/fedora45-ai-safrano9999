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

## Sources

- Workflow SOT: [SCRIPTS/githubactions/fedora45-ai-safrano9999](https://github.com/safrano9999/SCRIPTS/tree/main/githubactions/fedora45-ai-safrano9999).
- [openclaw-deterministic-latest](https://github.com/safrano9999/openclaw-deterministic-latest).
- [openclaw-ephemeral](https://github.com/safrano9999/openclaw-ephemeral).
- [hermes-ephemeral](https://github.com/safrano9999/hermes-ephemeral).
- Additional package pins: [build.conf](fedora45-ai-core-pre/build.conf).
- Foundation build: [Containerfile](fedora45-ai-core-pre/Containerfile).
- Core integration: [Containerfile](fedora45-ai-core/Containerfile).

Core-pre's `OPENCLAW_VERSION` and optional `OPENCLAW_UPSTREAM_SHA` arguments
select the source for Deterministic builds and Ephemeral compatibility tests.
The override is preserved while the stable version is unchanged. An upstream
version increase clears it, returning to that release's official source.
`openclaw-components.yml` automatically checks Core-pre changes and every 30 minutes
checks for Deterministic changes. It builds/tests/publishes only when the selected
inputs differ from an existing successful release, then records the exact release
and checksum in Core. It uses `GH_RELEASE_TOKEN`; it never starts an image build.

Core installs Deterministic and Ephemeral in two separate steps. Deterministic
installs the complete matching OpenClaw npm package and Codex plugin, including
their dependencies and exports. Ephemeral supplies configuration and startup code
and discovers optional local models even without API keys. The prepared inputs
in `fedora45-ai-core/build.conf` are checked against Core-pre's source selection.
The old prepared snapshot is retained as a test fixture; a fresh snapshot must
be prepared once the new runtime bundle has been built and published.

| Fixed component | Previous | New stable pin |
|---|---|---|
| OpenClaw | 2026.9.3 | 2026.9.4 |
| Hermes | 0.21.0 | 0.21.2 |
| Electrum | 4.7.2 | 4.8.1 |
| LND / lncli | v0.20.1-beta | v0.21.3-beta |
| Geth | 1.17.2 | 1.17.5 |
| Webhook | 2.8.3 | 2.8.3 |
| Vditor | 3.11.2 | 4.0.0 |
| BIP39 | 0.5.6 | 0.5.6 |

The Brave and Codex plugins follow the pinned OpenClaw version. Node 26 comes
from Fedora RPMs without a patch-version pin. npm is updated before installing
Hermes so its installer keeps the system Node. Python development headers support
source builds on Fedora's Python 3.15; Psycopg uses Fedora's libpq. Other floating
channels (Codex CLI, Claude and uv) keep their previous selection strategy.

The Nous API-key patch follows Hermes 0.21.2's credential owners and preserves
interactive OAuth. Its 16 API-key regressions and 169 related upstream checks
pass, including the default chat transport and optional native Messages transport.

## Publishing and deployment

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
