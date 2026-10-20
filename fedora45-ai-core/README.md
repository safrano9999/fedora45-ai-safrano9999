# fedora45-ai-core

`ghcr.io/safrano9999/fedora45-ai-core` is the small, frequently changed
ephemeral layer in the Fedora image chain:

```text
fedora45-ai-core-pre -> fedora45-ai-core -> fedora45-ai-base -> fedora45-ai-kachelmann -> fedora45-ai-safrano9999
```

The heavy generic Fedora 45 packages, toolchains, OpenClaw/Hermes installations,
and third-party command-line tools live in `fedora45-ai-core-pre`. Core imports
the pinned deterministic OpenClaw distribution. Its Containerfile obtains
`openclaw-ephemeral`, `hermes-ephemeral`, `opencode-ephemeral`, and `litellm-free`
directly from their current Git default branches, and NOTE from its `latest`
release. BuildKit handles Git fetching; no source snapshot or prepared commit
list overrides these sources. Release checksums are verified before installation.
The image retains no generated OpenClaw configuration; configuration is rebuilt from
injected environment variables on each container start. The private
`persistainer` runtime inherited from Core-pre projects all declared persistent
paths before the three ephemeral configurators and their gateways start.

Optional repeatable `MCP_SERVER_NAME`, `MCP_SERVER_URL`, and
`MCP_SERVER_BEARER` groups are projected into the global agent configs at
startup by their respective ephemeral generators: OpenClaw owns its JSON
projection, Hermes owns its YAML projection, and OpenCode owns its global
`opencode.json` MCP projection. The first group is suffixless;
subsequent `config.sh` entries use
`_02`, `_03`, and so on. A missing name is derived from the URL hostname and a
missing Bearer configures an unauthenticated endpoint. Bearer values remain in
the injected environment; generated JSON/YAML stores only `${...}` references.
All tools are exposed to all agents, parallel tool calls are enabled, and the
OpenClaw Codex projection defaults MCP tool approvals to `approve`.

Core intentionally contains no checked-out Safrano project repository. Base
and Safrano add their disjoint repository sets in later layers.

## Runtime ownership and order

Systemd units remain with the package that owns their runtime:

- Core-pre/CONTAINER owns BIP39, Cloudflared, Cockpit, Tailscaled and
  `tailscale-up`; private `persistainer` contributes `persistainer.service`.
- Core/CONTAINER owns the optional init-hook runner, Vditor, the OpenClaw,
  OpenCode, and Hermes gateways, and the Hermes dashboard. `openclaw-ephemeral` owns OpenClaw configuration and
  scheduling; private `hermes-ephemeral` owns Hermes configuration; and
  `opencode-ephemeral` owns the OpenCode MCP configuration oneshot.
- Base and later layers install application units directly from each selected
  repository's `image/runtime` overlay.

The cumulative Fedora images use this graph:

```text
network-online
├── direct stateless listeners
└── persistainer.service
    ├── tailscaled.service -> tailscale-up.service
    ├── persistent application listeners
    ├── openclaw-config.service -> optional init hooks -> optional VikAI bootstrap -> openclaw.service
    ├── opencode-config.service -> opencode.service
    └── hermes-config.service -> optional init hooks -> hermes.service -> hermes-dashboard.service

configured Nextcloud accounts
├── boot -> nextcloud-sync@N.service
└── nextcloud-sync@N.timer -> the same nextcloud-sync@N.service

parallel final jobs
├── openclaw-ephemeral-schedule.service
└── citadel-scan.service
```

Final jobs use optional `After=` edges for application units: missing units are
ignored, disabled units are not started by those edges, and condition-skipped
units are already complete. Listener units expose readiness through the common
bounded `fedora45-wait-ready` helper, so the final jobs need no fixed sleep.

Build and publish through the GitHub Actions cascade maintained in
`SCRIPTS/githubactions/fedora45-ai-safrano9999`. Private Git sources and release
downloads use BuildKit secrets; credentials are not stored in image layers.
Image pulls on the host are handled exclusively by Smart1.
