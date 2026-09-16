#!/usr/bin/env python3
"""Refresh shared code in portable n8n exports and regenerate the import bundle."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEGIN = "// BEGIN GENERATED: report-redaction.js\n"
END = "// END GENERATED: report-redaction.js\n"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="Fail if generated exports are stale")
args = parser.parse_args()
helper = BEGIN + (ROOT / "report-redaction.js").read_text().rstrip() + "\n" + END
workflows, outputs = [], {}
for suffix in ("step-report", "repair", "integration", "workflow"):
    path = ROOT / f"n8n-fedora45-{suffix}.json"
    workflow = json.loads(path.read_text())
    workflow.setdefault("settings", {})["availableInMCP"] = True
    for node in workflow["nodes"]:
        parameters = node.get("parameters", {})
        code = parameters.get("jsCode", "")
        if code.startswith(BEGIN):
            parameters["jsCode"] = helper + code.split(END, 1)[1]
        elif "function safe(" in code:
            raise SystemExit(f"Unmanaged redaction helper: {path.name}: {node['name']}")
    workflows.append(workflow)
    outputs[path] = workflow
outputs[ROOT / "n8n-fedora45-all.json"] = workflows
stale = []
for path, data in outputs.items():
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.read_text() != rendered:
        stale.append(path.name)
        if not args.check:
            path.write_text(rendered)
if args.check and stale:
    raise SystemExit("Regenerate workflows: " + ", ".join(stale))
print("Workflow exports are current." if args.check else f"Updated {len(stale)} workflow exports.")
