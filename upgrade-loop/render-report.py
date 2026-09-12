"""Render the complete structured run once, using only the Python standard library."""
import json
from pathlib import Path
import subprocess
import textwrap


def render(run, summary, destination):
    helper = (Path(__file__).with_name("report-redaction.js")).read_text()
    script = helper + '\nconst fs=require("fs");const j=JSON.parse(fs.readFileSync(0,"utf8"));j.run.events=safe(j.run.events);process.stdout.write(JSON.stringify(j));'
    run = json.loads(subprocess.run(["node", "-e", script], input=json.dumps(run), text=True,
                                   capture_output=True, check=True, timeout=10).stdout)
    lines = ["Fedora45 upgrade report", "Run: " + run["run"]["id"], "Status: " + run["status"], "", summary, ""]
    for event in run["run"]["events"]:
        lines.extend(json.dumps(event, indent=2, ensure_ascii=True).splitlines())
        lines.append("")
    wrapped = [part for line in lines for part in (textwrap.wrap(line, 110, replace_whitespace=False) or [""])]
    pages = [wrapped[i:i + 65] for i in range(0, len(wrapped), 65)]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    page_ids = []
    for page in pages:
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode())
        content = b"BT /F1 8 Tf 11 TL 32 810 Td\n"
        for line in page:
            escaped = line.encode("ascii", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            content += b"(" + escaped + b") Tj T*\n"
        content += b"ET\n"
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream")
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(str(i) + ' 0 R' for i in page_ids)}] >>".encode()
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    destination.write_bytes(document)
    destination.chmod(0o600)
