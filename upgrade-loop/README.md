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
2. By default, only a newer OpenClaw **or** Hermes release starts preparation.
   Explicit `upgrade-safrano9999` additionally opens the gate for changed consumed
   Safrano sources, starting at the earliest affected published image.
3. After the update gate, `Resolve latest sources once` resolves the latest
   Safrano sources inside n8n. It records all selected commits and release
   checksums in one source snapshot and dispatches `fedora45-container-preparation.yml`
   with that snapshot. Follow the exact execution through its correlation ID;
   no image-build Action is dispatched.
4. The GitHub runner consumes the snapshot without resolving latest again.
   Test both selected Ephemeral generators against their selected upstream
   releases, including the component whose upstream version did not change.
   Check Hermes patch applicability; use the selected OpenClaw/NOTE assets.
5. After checks pass, publish the upstream, generator and release inputs together
   with `upgrade-loop/prepared-sources.json` in one Git commit. Concurrent main
   changes block the push; never force-push or mix different source snapshots.
6. Verify that the returned snapshot matches the one selected by n8n. Only
   `READY_FOR_BUILD` opens the source-volume gate: clone/update exactly those
   dependency commits with depth 1. The Fedora45 repository itself uses the new
   prepared build commit, which contains the snapshot. Return evidence and the
   clone manifest to Hermes. A missing snapshot or failed sync stops the run.

A version change in OpenClaw therefore also incorporates a new Hermes-Ephemeral
commit, and vice versa. Both selected generator commits remain fixed throughout
preparation and become explicit inputs for the subsequent image build.

All Safrano components select their latest applicable source once inside n8n,
after the upstream-update gate: images use `:latest`, Git sources use the current default branch,
and release payloads use the latest published stable release. The OpenClaw
payload must match the target OpenClaw version. This also refreshes the
deterministic patch when only Hermes changed, and refreshes NOTE on either
upstream update. A matching fixed release tag may represent the exact bytes
of a rolling `latest` asset; its SHA256 is recorded and verified. The resulting
pins are a snapshot for that preparation/build, not a policy to stay on an old
release. NOTE, patch and generator updates alone open the gate only with explicit
`upgrade-safrano9999`. The default remains the upstream release gate.

## Commit-bound build handoff

Only `READY_FOR_BUILD` includes `safrano_build_inputs: {"build_commit": "<40-hex SHA>"}`.
The handoff also includes `safrano_build_key` and the complete
`safrano_build_request` (`action:run`, `section:chains`, `key`, `cascade`,
`inputs`). The user/Hermes passes that request to Safrano MCP `build_images`. `action="plan"` validates the proposed dispatch
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
declarations and the shared dependency snapshot. All six image Actions load
that snapshot. Persistainer and the component helpers fetch exactly the selected
commits; Core verifies its generator/release inputs against it. The separate
NEXTCLOUD runtime archive is bound to its selected asset ID and SHA256 as well.

## Entry points and credentials

[Main workflow](n8n-fedora45-workflow.json) retains the ID `fedora45LoopDraft` and
its authenticated POST endpoint `/webhook/fedora45-update-loop`. An empty body
starts preparation. The `?--check` query flag or literal body `--check` returns
exactly two version-status lines and ends without dispatching an Action.
The explicit `?--validate-only` flag (or literal `--validate-only` body) tests
both selected runtimes on Actions with an explicitly requested snapshot, without publishing pins. It ends at
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

## Explicit Safrano source upgrades

Send `{"mode":"upgrade-safrano9999","feedback":true}` through the same webhook
or n8n MCP webhook input. Literal `upgrade-safrano9999`,
`--upgrade-safrano9999` and boolean `upgrade-safrano9999:true` are also accepted.
The flag cannot be combined with `--check` or `--sources`.

For the complete agent-controlled sequence, use `upgrade-safrano9999 + auto`
(literal body or JSON `{"mode":"upgrade-safrano9999+auto"}`). Equivalently, add
`"auto":true` to `{"mode":"upgrade-safrano9999"}`. This selects saved completion
feedback for preparation and includes `auto_pull:true` and `feedback:true` in
`safrano_build_request`. Explicit `feedback:false` disables both notifications.
The client passes the returned request unchanged to Safrano MCP `build_images`
on `READY_FOR_BUILD`; it need not assemble any extra flags or inputs. That MCP
builds the selected cascade and queues Smart1 pulls as stages succeed. Feedback
arrives after all builds/pulls succeed or immediately on failure. There is no
container restart in this mode. `NO_UPDATE` stops without a build or pull.
`auto:false` preserves preparation-only behavior; auto cannot be combined with
check-only modes. The two servers use their existing saved callback settings;
no callback secret is placed in the build handoff or published source snapshot.

