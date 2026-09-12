from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class EntryRequest(BaseModel):
    path: str
    kind: str


class FileRequest(BaseModel):
    path: str
    content: str


class RenameRequest(BaseModel):
    path: str
    destination: str


class Storage:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def resolve(self, value: str, *, root_allowed: bool = False) -> Path:
        relative = Path(value or ".")
        if relative.is_absolute():
            raise ValueError("Only workspace-relative paths are allowed")
        candidate = (self.root / relative).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Path leaves the workspace") from exc
        if candidate == self.root and not root_allowed:
            raise ValueError("The workspace root cannot be changed")
        return candidate

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def tree(self, directory: Path | None = None) -> list[dict[str, object]]:
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        for child in children:
            if child.name.startswith(".") or child.is_symlink():
                continue
            if child.is_dir():
                entries.append({
                    "name": child.name,
                    "path": self.relative(child),
                    "kind": "directory",
                    "children": self.tree(child),
                })
            elif child.suffix.casefold() == ".md":
                entries.append({"name": child.name, "path": self.relative(child), "kind": "file"})
        return entries

    def create(self, value: str, kind: str) -> Path:
        path = self.resolve(value)
        if kind == "file" and path.suffix.casefold() != ".md":
            path = Path(f"{path}.md")
        if path.exists():
            raise FileExistsError(value)
        if kind == "directory":
            path.mkdir(parents=True)
        elif kind == "file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
        else:
            raise ValueError("Entry kind must be file or directory")
        return path

    def delete(self, value: str) -> None:
        path = self.resolve(value)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            raise FileNotFoundError(value)


ROOT = Path(os.environ.get("VDITOR_NOTES_PATH", "/data"))
VENDOR = Path(os.environ.get("VDITOR_VENDOR_PATH", "/usr/local/lib/vditor"))
APP_DIR = Path(__file__).resolve().parent
storage = Storage(ROOT)
app = FastAPI(title="Vditor Notes", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/vendor", StaticFiles(directory=VENDOR, check_dir=False), name="vendor")


def fail(error: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(error))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/tree")
def get_tree() -> dict[str, object]:
    return {"entries": storage.tree()}


@app.get("/api/file")
def get_file(path: str) -> dict[str, str]:
    try:
        target = storage.resolve(path)
        if target.suffix.casefold() != ".md" or not target.is_file():
            raise FileNotFoundError(path)
        return {"path": storage.relative(target), "content": target.read_text(encoding="utf-8")}
    except (ValueError, OSError) as error:
        raise fail(error, 404) from error


@app.put("/api/file")
def save_file(request: FileRequest) -> dict[str, str]:
    try:
        target = storage.resolve(request.path)
        if target.suffix.casefold() != ".md":
            raise ValueError("Only Markdown files can be edited")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(request.content, encoding="utf-8")
        temporary.replace(target)
        return {"path": storage.relative(target)}
    except (ValueError, OSError) as error:
        raise fail(error) from error


@app.post("/api/entry")
def create_entry(request: EntryRequest) -> dict[str, str]:
    try:
        target = storage.create(request.path, request.kind)
        return {"path": storage.relative(target), "kind": request.kind}
    except (ValueError, OSError) as error:
        raise fail(error) from error


@app.post("/api/rename")
def rename_entry(request: RenameRequest) -> dict[str, str]:
    try:
        source = storage.resolve(request.path)
        destination = storage.resolve(request.destination)
        if not source.exists():
            raise FileNotFoundError(request.path)
        if source.is_file() and source.suffix.casefold() == ".md" and destination.suffix.casefold() != ".md":
            destination = Path(f"{destination}.md")
        if destination.exists():
            raise FileExistsError(request.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return {"path": storage.relative(destination)}
    except (ValueError, OSError) as error:
        raise fail(error) from error


@app.delete("/api/entry")
def delete_entry(path: str) -> dict[str, bool]:
    try:
        storage.delete(path)
        return {"deleted": True}
    except (ValueError, OSError) as error:
        raise fail(error) from error


@app.get("/api/raw")
def raw_file(path: str) -> FileResponse:
    try:
        target = storage.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return FileResponse(target)
    except (ValueError, OSError) as error:
        raise fail(error, 404) from error


@app.post("/api/upload")
async def upload_file(directory: str = "", file: UploadFile = File(...)) -> dict[str, str]:
    try:
        base = storage.resolve(directory, root_allowed=True)
        media = base / "media"
        media.mkdir(parents=True, exist_ok=True)
        filename = Path(file.filename or "attachment").name
        target = media / filename
        counter = 1
        while target.exists():
            target = media / f"{Path(filename).stem}_{counter:02d}{Path(filename).suffix}"
            counter += 1
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        relative = storage.relative(target)
        return {"path": relative, "url": f"/api/raw?path={relative}"}
    except (ValueError, OSError) as error:
        raise fail(error) from error
