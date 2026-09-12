const state = { current: "", directory: "", dirty: false, ready: false, openDirs: new Set() };
const tree = document.querySelector("#tree");
const status = document.querySelector("#status");
const current = document.querySelector("#current");
const empty = document.querySelector("#empty");
const editorHost = document.querySelector("#editor");
const sidebar = document.querySelector("#sidebar");
const dialog = document.querySelector("#entry-dialog");
const entryName = document.querySelector("#entry-name");
let dialogMode = "";

const editor = new Vditor("editor", {
  mode: "ir",
  lang: "de_DE",
  cdn: "/vendor",
  cache: { enable: false },
  height: "100%",
  toolbar: ["headings", "bold", "italic", "strike", "link", "list", "ordered-list", "check", "quote", "code", "inline-code", "table", "upload", "undo", "redo", "fullscreen", "preview"],
  input: () => { if (state.ready) state.dirty = true; },
  upload: {
    accept: "image/*,.pdf,.mp4,.webm,.mp3,.wav",
    handler: async files => {
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const result = await api(`/api/upload?directory=${encodeURIComponent(currentDirectory())}`, { method: "POST", body: form });
        const image = file.type.startsWith("image/");
        editor.insertValue(`${image ? "!" : ""}[${file.name}](${result.url})`);
      }
      return null;
    },
  },
  after: () => { state.ready = true; },
});

function setStatus(message, error = false) {
  status.textContent = message;
  status.style.color = error ? "var(--danger)" : "";
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function currentDirectory() {
  if (state.directory) return state.directory;
  if (!state.current.includes("/")) return "";
  return state.current.slice(0, state.current.lastIndexOf("/"));
}

function joinPath(base, name) {
  return [base.replace(/\/$/, ""), name.replace(/^\//, "")].filter(Boolean).join("/");
}

function renderEntries(entries) {
  const list = document.createElement("ul");
  list.className = "tree-list";
  for (const entry of entries) {
    const item = document.createElement("li");
    const row = document.createElement("div");
    const button = document.createElement("button");
    row.className = "tree-row";
    button.type = "button";
    button.className = `${entry.kind}${entry.path === state.current || entry.path === state.directory ? " active" : ""}`;
    button.textContent = entry.name;
    button.title = entry.path;
    if (entry.kind === "directory") {
      const children = renderEntries(entry.children || []);
      children.hidden = !state.openDirs.has(entry.path);
      button.classList.toggle("open", !children.hidden);
      button.addEventListener("click", () => {
        state.directory = entry.path;
        if (state.openDirs.has(entry.path)) state.openDirs.delete(entry.path);
        else state.openDirs.add(entry.path);
        children.hidden = !state.openDirs.has(entry.path);
        button.classList.toggle("open", !children.hidden);
      });
      item.append(row, children);
    } else {
      button.addEventListener("click", () => openFile(entry.path));
      item.append(row);
    }
    row.append(button);
    list.append(item);
  }
  return list;
}

async function refreshTree() {
  const data = await api("/api/tree");
  tree.replaceChildren(renderEntries(data.entries));
}

async function openFile(path) {
  if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen?")) return;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  state.ready = false;
  editor.setValue(data.content);
  state.current = data.path;
  state.directory = data.path.includes("/") ? data.path.slice(0, data.path.lastIndexOf("/")) : "";
  let parent = state.directory;
  while (parent) {
    state.openDirs.add(parent);
    parent = parent.includes("/") ? parent.slice(0, parent.lastIndexOf("/")) : "";
  }
  state.dirty = false;
  state.ready = true;
  current.textContent = data.path;
  empty.classList.add("hidden");
  editorHost.classList.remove("hidden");
  sidebar.classList.remove("open");
  setStatus("Geöffnet");
  await refreshTree();
}

async function saveFile() {
  if (!state.current) return setStatus("Keine Datei geöffnet", true);
  await api("/api/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: state.current, content: editor.getValue() }),
  });
  state.dirty = false;
  setStatus("Gespeichert");
}

function ask(mode, title, value = "") {
  dialogMode = mode;
  document.querySelector("#dialog-title").textContent = title;
  entryName.value = value;
  dialog.showModal();
  entryName.focus();
}

async function applyDialog() {
  const name = entryName.value.trim();
  if (!name) return;
  if (dialogMode === "rename") {
    const oldPath = state.current || state.directory;
    const parent = oldPath.includes("/") ? oldPath.slice(0, oldPath.lastIndexOf("/")) : "";
    const result = await api("/api/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: oldPath, destination: joinPath(parent, name) }),
    });
    if (state.current === oldPath) state.current = result.path;
    else state.directory = result.path;
    current.textContent = state.current || "Keine Datei geöffnet";
  } else {
    const kind = dialogMode === "new-directory" ? "directory" : "file";
    const result = await api("/api/entry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: joinPath(currentDirectory(), name), kind }),
    });
    if (kind === "file") await openFile(result.path);
  }
  await refreshTree();
  setStatus("Übernommen");
}

async function removeEntry() {
  const path = state.current || state.directory;
  if (!path || !confirm(`Wirklich löschen: ${path}?`)) return;
  await api(`/api/entry?path=${encodeURIComponent(path)}`, { method: "DELETE" });
  if (path === state.current) {
    state.current = "";
    state.dirty = false;
    current.textContent = "Keine Datei geöffnet";
    editorHost.classList.add("hidden");
    empty.classList.remove("hidden");
  } else state.directory = "";
  await refreshTree();
  setStatus("Gelöscht");
}

document.querySelector("header nav").addEventListener("click", event => {
  const action = event.target.dataset.action;
  if (!action) return;
  const selected = state.current || state.directory;
  if (action === "save") saveFile().catch(error => setStatus(error.message, true));
  if (action === "delete") removeEntry().catch(error => setStatus(error.message, true));
  if (action === "rename" && selected) ask("rename", "Umbenennen", selected.split("/").pop());
  if (action === "new-file") ask(action, "Neue Markdown-Datei");
  if (action === "new-directory") ask(action, "Neuer Ordner");
});

dialog.addEventListener("close", () => {
  if (dialog.returnValue === "default") applyDialog().catch(error => setStatus(error.message, true));
});
document.querySelector("#menu").addEventListener("click", () => sidebar.classList.toggle("open"));
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveFile().catch(error => setStatus(error.message, true));
  }
});
window.addEventListener("beforeunload", event => { if (state.dirty) event.preventDefault(); });
editorHost.classList.add("hidden");
refreshTree().catch(error => setStatus(error.message, true));
