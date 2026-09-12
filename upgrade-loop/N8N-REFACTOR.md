# Fedora45 workflow refactoring

- Main workflow: 158 → 83 nodes. All four workflows: 128 nodes (previous main + reporter: 167).
- Reporting calls: 81 → 23, including final rendering (71.6% fewer). No per-business-step reporting subworkflow calls remain.
- Repair proposals share one Astra model and chain. Artifact-specific deterministic validation, attempt limits, review conditions and retry routes are retained in the shared repair workflow.
- The entire eight-node integration block, including its original coverage gate, moved to an independently callable workflow.
- Every original If node and its condition parameters are preserved exactly. New switches only dispatch artifact types or aggregate results; no stable/latest/verified decisions are changed.
- Checkpoint summaries occur immediately before If evaluation, once per gate. The selected branch is recorded by the following step/checkpoint. Endpoint summaries include success, no-update, abort and both rollback outcomes (rollback failure retains the existing shared abort endpoint).
- Facts are stored in the current run item, not workflow-global static data. Secret fields are redacted. No PDF is rewritten after individual steps; final rendering receives the whole run once.
- Existing business adapters and decision placeholders remain disabled. The missing final renderer fails explicitly instead of pretending a PDF exists. The structural refactor is not an executable deployment implementation.

Import `n8n-fedora45-all.json` to install the main, repair, integration and reporting workflows together. The existing LiteLLM credential is referenced, never exported with a key.

Runtime validation: the local n8n task runner executes the reporter and receives a real Astra summary through LiteLLM Chat Completions. An intentional FAIL remains FAIL. JSON payload cloning and the existing local credential reference are compatible with the installed n8n runtime.

Redaction is maintained once in `report-redaction.js`. Run `python3 generate-workflows.py` after editing it, then `python3 generate-workflows.py --check` before publishing/importing. The generator refreshes 48 embedded helpers and the four-workflow bundle. Copies remain in the portable JSON export; no runtime library, extra node or per-step subworkflow call is needed.

Startup policy: four native five-minute Wait nodes cover candidate startup, deployment, rollback and standalone integration entry. Candidate startup and rollback recovery checks are separate steps. All SOT/source edits require prior user discussion and explicit Go; startup model-catalog loading alone is no source-patch trigger. Existing gate conditions and tagging outcomes remain unchanged.
