# Fedora45 preparation boundary

The current contract is [README.md](README.md). n8n only prepares container build
inputs through GitHub API/GitHub Actions. All four exports are free of SSH,
host access, local image builds, deployment and live-container checks. A narrow
custom node runs shallow Git checkouts inside n8n's existing persistent volume.

A new OpenClaw or Hermes release opens the gate. Both latest Ephemeral commits
must be captured, tested and pinned together. Only `READY_FOR_BUILD` then allows
cloning/updating the Safrano repositories derived from the selected image's
Containerfile/build.conf chain, always depth 1 and at resolved commits.
Checks, previews, validation-only and no-update runs do not mutate the volume.
Build/pull are controlled separately by the user/Hermes through Safrano MCP.
Safrano source selection always starts from latest: current default branches,
latest stable NOTE, and the latest deterministic payload matching OpenClaw.
The exact selected commits/releases/checksums are then held for that run.
`--sources` resolves latest afresh without changing pins or cloning repositories.
The user owns the deterministic test routine after the next container restart.

The former three host sub-workflows are inactive retirement notices. The old
host runner and live probes remain legacy files and are not part of this run.
