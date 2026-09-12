# Fedora45 workflow implementation

- The main workflow uses deterministic host adapters with explicit decision conditions.
- Build and Smart1 operations run through `mcp-safrano9999`.
- Build → pull → recreate the real instance with original volumes → five-minute boot age → bounded live checks.
- The integration workflow shares the same container identity, boot interval, run results and deadline.
- Every failed live check leads to stable recovery. After successful checks, promote verified/latest and update the Quadlet without another restart.
- One shared repair workflow retains proposal, exact-diff approval, live/fixture verification and adoption. It never approves source changes itself.
- One final Astra summary and one complete PDF replace per-gate LLM summaries. Shared redaction is used by the exports and PDF renderer.
- Manual workflows remain inactive. No end-to-end execution is performed during installation; validation is deferred to the next real upgrade.
