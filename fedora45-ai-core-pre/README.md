# fedora45-ai-core-pre

`ghcr.io/safrano9999/fedora45-ai-core-pre` is the heavy, reusable first layer
of the Fedora 45 AI image chain:

```text
fedora45-ai-core-pre -> fedora45-ai-core -> fedora45-ai-base -> fedora45-ai-kachelmann -> fedora45-ai-safrano9999
```

It contains only the generic Fedora/toolchain payload: system packages,
Node/Python tooling, the unconfigured OpenClaw and Hermes installations,
Codex/Claude CLIs, OpenCode with its Telegram bridge, media/network utilities,
and the generic blockchain tools.
It contains no `/opt/safrano9999` project tree, NOTE release, ephemeral runtime
configuration, or project-specific Safrano services. Its `image/runtime/`
overlay owns the generic Tailscale, Cockpit, Cloudflare connector, and BIP39
systemd integration inherited by every higher layer. The authenticated build
preparation also stages the private `safrano9999/persistainer` `main` overlay.
Its one-shot persistence preparation runs before the optional Tailscale state
is restored. The generic readiness helper is installed here for higher-layer
service units.

OpenCode is pinned to `1.18.32` and `@grinev/opencode-telegram-bot` to `0.25.3`.
The image contains no credentials: `OPENCODE_API_KEY`,
`OPENCODE_TELEGRAMTOKEN`, and `OPENCODE_TELEGRAM_CHAT_ID` are runtime values.
When `OPENCODE_API_KEY` is present, the enabled systemd unit starts OpenCode
on `0.0.0.0:4096`; the Telegram bridge additionally requires its bot token and
chat/user ID. The default bridge model is `opencode/mimo-v2.6-flash-free`.

`fedora45-ai-core` adds the comparatively small and frequently changed layer:
the deterministic OpenClaw overlay, `openclaw-ephemeral`, NOTE, Hermes/OpenClaw
configuration generators, and their project-aware systemd runtime units.

Local build:

```bash
gh auth setup-git
./build/build-local.sh
```

The build fails if the private `persistainer` source cannot be fetched or its
runtime overlay is incomplete; a previously staged payload is never reused.
