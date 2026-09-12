"""Reject stale Fedora names in project-owned image files, paths and PDFs."""
import json
import os
import re
from pathlib import Path

pattern = re.compile(rb"fedora(?:[ _:\-]|\\_)?44", re.I)
roots = ("/etc", "/usr/local/bin", "/usr/local/libexec", "/usr/lib/systemd/system-generators",
         "/usr/local/share", "/opt/safrano9999", "/README")
failures, checked, pdfs = set(), 0, 0
for root in roots:
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in (".git", "__pycache__", "node_modules", ".venv", "venv")]
        for name in dirs + files:
            path = Path(directory) / name
            if pattern.search(str(path).encode()):
                failures.add(str(path))
            if path.is_symlink():
                if pattern.search(os.readlink(path).encode()):
                    failures.add(str(path))
                continue
            if not path.is_file():
                continue
            checked += 1
            if path.suffix.lower() == ".pdf":
                import pypdfium2
                with pypdfium2.PdfDocument(path) as document:
                    for page in document:
                        text = page.get_textpage()
                        try:
                            if pattern.search(text.get_text_range().encode()):
                                failures.add(str(path))
                        finally:
                            text.close()
                            page.close()
                pdfs += 1
            else:
                with path.open("rb") as stream:
                    previous = b""
                    while block := stream.read(1024 * 1024):
                        if pattern.search(previous + block):
                            failures.add(str(path))
                            break
                        previous = block[-32:]
print(json.dumps({"status": "FAIL" if failures else "PASS", "files_checked": checked,
                  "pdfs_checked": pdfs, "stale_names": sorted(failures)}, indent=2))
raise SystemExit(bool(failures))
