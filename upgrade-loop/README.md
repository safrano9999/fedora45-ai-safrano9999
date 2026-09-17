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
4. On the GitHub runner, capture the current default-branch commit of **both** Ephemeral
   repositories. Test each generator against its selected upstream release,
   including the component whose upstream version did not change. Validate the
   latest compatible OpenClaw deterministic artifact/checksum and Hermes patch applicability.
   Resolve NOTE's latest published stable release and its asset checksum as well.
5. After all checks pass, update both upstream pins and both tested Ephemeral
   pins together with the selected deterministic and NOTE releases/checksums in
   one Git commit. Publish without force-pushing. Concurrent repository
   changes block publication rather than mixing untested sources.
6. Read and validate the Action's evidence artifact. Only `READY_FOR_BUILD`
   opens the source-volume gate: discover the selected image's parent chain at
   its exact build commit, then clone/update its Safrano source repositories in
   the persistent n8n volume with depth 1. Return the preparation evidence and
   source manifest to Hermes. Missing evidence or a failed sync stops the run.

A version change in OpenClaw therefore also incorporates a new Hermes-Ephemeral
commit, and vice versa. Both selected generator commits remain fixed throughout
preparation and become explicit inputs for the subsequent image build.

All Safrano components select their latest applicable source afresh for each
preparation: images use `:latest`, Git sources use the current default branch,
and release payloads use the latest published stable release. The OpenClaw
payload must match the target OpenClaw version. This also refreshes the
deterministic patch when only Hermes changed, and refreshes NOTE on either
upstream update. A matching fixed release tag may represent the exact bytes
of a rolling `latest` asset; its SHA256 is recorded and verified. The resulting
pins are a snapshot for that preparation/build, not a policy to stay on an old
release. NOTE, patch and generator updates alone still do not open the build gate.

## Commit-bound build handoff

Only `READY_FOR_BUILD` includes `safrano_build_inputs: {"build_commit": "<40-hex SHA>"}`.
The user/Hermes passes that object as `inputs` to Safrano MCP `build_images`, with
the selected Fedora45 chain key. `action="plan"` validates the proposed dispatch
using read calls only. `action="run"` requires the explicit prepared commit,
creates/reuses the exact lightweight tag `build-<SHA>`, and dispatches its workflow
at that tag. An existing tag pointing elsewhere is rejected, never moved.
`run_workflow` enforces the same rule for Fedora45 image workflows.

All six image Actions accept `build_commit`, check out that SHA with depth 1,
verify `git rev-parse HEAD`, and pass the same SHA and workflow ref to each cascade
stage. The job summary and `org.opencontainers.image.revision` label record it.
Both workflow definitions and source checkouts therefore stay on the prepared
commit when `main` advances. Setting the movable repository tag `stable` is not
required for this handoff. Direct manual Actions without `build_commit` select
their immutable dispatch-event SHA once and carry it through their cascade.

n8n still does not dispatch a build or move a repository/image tag. The build
handoff pins this Fedora45 repository, including both tested Ephemeral commit
declarations; independently moving component branch refs retain the behavior
described in the source inventory section below.

## Entry points and credentials

[Main workflow](n8n-fedora45-workflow.json) retains the ID `fedora45LoopDraft` and
its authenticated POST endpoint `/webhook/fedora45-update-loop`. An empty body
starts preparation. The `?--check` query flag or literal body `--check` returns
exactly two version-status lines and ends without dispatching an Action.
The explicit `?--validate-only` flag (or literal `--validate-only` body) tests
both selected runtimes on Actions without publishing pins. It ends at
`VALIDATED_ONLY`, never at `READY_FOR_BUILD`.

`?--sources` (or literal body `--sources`) freshly resolves the latest source
repositories/releases and their exact commits; it
does not dispatch an Action or create/update any checkout. Optional query
parameters `target=fedora45-ai-safrano9999-full` and `ref=<commit>` select a
different image chain/source revision for this preview. The default target is
`fedora45-ai-safrano9999`, default ref `main`. Resolve the image commit once and
read every declaration at that immutable commit.
The `target` query parameter also selects the source chain for normal preparation;
`ref` is preview-only. Successful preparation always syncs its published build commit.

## Source inventory and persistent shallow checkouts

The inventory follows `FROM` through the parent image variables in `build.conf`.
It reads `EXTENSIONS`, `STANDALONE` (including `repo@branch`) and literal
`*_REPOSITORY` declarations in the layer's build configuration/preparation helper.
Configuration is parsed as data; no repository code or shell expressions run.
Unknown/dynamic declarations, ambiguous parents and conflicting refs stop discovery.
Only `safrano9999` sources enter the list. External Fedora, OpenClaw/Hermes
upstream, RPM, npm and PyPI sources are excluded. `SCRIPTS` is build tooling,
not an image component, so it is not added to the component clone list.

The regular image currently selects 22 repositories including this image repo;
`-full` adds `VikAI` for 23. This is derived, not a maintained repository allowlist.
The preview reports `source_policy: latest-resolved-for-preview`, the previous
`declared_ref`, the currently selected `ref` and its exact `commit`; release
assets also record their SHA256. It does not alter published build inputs.
For a ready build, release tags and both Ephemeral pins from that preparation
are honored rather than resolving different payloads after compatibility checks;
remaining branch/HEAD
refs are resolved to commits before syncing. The manifest records their selection
time. These snapshots do not pin an otherwise moving branch in a later GitHub
build; the build's own source evidence remains authoritative for its actual inputs.

Checkouts live at `/home/node/.n8n/fedora45-sources/<repository>` in the existing
n8n named volume. First checkout uses `git clone --depth 1`; updates use
`git fetch --depth 1` and detached checkout of the resolved commit. HEAD identity
and one-commit ancestry are verified. A successful `manifest.json` records every
checkout. Existing local edits, unexpected origins, symlinks and concurrent runs
stop synchronization. Repositories removed from the selection are not updated
or automatically deleted. A failed multi-repo sync can leave some clean checkouts
updated; it never returns `sources_ready`, and rerunning safely converges.

`--check`, `--sources`, `--validate-only`, `NO_UPDATE` and blocked preparation
never clone/update repositories. The trigger is still a new upstream OC/Hermes
version; Ephemeral-only commits cannot open this gate. Builds remain on GitHub.

The narrow custom node `CUSTOM.fedora45Sources` uses the existing n8n GitHub
credential. Git receives authentication only through child-process environment,
not URLs, arguments, workflow data or `.git/config`. It runs only Git inside n8n;
it does not execute checkout scripts, initialize submodules, use SSH, access a
container socket or run a local image build. Execute Command remains disabled.

Install/update the three files from [n8n-sources](n8n-sources) in
`/home/node/.n8n/custom/fedora45-sources/`, then restart n8n when no execution is
running. The custom node and checkouts persist in the existing volume. Install
the node before publishing/importing the workflow; no AI-container restart,
new host mount or additional container environment secret is needed.

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
node --test upgrade-loop/tests/test_sources.cjs
```

The separate Safrano MCP now supports authenticated HTTPS as well as stdio.
Its ucore entry is `safrano9999-mcp`, configured through the shared numbered
MCP environment group. The service can build/pull when the user/Hermes requests
it; adding the server to the environment does not start either operation.
