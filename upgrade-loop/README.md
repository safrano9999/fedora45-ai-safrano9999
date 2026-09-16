# Fedora45 container preparation

n8n prepares build inputs on GitHub Actions. It does not access the host, build
or pull images, change tags or Quadlets, restart containers, or test the running
instance. This scope replaces the former host-based update/deployment loop.

The user/Hermes controls the later steps: build through SAFRANO_MCP, pull through
SAFRANO_MCP, and a separate deterministic container check after restart. That
container check is owned by the user and is not implemented by this workflow.

## n8n sequence

1. Read the published OpenClaw/Hermes pins from GitHub `main` and compare them
   with the latest stable upstream releases. Reject malformed data and downgrades.
2. Only a newer OpenClaw **or** Hermes release starts preparation. Ephemeral-only
   commits never trigger preparation or an image build.
3. Dispatch `fedora45-container-preparation.yml` on GitHub Actions and follow
   the exact execution ID through a unique correlation ID. No image-build Action
   is dispatched. Wait at most one hour; failures stop the workflow.
4. On the GitHub runner, capture the current `main` commit of **both** Ephemeral
   repositories. Test each generator against its selected upstream release,
   including the component whose upstream version did not change. Validate the
   published OpenClaw deterministic artifact/checksum and Hermes patch applicability.
5. After all checks pass, update both upstream pins and both tested Ephemeral
   pins in one Git commit. Publish without force-pushing. Concurrent repository
   changes block publication rather than mixing untested sources.
6. Read the Action's evidence artifact. Return `READY_FOR_BUILD`, its exact
   Git commit, both generator commits and check results to Hermes. A failing,
   incomplete or missing result never becomes a successful handoff.

A version change in OpenClaw therefore also incorporates a new Hermes-Ephemeral
commit, and vice versa. Both selected generator commits remain fixed throughout
preparation and become explicit inputs for the subsequent image build.

## Entry points and credentials

[Main workflow](n8n-fedora45-workflow.json) retains the ID `fedora45LoopDraft` and
its authenticated POST endpoint `/webhook/fedora45-update-loop`. An empty body
starts preparation. The `?--check` query flag or literal body `--check` returns
exactly two version-status lines and ends without dispatching an Action.
The explicit `?--validate-only` flag (or literal `--validate-only` body) tests
both selected runtimes on Actions without publishing pins. It ends at
`VALIDATED_ONLY`, never at `READY_FOR_BUILD`.

The baseline is the **published source pins**, not the version of the currently
running container. n8n does not inspect that container. The separate Hermes
routine can later compare the prepared, built and running identities.

The main workflow uses the existing webhook bearer and a GitHub API credential
`fedora45GitHubPreparation`. Secrets are stored in n8n credentials, never in
workflow exports. Actions uses `GH_RELEASE_TOKEN` for private generator access
and publishing the two version-pin files. It has no host credential.
Artifact redirects are resolved separately; the signed storage download receives
no GitHub credential and must use GitHub's HTTPS artifact-storage domain.

The former integration, repair and report workflow IDs are retained as inactive
retirement notices with `availableInMCP: false`. They contain no host adapters.
Their old graphs remain in n8n history and Git history; they must not be republished.
The old `steps.py`, `live-checks.py` and probes are legacy utilities, not invoked
by the preparation workflow. `fedora45HostRunner` is not referenced by any of
these four workflow exports.

## Source changes and failure handling

Routine declared version, commit and checksum pin updates are preparation.
Application, generator, patch and repair-script changes still require the
user's explicit Go for the exact proposed diff. A failed compatibility check
returns `BLOCKED`; preparation does not invent a repair, omit a patch or weaken
checks. Startup/model-catalog delays are not evidence for a source modification.

The Action stores `container-preparation.json` and a GitHub job summary for
successful, blocked and validation-only runs. Runtime model/MCP/Citadel/Tailscale
checks belong to the separate post-restart routine, not to n8n preparation.

## Maintenance and validation

Actions definitions originate in `SCRIPTS/githubactions`, then synchronize into
`.github/workflows`. [prepare-container.py](prepare-container.py) runs on the
GitHub runner. `validate_only=true` tests the selected versions and both latest
generators without committing pins, building or pulling an image.

Regenerate the portable [four-workflow bundle](n8n-fedora45-all.json):

```sh
python3 upgrade-loop/generate-preparation-workflow.py
python3 upgrade-loop/generate-workflows.py
python3 upgrade-loop/generate-workflows.py --check
python3 -m unittest discover -s upgrade-loop/tests -p test_preparation.py
```

The separate Safrano MCP now supports authenticated HTTPS as well as stdio.
Its ucore entry is `safrano9999-mcp`, configured through the shared numbered
MCP environment group. The service can build/pull when the user/Hermes requests
it; adding the server to the environment does not start either operation.
