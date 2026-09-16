# Fedora45 preparation boundary

The current contract is [README.md](README.md). n8n only prepares container build
inputs through GitHub API/GitHub Actions. All four exports are free of SSH,
local command execution, deployment and live-container checks.

A new OpenClaw or Hermes release opens the gate. Both latest Ephemeral commits
must be captured, tested and pinned together. n8n ends at `READY_FOR_BUILD`.
Build/pull are controlled separately by the user/Hermes through Safrano MCP.
The user owns the deterministic test routine after the next container restart.

The former three host sub-workflows are inactive retirement notices. The old
host runner and live probes remain legacy files and are not part of this run.
