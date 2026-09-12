# Fedora45 Image Update Loop — Architecture

The initial Fedora45 migration retains the Fedora44 application pins (Hermes 0.21.0, OpenClaw 2026.9.3). Only the Fedora base, image namespace and DNF-managed Node 26 change. Subsequent application-upgrade goals below remain separate. Node patch versions are selected by Fedora repositories and recorded from the built image.

[Import all four n8n workflows](n8n-fedora45-all.json) · [Main loop](n8n-fedora45-workflow.json) · [Shared repair](n8n-fedora45-repair.json) · [Integration tests](n8n-fedora45-integration.json) · [Checkpoint reporting](n8n-fedora45-step-report.json).

The main canvas contains 78 nodes instead of 158. Reporting calls drop from 81 to 23 (71.6%): structured results travel in the execution-local `run.events` array; Astra summarizes only decision checkpoints and outcomes. The final PDF is rendered once from the complete run. Application adapters and existing empty decision placeholders remain disabled and unimplemented; this refactoring does not make the deployment loop executable.

The repair and reporting LLM nodes use **Astra** through local LiteLLM: `astra` → `chatgpt/gpt-6-astra`, at `http://litellm-database:4000/v1` on Podman network `core`. LiteLLM stores the model persistently; n8n stores its credential locally. Both workflows remain inactive, with prompts and execution logic still pending.

The requirements below are binding acceptance rules. The first executable run targets **Hermes 0.21.2 and OpenClaw 2026.9.4**. Update `openclaw-ephemeral`, the Hermes adaptations, and the deterministic OpenClaw patch where compatibility requires it. Bounded LLM-assisted corrections must pass the same tests; they may not remove checks, weaken acceptance criteria, or silently omit a patch.

**Protected SOT:** Keep `SCRIPTS`, shared source-of-truth files such as `config.sh`, and their hardlinked copies unchanged by default. Changes are exceptional. Before applying one, prepare the exact diff and explain why it is unavoidable, then send the proposal through the **Telegram main agent**. Wait for the user's explicit **Go** for that proposal. Silence or an LLM's decision is not approval; changed proposals require a new Go. This rule also applies to automatic repairs and self-optimization.

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

**Core principle:** Decisions and verification are deterministic wherever possible. AI repairs failed version parsers or incompatible generators and writes concise step summaries. Repairs are re-verified deterministically; summaries never determine whether a check passes.

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
   Pull the frozen `stable` images locally through Smart1 via `mcp-safrano9999`. Generate Quadlets from existing configuration with `Image=...:stable`, using the planned Quadlet-only setup command. Run `systemctl --user daemon-reload` as the owning user, without restarting containers. Verify the stable image references and unchanged container IDs, start times, and service PIDs before continuing.

3. **Prepare the affected ephemeral generators**
   - Use the first checks' recorded results. For each incompatible generator, Astra prepares a correction in an isolated checkout; deterministic tests run again against both the target version and the frozen current version. Repeat only within the configured per-generator attempt limit. SOT changes still require the Telegram main-agent Go before application.
   - Once all affected generators are compatible, publish their tested commits on `main` and move each repository's Git tag `latest` to its exact tested commit through `mcp-safrano9999`. This also applies when no code correction was needed. Verify every remote commit and peeled tag; a partial publication or verification failure blocks the image build.
   - Record and use those exact commits as subsequent build inputs. Do not resolve moving `main` or `latest` references again during the run. The Core build-context script accepts `OPENCLAW_EPHEMERAL_COMMIT` and `HERMES_EPHEMERAL_COMMIT` as exact commit inputs; ordinary builds still default to `main`.
   - These are the standalone generator repositories' Git `latest` tags. Fedora image `latest` tags remain gated by the complete image tests in Step 4. Every check, correction attempt, and publication result is included in the PDF.

4. **Dry run**
   New versions are resolved on a trial basis (e.g. `apt-get install --dry-run`, `pip install --dry-run`/`--report`, an isolated build stage) — without producing the final image layer.
   Goal: catch dependency conflicts before actually building.

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

The container is started on a trial basis (CI or local sandbox) and checked against a defined checklist:

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

Keep deterministic configuration/assignment tests and live model, MCP, and network probes distinguishable in the report. All required checks must pass. For container-backed services, confirm the response comes from the candidate under test; a response from the previous production instance does not validate the candidate.

