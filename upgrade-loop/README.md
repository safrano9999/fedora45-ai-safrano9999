# Fedora45 Image Update Loop — Architecture

The Fedora45 migration upgrades Hermes to 0.21.2 and OpenClaw to 2026.9.4, including compatibility checks for the project patches and both Ephemeral generators. Node 26 comes from Fedora RPMs; its patch version is selected by Fedora repositories and recorded from the built image.

[Import all four n8n workflows](n8n-fedora45-all.json) · [Main loop](n8n-fedora45-workflow.json) · [Shared repair](n8n-fedora45-repair.json) · [Integration tests](n8n-fedora45-integration.json) · [Checkpoint reporting](n8n-fedora45-step-report.json).

The manual Fedora45 bootstrap records its [pre-migration baseline](evidence/fedora45-2026.9.1-baseline.json). Repeatable container probes cover [agent model replies](probes/runtime-models.py) and the installed [per-agent MCP projection](probes/runtime-mcp-projection.mjs); model probes use dedicated sessions without channel delivery.

The four workflows contain deterministic host adapters and explicit decision conditions. The main workflow also accepts authenticated POST requests at `/webhook/fedora45-update-loop`: an empty body starts the existing run; the query flag `?--check` (or literal body `--check`) returns only two version-status lines as `text/plain` and ends. Only this check branch is exercised during installation; the complete upgrade workflow is validated with the next real upstream upgrade.

Optional `WEBHOOK_URL`, `WEBHOOK_BEARER`, `WEBHOOK_TIMES`, and `WEBHOOK_INIT` groups configure independent native OpenClaw command cron jobs. The existing `new`/`skip` setup repeats the fields with `_02`, `_03`, etc. Empty URLs are skipped; an empty bearer sends no Authorization header. CSV times use Europe/Vienna (CET/CEST), defaulting to 00:00 when blank. The final Ephemeral systemd service additionally fires `WEBHOOK_INIT=true` hooks in group order after gateway startup; the optional default is `false`. The common Fullrun has been removed. The webhook caller performs a generic HTTP POST and forwards response text to the configured main Telegram target; module-owned delivery acknowledgements do not produce duplicate messages. Our version-check URL ends in `?--check`; removing those seven characters requests the normal full workflow. The check output remains exactly two lines, using `✅` for current versions and `🟡` for available updates. The normal upgrade workflow, gates, and tagging semantics remain unchanged.

**Live sequence:** build fixed candidates → Smart1 pull → recreate the existing instance with its original volumes → wait until that container start is five minutes old → run the required checks → promote the exact image to `verified` and `latest` → set the Quadlet to `latest` and daemon-reload, without another restart. A failed live check restores `stable` with the same volumes.

The [live policy](live-flow-policy.json) caps the test phase at 600 seconds. Checks use fixed scripts, bounded HTTP concurrency and small real model probes; no LLM grades test results and no automatic test retry runs. The integration sub-workflow reuses the same boot timestamp, results and deadline. Every result remains in the final PDF; unfinished checks are `NOT_TESTED`.

Astra runs through local LiteLLM (`http://litellm-database:4000/v1`, model `astra`) once for the final summary and when a repair proposal is needed. Business and checkpoint nodes make no LLM calls. [steps.py](steps.py) records execution-local state; [live-checks.py](live-checks.py) calls the existing probes; [render-report.py](render-report.py) renders the complete PDF once. Redaction remains centralized in [report-redaction.js](report-redaction.js).

The local n8n SSH credential invokes the host runner; the owner, instance, Quadlet and registry auth-file paths are stored in `~/.config/fedora45-upgrade-loop.json`. Credentials are not included in the workflow exports. Run receipts and PDFs are stored under `~/.local/state/fedora45-upgrade-loop/<run-id>/`. Source repairs require an explicit `GO` record bound to the proposed patch SHA-256 and source commit; the workflow never creates that approval itself. Ordinary declared version, commit and checksum pin updates are the upgrade operation already described here.