The maximum form is `upgrade-safrano9999 + auto-upgrade` (or JSON
`{"mode":"upgrade-safrano9999+auto-upgrade"}`). It also sets `auto_upgrade:true`
in the build request. Safrano MCP then owns build → pull → whitelist auto-update
as one persisted operation, with one final notification after that whole operation,
or immediately on failure. The last step updates whitelisted digests and requests
restarts of affected previously active services; it does not wait for readiness.
No build/pull failure can start that whitelist upgrade.

If there is no source update, maximum mode still returns
`safrano_upgrade_request:{"action":"auto-update","feedback":true}` for the client
to pass to `podman_smart1`. This is the only extra follow-up in the no-build case;
plain `+ auto` still stops on `NO_UPDATE`. Explicit `feedback:false` is respected.
The n8n preparation callback remains separate from the Safrano operation callback.

After resolving sources once, n8n reads each selected GHCR image's `:latest`
manifest and configuration, verifies their digests and reads
`org.opencontainers.image.revision`. Its GitHub checkout supplies the source
snapshot actually published in that image. Preparation commits on `main` alone
are never treated as proof of a completed build. These are metadata GETs, no
image pulls; private packages require the GitHub credential to have read access.
Missing or inconsistent evidence stops preparation instead of guessing.

Compare consumed repository commits, release payload checksums, inherited source
versions and image-definition files. The parent rootfs layers also detect an
image built on an older parent. Each stage is compared separately, so a partially
completed cascade resumes at its first outdated descendant. Select the earliest
changed stage across all reasons: Hermes-Ephemeral alone means `fedora45_core`;
an OpenClaw/Hermes runtime release means `fedora45_core_pre`. A Base-only source
change starts at `fedora45_base`. Runtime Containerfiles, build configuration,
build helpers and image overlays count at their consuming stages; changes to
n8n orchestration or repository documentation do not force an image rebuild.

No changes returns `NO_UPDATE` before Action dispatch and before volume sync.
Changes follow the existing compatibility checks and commit publication, then
return the exact start key in `safrano_build_request`. The selected target still
limits the cascade; when the start is already the target, `cascade` is false. n8n prepares and hands off; Safrano MCP performs the build.
Completion feedback remains one empty webhook notification on any final outcome.

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
For a ready build, every dependency uses the same snapshot selected before the
preparation Action. Clone/sync does not re-query GitHub for newer refs. Image
build helpers read the identical snapshot from the prepared build commit, so a
branch or release advancing after selection cannot silently change the build.

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

Install/update all JavaScript files, `rendezvous.json`, `package.json` and
`package-lock.json` from [n8n-sources](n8n-sources) in
`/home/node/.n8n/custom/fedora45-sources/`. Run `npm ci --omit=dev --ignore-scripts
--registry=https://registry.npmjs.org/` in that directory inside n8n, then restart
n8n when no execution is running. The custom nodes, npm dependency and checkouts
persist in the existing volume. Install
the node before publishing/importing the workflow; no AI-container restart,
new host mount or additional container environment secret is needed.

The baseline is the **published source pins**, not the version of the currently
running container. n8n does not inspect that container. The separate Hermes
routine can later compare the prepared, built and running identities.