The PDF includes per-agent MCP assignments, tool-call results, resolved model results, and every Citadel/Tailscale link with its outcome. Grouped test nodes must include every inventory item's result in their report entry.

Use isolated, repeatable fixtures and the recorded patch references for these tests. A patch applying successfully is insufficient; its required behavior must also pass. Missing or `NOT_TESTED` checks block promotion.

**Only if every point passes** → mark the exact image "verified" (e.g. an additional `verified` tag), then advance `latest` through `mcp-safrano9999` to that same image digest. No rebuild occurs between verification and promotion.

**Failure case:** No retry with the same versions — abort, report/notification, old image stays active.

---

## 5. Self-healing script loop (for `check-versions.sh`)

Triggered only when the result validation from Step 1 fails (e.g. a 3rd party changes its versioning scheme or API response format).

1. **LLM escalation**
   The AI receives: the failing script, the raw (broken) 3rd-party output, and the error/reason the validation failed.
   Task: adapt `check-versions.sh` so it correctly handles the new format.

2. **Verification of the adapted script**
   The changed script is **never adopted blindly**:
   - It's run again against the raw 3rd-party output → must pass the "clean" check
   - It's also run against historical test fixtures (old formats must keep working)

3. **Adoption**
   Only after passing verification → committed, ideally as a PR with a review step, before it's promoted into the production pipeline path.

**Turn limit:** If the AI can't reliably fix the script after X attempts → abort, human is notified, the last known-good state stays active.

---

## 6. Local deployment

- Pull the newly promoted `latest` locally through Smart1 via **`mcp-safrano9999`** and verify that its digest matches the tested candidate.
- Generate the Quadlets from existing configuration with `Image=...:latest`.
- Run `systemctl --user daemon-reload` as the owning user.
- Recreate the containers from the verified `latest` image. A plain `podman restart` does not replace a container's image.
- Optional: blue-green deployment — the new container comes up in parallel, and only after another health check does the old one get shut down (minimizes downtime & rollback risk)

### Post-check

A small mini-loop after the restart:
- Is the new container stable?
- If not → restore `:stable` in the Quadlets, run `daemon-reload`, recreate the containers from the frozen stable images, and verify recovery.

---

## 7. Reporting — PDF summary via OpenClaw → Telegram

After every executed business step and selected decision branch, the shared reporting sub-workflow adds an entry to the run's **PDF report**. An LLM writes 1–2 concise English sentences from the recorded result; the PDF is updated and saved after each entry.

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

1. After each business step or selected decision branch, persist its structured result, run/step ID, attempt, status, duration, and artifacts. Redact sensitive data before summarization.
2. Ask an LLM for a 1–2 sentence summary. If it fails, times out, or provides an unusable summary, use the deterministic step result as a fallback.
3. Append the entry to the run's report and update the PDF locally. Return the original pipeline data unchanged; reporting never changes pass/fail decisions.
4. At the terminal outcome, finalize the accumulated PDF, including overall status. Retained structured results allow regeneration if an intermediate PDF update failed.
5. **OpenClaw** handles delivery: the file is sent to the configured Telegram channel/chat (bot token + authorized chat, as set up in OpenClaw).
6. Optional: a short status text message (traffic-light summary) before the PDF, so you can tell at a glance whether everything went fine without opening the PDF.

### Important for the integration

- OpenClaw needs a **triggerable interface** (CLI command, local command trigger, or a watched folder) that your deploy script calls at the end — depends on how OpenClaw is set up on your side.
- Sensible default: the same report is sent **every time**, even on failure/rollback — that's exactly when the notification matters most.
- Sensitive data (internal hostnames, secrets, tokens) must not end up unfiltered in the PDF report before it goes out over Telegram.
- Reporting nodes do not report themselves recursively. The delivery receipt is recorded locally after sending the final PDF.

---

## Overall flow (simplified)

Orchestrated end-to-end as an **n8n workflow**; steps marked `[mcp-safrano9999]` are executed via that MCP server. After every business step or selected branch: **LLM summary → collect in the run for the final PDF → continue**.