The requirements below are binding acceptance rules. Hermes **0.21.2** and OpenClaw **2026.9.4** are the installed baseline; the next run selects newer releases only when the version check finds them. Propose changes to `openclaw-ephemeral`, Hermes adaptations or OpenClaw patches only when compatibility evidence requires them; applying any source change needs prior user discussion and explicit Go. Bounded LLM-assisted corrections must pass the same tests; they may not remove checks, weaken acceptance criteria, or silently omit a patch.

**SOT and source-code changes require prior approval — ALWAYS:** All SOT and source-code changes ALWAYS require prior discussion and the user's explicit Go for the exact proposed diff, through the Telegram main agent. This includes SCRIPTS/config.sh, hardlinks, OpenClaw, Hermes, generators and repair scripts. An LLM cannot approve or apply a change autonomously. Initial model-catalog loading or first-call slowness alone never justifies a source patch. Prepare the exact diff and reason, discuss it with the user, and wait for Go before changing SOT or source code. Silence, test failures or a successful verification are not approval; a changed proposal requires a new Go. This also applies to automatic repairs and self-optimization.

## Background: What is a "Loop" here?

This design is based on the agentic loop concepts from Anthropic's blog post
[*Getting started with loops*](https://claude.com/blog/getting-started-with-loops):

- **Loop** — An agent doesn't just do one thing and stop. It gathers context, takes an action, checks the result, and repeats if needed — until the task is actually finished.
- **Goal** — A clear, *measurable* definition of "done," so both the agent and any checking mechanism know when the task is truly complete (not just "looks done").
- **Goal-based loop** — A loop where an *evaluator* checks whether an attempt actually satisfies the goal. The agent keeps iterating until the goal is met, or a **turn limit** is hit (a cap that stops endless retries if the goal is never reached).

This document applies that pattern to a concrete infrastructure use case: automatically updating a Docker image built from multiple software packages, testing it safely, deploying it, and reporting the outcome.

## Overview

The system consists of three nested loops:

1. **Deploy loop** — controls the overall flow from version check to container restart
2. **Build/test loop** — validates a new image before it gets deployed
3. **Self-healing script loop** — repairs the version-check script when 3rd-party formats change

**Core principle:** Decisions and verification are deterministic wherever possible. AI proposes repairs for failed version parsers or incompatible generators and writes one final summary. Repairs are re-verified deterministically; summaries never determine whether a check passes.

## Orchestration & tooling

The entire loop — all three levels (deploy, build/test, self-healing) — is orchestrated as an **n8n workflow**. n8n is the "conductor": it triggers each step, evaluates conditions (update available? checklist passed? health check ok?), and decides whether to continue, retry, or abort.

- **Build and pull operations** run through the **`mcp-safrano9999`** MCP server. n8n calls this MCP server for:
  - triggering the GitHub Action build
  - the cascaded `stable`/`latest` tagging on GHCR and GitHub repos
  - pulling the verified image locally for deployment
- **n8n nodes** handle the surrounding logic: version comparison, dry-run/compatibility evaluation, checklist evaluation, report generation, and calling OpenClaw for the Telegram delivery.
- Turn limits and abort conditions are implemented as n8n workflow branches (e.g. an "If retries exceeded → notify and stop" path), so the workflow itself enforces the goal-based loop pattern rather than relying on an external scheduler.

---

## 1. Gather context — version check

- `check-versions.sh` runs deterministically (parsers/regex/API calls against 3rd-party sources)
- Compares current version pins against available releases
- **No update found** → report the result and end without infrastructure changes

### Result validation (intermediate step)

Before the output of `check-versions.sh` is used further, it's checked for:

- Expected format followed (semver pattern, JSON schema, expected fields present)?
- Sane values (no empty strings, no exceptions)?
- Plausibility (e.g. new version isn't lower than the currently pinned one)?

| Result | Action |
|---|---|
| ✅ Clean | If an update is available, check the corresponding ephemeral generator first as described below; otherwise report and end |
| ❌ Not clean | Escalate to the self-healing script loop (see Section 5) |

### First checks: OpenClaw and Hermes ephemeral compatibility

Before the stable cascade, dry runs, pin changes, or image builds, test each affected generator against its exact proposed upstream version in isolation:

| Upstream update | First compatibility check |
|---|---|
| OpenClaw | `openclaw-ephemeral`: generated configuration, agent/default-model inheritance, MCP assignments, plugins, and scheduling against the target OpenClaw runtime. |
| Hermes | `hermes-ephemeral`: ENV/config generation, models, global MCP access, and systemd integration against the target Hermes runtime. |

If both change, run both checks, OpenClaw first. An unchanged upstream records its branch as not applicable. Record target versions, generator commits, and deterministic results in the run PDF.

These first checks are read-only. A completed test may establish compatibility or identify incompatibilities; both outcomes proceed to the stable snapshot. Missing tests, unavailable target inputs, or an inconclusive result abort the run.

---

## 2. Action — freeze stable, prepare ephemeral, then check compatibility

After version validation and the initial generator checks, run these steps in order:

1. **Tag current version as `stable`**
   For every affected image (this can cascade across multiple dependent images/repos — e.g. a base image and everything built on top of it), the *currently running* version is re-tagged as `stable` on both GHCR and the corresponding GitHub repo. This preserves a known-good fallback point before the dry run, version-pin changes, or builds.
   - Include the full `openclaw-ephemeral` and `hermes-ephemeral` repository baselines before adopting generator corrections. Cascade order matters: base images are re-tagged first, then dependent images.

2. **Prepare stable Quadlets without restarting**
   Pull the frozen `stable` images locally through Smart1 via `mcp-safrano9999`. Change only the image references in the existing Quadlet and Compose files to `:stable`; preserve all instance settings and volume mappings. Run `systemctl --user daemon-reload` as the owning user, without restarting containers. Verify the stable image references and unchanged container IDs, start times, and service PIDs before continuing.

3. **Prepare the affected ephemeral generators**
   - Use the first checks' recorded results. For each incompatible generator, Astra prepares a correction in an isolated checkout; deterministic tests run again against both the target version and the frozen current version. Repeat only within the configured per-generator attempt limit. Every SOT or source-code change requires prior discussion and explicit Telegram main-agent Go before application.
   - Once all affected generators are compatible, publish their tested commits on `main` and move each repository's Git tag `latest` to its exact tested commit through `mcp-safrano9999`. This also applies when no code correction was needed. Verify every remote commit and peeled tag; a partial publication or verification failure blocks the image build.
   - Record and use those exact commits as subsequent build inputs. Do not resolve moving `main` or `latest` references again during the run. The Core build-context script accepts `OPENCLAW_EPHEMERAL_COMMIT` and `HERMES_EPHEMERAL_COMMIT` as exact commit inputs; ordinary builds still default to `main`.
   - These are the standalone generator repositories' Git `latest` tags. Fedora image `latest` tags remain gated by the complete image tests in Step 4. Every check, correction attempt, and publication result is included in the PDF.

4. **Dry run**
   `prepare-runtime.py` resolves the target runtimes for the generator checks without building the final image. Validate the declared pin changes, published patch inputs and Hermes patch applicability before committing. Image package installation is checked by the existing build Actions.

5. **Compatibility check**
   - Changelog/semver comparison (breaking change on a major bump?)
   - Known incompatible combinations (block-/allow-list)
   - Matrix check for package dependencies (e.g. "Package A ≥2.0 requires Lib B ≥1.5")
   - Own patches and generators: `openclaw-ephemeral`, `hermes-ephemeral`, the `openclaw-deterministic` overlay, and any additional Safrano patches must support the proposed upstream versions.
   - Record exact patch source commits and artifact digests. Check patch application and required configuration, schema, and API interfaces in isolation; never silently omit an incompatible patch.

6. **Atomic commit**
   Only if the dry run, dependency compatibility, and own-patch compatibility checks all pass → all version pins are committed in a single step (never partially).

**Failure case:** Generator repair is exhausted, publication verification fails, or a subsequent dry run/compatibility check fails → abort, no image build triggered, report generated.

---

## 3. Build — publish an identifiable candidate

After Step 2 completes, build and publish the candidate:

1. **Build the new version**
   - Dispatch the GitHub Action through `mcp-safrano9999` with `push_latest=false` and `push_stable=false`.
   - Publish each candidate with the same fixed `YYYY.M.N` tag for this run. The cascade passes this tag as the next layer's parent reference, together with both publishing flags; downstream builds use that candidate instead of `latest`.
   - Record every candidate digest for testing and deployment. Existing workflows default to `push_latest=true`; the upgrade loop must explicitly disable it.

Keep `latest` unchanged during the build and tests. `stable` remains the frozen running baseline. Step 4 promotes the exact tested candidate after verification.
Multiple GHCR tags cannot move in one atomic transaction. Promote only after the complete required candidate set passes, verify every promoted digest, and deploy only that recorded set. A partial promotion blocks deployment.

---

## 4. Check — test-container checklist

Pull the exact candidate and recreate the actual instance with its existing volumes, **wait a full five minutes for ALL services**, then begin the checklist. No readiness, port, CLI, model, MCP, Citadel, Tailscale or other runtime test runs during this boot interval. Apply the same order after deployment, rollback and every restart during testing. The independently callable integration workflow requires a run ID with a recorded baseline and waits only for the remainder of that same five-minute boot interval. It does not add another five minutes after the main loop has already waited.

OpenClaw builds its model catalog initially. Record first and repeated calls separately; initial slowness alone is normal and does not justify a source patch. Preserve actual failures and `NOT_TESTED`. Record container-start, wait-completion and test timestamps in the run report.

Checklist after the wait:

- [ ] Does the container start without a crash loop?
- [ ] Are all expected processes/services running?
- [ ] Does the health/readiness endpoint respond with 200?
- [ ] Are the expected ports open and reachable?
- [ ] Are critical binaries/CLI tools executable (`--version` check)?
- [ ] Are config files/volumes mounted correctly?
- [ ] Are logs free of known error patterns?
- [ ] Do functional smoke tests pass (e.g. a test request against an app route)?
- [ ] Do our OpenClaw and Hermes patches work with the new upstream versions in the actual candidate container?
- [ ] Do ENV-to-config generation, default-model inheritance, systemd integration, persistence/migrations, and tool invocation pass their regression cases?
- [ ] Does OpenClaw preserve per-agent MCP selection, while Hermes continues to expose all configured MCP servers?

Mandatory integration coverage for the new container:

| Area | Required checks |
|---|---|
| Inventory | Derive the complete agent, MCP, model, Citadel-link, and Tailscale-link inventory from the intended configuration. Track every expected entry and its result; no fixed sample list. |
| OpenClaw MCP | For every agent, verify the exact allowed MCP servers and tools, absence of unassigned servers, authentication, and a successful non-destructive tool call against each assigned server. |
| Hermes MCP | For every Hermes agent/profile, verify access and a successful non-destructive tool call for every enabled MCP server. Hermes continues to ignore OpenClaw's per-agent MCP selection field. |
| OpenClaw models | For every agent, resolve its inherited default or explicit model and configured fallbacks. Send a small real request through OpenClaw using each resolved model; verify authentication, response, and tool-calling compatibility. |
| Hermes models | Run the equivalent model checks through each configured Hermes agent/profile, including inherited defaults and configured fallbacks. |
| Citadel | Verify Citadel starts and renders its configured entries. Open every link, follow expected redirects/authentication, and verify the intended target service or page works. |
| Tailscale links | Test every configured Tailscale URL/address from the intended tailnet access context, including name resolution, routing, TLS where applicable, and the actual target response. |

MCP or model listings alone do not count as successful integration tests. Intentionally disabled entries require an explicit expected-disabled check. Unexpected omissions and untested entries fail the coverage gate.

Keep deterministic configuration/assignment tests and live model, MCP, and network probes distinguishable in the report. All required checks must pass. Confirm the container ID, start timestamp and image ID match the newly deployed candidate throughout the checks.

The PDF includes per-agent MCP assignments, tool-call results, resolved model results, and every Citadel/Tailscale link with its outcome. Grouped test nodes must include every inventory item's result in their report entry.

Use isolated, repeatable fixtures and the recorded patch references for these tests. A patch applying successfully is insufficient; its required behavior must also pass. Missing or `NOT_TESTED` checks block promotion.

**Only if every point passes** → mark the exact image "verified" (e.g. an additional `verified` tag), then advance `latest` through `mcp-safrano9999` to that same image digest. No rebuild occurs between verification and promotion.

**Failure case:** No automatic retry with the same versions. Restore the frozen stable image, wait for its boot interval, check recovery and report every recorded result.

---

## 5. Self-healing script loop (for `check-versions.sh`)

Triggered only when the result validation from Step 1 fails (e.g. a 3rd party changes its versioning scheme or API response format).

1. **LLM escalation**
   The AI receives: the failing script, the raw (broken) 3rd-party output, and the error/reason the validation failed.
   Task: propose a minimal diff for `check-versions.sh`. Discuss it with the user and obtain explicit Go before applying any source edit, including in a repair checkout.

2. **Verification of the adapted script**
   The changed script is **never adopted blindly**:
   - It's run again against the raw 3rd-party output → must pass the "clean" check
   - It's also run against historical test fixtures (old formats must keep working)

3. **Adoption**
   Only the explicitly approved diff may be applied and verified; after passing verification it may be committed and adopted. Verification does not replace prior user approval.

**Turn limit:** If the AI can't reliably fix the script after X attempts → abort, human is notified, the last known-good state stays active.

---

## 6. Local deployment

- After the successful build, pull the immutable candidate through Smart1 via `mcp-safrano9999`.
- Set the existing Quadlet to the candidate digest, daemon-reload and recreate the actual instance with its original volumes.
- Wait until that start is five minutes old, then run the required live checks and health check.
- Only after every required check passes, promote the exact tested digests to `verified` and `latest`. Update the local `latest` aliases to those same images, set the Quadlet to `latest` and daemon-reload. **No second restart.**
- On any live failure, restore `:stable`, daemon-reload, recreate the same instance, wait five minutes and check recovery. If the live instance was never replaced, restore the stable Quadlet without an unnecessary restart.
- Keep all persistent volumes. Image rollback does not revert application data or migrations already written to them.
- Remove temporary candidate digest entries from Smart1 after success or successful recovery; retain `latest` and `stable`. Keep candidate references when recovery fails.

---

## 7. Reporting — PDF summary via OpenClaw → Telegram

Every business step and selected branch appends its structured result to the execution-local `run.events` array. Decision gates evaluate structured results directly; the shared reporting workflow makes one final LLM summary call. The final PDF is rendered once from the complete run, including every step and repair attempt.

At the end of the loop (success, no update, rollback, or abort), the complete PDF is delivered to Telegram via **OpenClaw**. Repeated repair attempts remain separate entries in the same report.

### Contents of the PDF summary

- **Versions:** old vs. new version per package, update source
- **Dry run/compatibility check:** result, any conflicts found
- **Own patches:** source commits, artifact digests, target upstream versions, compatibility results, and candidate-container regression results
- **Build:** image tag, build time, GH Action run link
- **Test-container checklist:** status of every single checkpoint (✅/❌)
- **Deploy status:** succeeded / rolled back / aborted, with health-check result
- **Self-healing loop (if triggered):** what was changed in `check-versions.sh`, verification result
- **Overall status:** a traffic-light-style summary at the top of the document (green/yellow/red)

### Flow

1. Each business step appends its status, timestamp, run/step ID, attempt, duration, and relevant artifacts to `run.events`.
2. After the run ends, call Astra once for a short final summary. Keep the deterministic report when summarization fails.
3. Combine that summary with the complete recorded run. Preserve all original pass/fail decisions; no PDF is rendered at intermediate checkpoints.
4. At the terminal outcome, collect all results and render the complete run into one final PDF. Keep structured results so the report can be regenerated.
5. **OpenClaw** handles delivery: the file is sent to the configured Telegram channel/chat (bot token + authorized chat, as set up in OpenClaw).
6. Optional: a short status text message (traffic-light summary) before the PDF, so you can tell at a glance whether everything went fine without opening the PDF.

Redaction is maintained in [report-redaction.js](report-redaction.js). Run `python3 generate-workflows.py` after an edit to refresh the portable workflow exports.

### Important for the integration

- OpenClaw needs a **triggerable interface** (CLI command, local command trigger, or a watched folder) that your deploy script calls at the end — depends on how OpenClaw is set up on your side.
- Sensible default: the same report is sent **every time**, even on failure/rollback — that's exactly when the notification matters most.
- Sensitive data (internal hostnames, secrets, tokens) must not end up unfiltered in the PDF report before it goes out over Telegram.
- Reporting nodes do not report themselves recursively. The delivery receipt is recorded locally after sending the final PDF.

---

## Overall flow

```text
Version check → no update: final report
      ↓ update
Read-only generator checks → freeze stable images and source repositories
      ↓
Prepare compatible generators and patch inputs → commit declared pins
      ↓
Build fixed candidates (push_latest=false) → Smart1 pull
      ↓
Recreate the real instance with original volumes → wait for 5-minute boot age
      ↓
Deterministic live checks + integration coverage + health
      ├─ fail → restore stable → restart if needed → wait → recovery check → report
      ↓ pass
verified + latest → local latest aliases → Quadlet latest → daemon-reload
      ↓
One Astra summary → one complete PDF → OpenClaw → Telegram
```

Repair failures use one shared proposal/approval/verification workflow. No source or SOT edit occurs without the user's explicit Go. No test run is started during workflow installation.

---

## Core principles at a glance

| Principle | Implementation |
|---|---|
| Determinism by default | `check-versions.sh`, dry run, compatibility check, test-container checklist |
| Atomicity | Version pins are never adopted partially |
| Unique image identity | Every candidate remains identifiable by its fixed version and digest |
| AI for repair and summaries | Bounded repair proposals with explicit approval and deterministic verification; one final summary |
| No blind trust in AI output | The adapted script is re-verified against fixtures + live data |
| Turn limits everywhere | Prevents endless retry loops in build, test, and script repair |
| Rollback capability | On both test failure and failed post-deploy health check |
| Traceability | Each executed step appends structured results; one final PDF is sent via OpenClaw to Telegram, including failures |
| Guaranteed rollback target | After read-only generator prechecks, freeze current images and full repositories as `stable` before generator corrections, pin changes, or builds |
| Clear tag semantics | Generator Git `latest` = tested source published before image builds; image `latest` = candidate accepted by all mandatory image tests; `stable` = frozen baseline |
| Earliest generator checks | OpenClaw → `openclaw-ephemeral`; Hermes → `hermes-ephemeral`. Check first, repair if needed, retest, publish Git `latest`, then build from the exact tested commits |
| Custom-patch compatibility | OpenClaw/Hermes patches pass preflight and candidate-container regressions before promotion |
| Explicit Quadlet transitions | Stable + reload without restart at initialization; latest + reload + recreation after tests; stable restored on rollback |
| Centralized orchestration | The whole loop (all 3 nested loops) runs as a single n8n workflow |
| Isolated build/pull layer | All build and pull operations go through `mcp-safrano9999`, not ad-hoc scripts |