The main workflow uses the existing webhook bearer and a GitHub API credential
`fedora45GitHubPreparation`. Secrets are stored in n8n credentials, never in
workflow exports. Actions uses `GH_RELEASE_TOKEN` for private generator access
and publishing the version-pin files and shared source snapshot. It has no host credential.
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
npm ci --prefix upgrade-loop/n8n-sources --ignore-scripts
node --test upgrade-loop/tests/test_feedback.cjs
```

The separate Safrano MCP now supports authenticated HTTPS as well as stdio.
Its ucore entry is `safrano9999-mcp`, configured through the shared numbered
MCP environment group. The service can build/pull when the user/Hermes requests
it; adding the server to the environment does not start either operation.

### Optional completion hook

Through n8n MCP `execute_workflow`, use production mode and a JSON webhook body such as `{"feedback":true,"callback_url":"http://…/webhooks/ucore-mcp-finish.hook","callback_secret":"…"}`. Legacy `--check`, `--sources`, and `--validate-only` remain valid; JSON may specify `"mode":"--check"`. Passing `callback_url` opts in; `feedback:false` explicitly suppresses it. With neither URL nor `feedback:true`, no callback is sent. A private `/home/node/.n8n/fedora45-feedback/default.json` containing `{"url":"…","secret":"…"}` allows subsequent calls to use only `feedback:true`.

The custom completion node uses the published [`mcp-rendezvous` npm package](https://www.npmjs.com/package/mcp-rendezvous) to store and deliver notifications in the existing n8n volume. There is no separate REST service. The small n8n worker still reads n8n's own execution table using the existing database connection, with a read-only PostgreSQL session (or SQLite read-only mode). It maps `success` to success, `error`/`crashed` to failure and `canceled` to cancellation; the library then sends exactly `{}` to the callback. No outcome details leave in the body; the client retrieves execution evidence separately. HMAC and a stable delivery ID support Hermes' native webhook adapter. Failed deliveries are retried and survive container restarts. The receiving hook should deduplicate delivery IDs. Client-provided secrets are sensitive request inputs; the custom node removes them from the body, query and raw JSON binary before continuing the graph.

The workflow description and the "MCP start and completion feedback" note instruct MCP clients to supply a callback URL or opt into the saved default before starting. `execute_workflow` already instructs clients to read workflow details first. Once it returns the execution ID, end the turn and do not poll. Direct webhook callers also receive an HTTP 202 JSON acknowledgement with `feedback_enabled`, `feedback_id` and the library's `next` instruction. `--check` keeps its two-line response. Only webhook feedback is enabled in this container; `herdr_target` belongs to host-side Safrano MCP and is not supported here.

Before upgrading the old worker, let any pending legacy notifications finish. Already delivered numeric job files remain as history and are never resent; new jobs use the library's UUID format with the n8n execution ID recorded as metadata. The existing private `default.json` is reused. Repeated registration for the same execution recovers its existing job. A fresh worker heartbeat is required before a feedback-enabled request continues.

Install the custom files and their locked npm dependencies as described above, then restart n8n after updating custom node definitions. This adds no host access, image build or deployment to the preparation loop. Automatic preparation still requires an upstream OpenClaw/Hermes version change. The GitHub preparation Action's `force_prepare` input is reserved for an explicitly requested operator rebuild.

### Stable third-party build upgrades

Use `upgrade-build-deps` to resolve every separately installed third-party tool
in Core-pre from the explicit [build whitelist](build-dependencies-whitelist.json).
The [Python resolver](build_dependencies.py) runs on the GitHub preparation runner.
The initial whitelist covers Solana/Agave, Electrum, LND, Geth, BIP39, uv,
cloudflared, webhook, Fugu, Codex CLI, Claude Code, Vditor and npm.

Each entry specifies a release line and stores its exact version and SHA-256
(or Git commit for Fugu and Electrum signing keys). Stable is the default.
Solana follows Anza's stable channel, even if another GitHub release is newer.
LND's regular `-beta` releases are allowed; RCs, alpha and nightly releases are
rejected. Fugu uses its published regular release and checks that the files
required by the image are present. No fallback to an untested development branch.
Changed bytes under the same version, missing assets, downgrades, duplicate
entries or divergent build.conf pins fail preparation before any pins are written.

The resolver updates the whitelist and `fedora45-ai-core-pre/build.conf` in the
same tested preparation commit. Containerfile downloads verify the saved hashes;
the image build never reselects a newer version for these pins. n8n compares against
the policy in the actually published Core-pre image, so prepared but unbuilt pins
still require a build. Changes select `fedora45_core_pre`, even when the simultaneous
Safrano source upgrade could otherwise start later. No change means NO_UPDATE and
no source clone or image build. A requested dependency check itself runs as a
GitHub preparation Action. Read-only local inspection is `python3 upgrade-loop/build_dependencies.py`; `--emit` validates saved pins without network
access. `--apply` is reserved for the GitHub runner; normal publication belongs to
prepare-container.py's shared commit.

The Fedora beta **base image digest is excluded**. OpenClaw and Hermes keep their
existing release and compatibility checks. Safrano repositories keep their own
source-upgrade path. RPM/Python packages use the existing repository/requirements
constraints on image build; this routine does not rewrite requirements in other
repositories or enable `--pre`. Their transitive dependencies are not a new lockfile.

Combine the options in `body.mode`, for example:

```text
upgrade-build-deps
upgrade-build-deps + auto
upgrade-safrano9999 + upgrade-build-deps + auto-upgrade
```

Equivalent JSON: `{"mode":"upgrade-safrano9999","upgrade_build_deps":true,
"auto_upgrade":true}`. The last form includes source checks, stable build pins,
GitHub image builds, automatic Smart1 pulls and the existing whitelist image
upgrade with nonblocking service starts. n8n only prepares and returns the Safrano
request; the client passes that request to Safrano MCP under the supplied auto
instruction. Webhook completion and the no-poll instruction apply to both phases.