```
check-versions.sh ──► Result clean? ──no──► Self-healing loop (AI + verification)
        │ yes                                          │
        ▼                                (after fix, back into the loop)
   Update available? ──no──► Report + end
        │ yes
        ▼
   Check openclaw-ephemeral FIRST if OpenClaw changes
        │
        ▼
   Check hermes-ephemeral FIRST if Hermes changes
        │ conclusive results (unchanged component = not applicable)
        ▼
   Tag current versions as "stable" (cascaded, GHCR + GitHub) [mcp-safrano9999]
        │
        ▼
   Pull stable locally [mcp-safrano9999]
        │
        ▼
   Quadlets → stable ──► daemon-reload (no restart) ──► verify unchanged baseline
        │ ok
        ▼
   All affected ephemeral generators compatible? ──no──► Astra correction + deterministic retest
        │ yes                                                   │ bounded retry
        ◄────────────────────────────────────────────────────────┘
        ▼
   Publish tested generator commits: main + Git latest ──► verify every recorded commit
        │
        ▼
   Dry run + compatibility check ──fail──► Abort + report
        │ ok
        ▼
   Own OpenClaw/Hermes patches compatible? ──no──► Abort + report
        │ yes
        ▼
   Atomic commit
        │
        ▼
   GH Action build ──► Image with unique tag
        │
        ▼
   Test-container checklist ──fail──► Abort, "stable" tag stays the rollback target
        │ ok
        ▼
   Own OpenClaw/Hermes patch regressions ──fail / not tested──► Abort + report
        │ ok
        ▼
   Inventory all agents, MCP servers, models, Citadel links, and Tailscale links
        │
        ▼
   Test OpenClaw MCP assignments ──► test Hermes global MCP access
        │
        ▼
   Test all OpenClaw agent models ──► test all Hermes agent models
        │
        ▼
   Test Citadel and every link ──► test every Tailscale link
        │
        ▼
   Complete inventory covered and all integrations passed? ──no──► Abort + report
        │ all points pass
        ▼
   Image marked "verified"
        │
        ▼
   Tag verified image as "latest" [mcp-safrano9999]
        │
        ▼
   Local: pull latest [mcp-safrano9999] ──► verify tested digest
        │
        ▼
   Quadlets → latest ──► daemon-reload ──► recreate containers
        │
        ▼
   Health check ──fail──► Quadlets → stable → daemon-reload → recreate → verify recovery
        │ ok
        ▼
   Finalize accumulated PDF (all steps) ──► OpenClaw ──► Telegram
        │
        ▼
   Done
```

> Note: The reporting step **always** runs — even on abort/rollback in earlier steps, a PDF with the corresponding failure state is generated and sent.

---

## Core principles at a glance

| Principle | Implementation |
|---|---|
| Determinism by default | `check-versions.sh`, dry run, compatibility check, test-container checklist |
| Atomicity | Version pins are never adopted partially |
| Unique image identity | Every candidate remains identifiable by its fixed version and digest |
| AI for repair and summaries | Bounded parser/generator corrections, deterministically verified; concise per-step PDF entries |
| No blind trust in AI output | The adapted script is re-verified against fixtures + live data |
| Turn limits everywhere | Prevents endless retry loops in build, test, and script repair |
| Rollback capability | On both test failure and failed post-deploy health check |
| Traceability | Each executed step updates the run PDF; the complete report is sent via OpenClaw to Telegram, including failures |
| Guaranteed rollback target | After read-only generator prechecks, freeze current images and full repositories as `stable` before generator corrections, pin changes, or builds |
| Clear tag semantics | Generator Git `latest` = tested source published before image builds; image `latest` = candidate accepted by all mandatory image tests; `stable` = frozen baseline |
| Earliest generator checks | OpenClaw → `openclaw-ephemeral`; Hermes → `hermes-ephemeral`. Check first, repair if needed, retest, publish Git `latest`, then build from the exact tested commits |
| Custom-patch compatibility | OpenClaw/Hermes patches pass preflight and candidate-container regressions before promotion |
| Explicit Quadlet transitions | Stable + reload without restart at initialization; latest + reload + recreation after tests; stable restored on rollback |
| Centralized orchestration | The whole loop (all 3 nested loops) runs as a single n8n workflow |
| Isolated build/pull layer | All build and pull operations go through `mcp-safrano9999`, not ad-hoc scripts |
