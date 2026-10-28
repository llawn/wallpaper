// Folder picker: a button showing the folder and a popover with a search field over the collection tree from
// /api/folders. Listed collection folders can be chosen (no free typing); the media above them are shown for
// context. "New subfolder…" names a new folder inside a non-external one (the server creates it on save).
// External collections are marked, and the caller can refuse a row with a reason (moving into or out of a
// submodule is not possible).

import { moveIndex, SelectMenu } from "./select-menu.js";
import { normaliseTag as slugName } from "./tag-input.js";

/** Tree order: by path parts, so "painting/japan/ukiyo-e" stays under "painting/japan". */
function byPath(a, b) {
  const pa = a.path.split("/"), pb = b.path.split("/");
  for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
    if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  }
  return pa.length - pb.length;
}

/** Rows a new subfolder can go in: everything except external collections and folders inside them. */
export function parentRows(rows) {
  const ext = rows.filter((r) => r.external).map((r) => r.path);
  return rows.filter((r) => !ext.some((e) => r.path === e || r.path.startsWith(`${e}/`)));
}

/** {path, error} for a new folder `name` inside `parent`: the name is slugged like the server's folder names. */
export function newFolderPath(parent, name, rows) {
  if (!parent) return { path: "", error: "Choose the folder it goes in." };
  if (!parentRows(rows).some((r) => r.path === parent)) {
    return { path: "", error: "New folders cannot go inside an external collection." };
  }
  const leaf = slugName(name);
  if (!leaf) return { path: "", error: "Give the folder a name with letters or digits." };
  const path = `${parent}/${leaf}`;
  if (rows.some((r) => r.path === path)) return { path, error: `${path} already exists: choose it in the list.` };
  return { path, error: "" };
}

/** Flat, sorted tree rows from /api/folders: [{path, name, depth, selectable, external}], with the intermediate
 *  folders (media such as "painting") inserted as non-selectable rows. */
export function folderRows(folders) {
  const rows = new Map();
  for (const f of folders) {
    const parts = f.folder.split("/");
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const own = i === parts.length - 1;
      const row = rows.get(path) ?? { path, name, depth: i, selectable: false, external: false };
      if (own) Object.assign(row, { selectable: true, external: Boolean(f.external) });
      rows.set(path, row);
    });
  }
  return [...rows.values()].sort(byPath);
}

/** Rows whose path contains `text` (case-insensitive), with their ancestors kept for context. */
export function filterRows(rows, text) {
  const t = String(text ?? "").trim().toLowerCase();
  if (!t) return rows;
  const hits = rows.filter((r) => r.path.toLowerCase().includes(t));
  const keep = new Set();
  for (const r of hits) {
    const parts = r.path.split("/");
    for (let i = 1; i <= parts.length; i++) keep.add(parts.slice(0, i).join("/"));
  }
  return rows.filter((r) => keep.has(r.path));
}

let uid = 0;

export class FolderPicker {
  /**
   * @param {HTMLElement} host
   * @param {{id: string, onChange: (folder: string) => void, refuse?: (row: object) => string}} options
   *        `refuse(row)` returns why a row cannot be chosen ("" when it can)
   */
  constructor(host, { id, onChange, refuse = () => "" }) {
    this.host = host;
    this.onChange = onChange;
    this.refuse = refuse;
    this.rows = [];
    this.shown = [];
    this.value = "";
    this.active = -1;
    this.locked = "";
    const n = ++uid;
    host.classList.add("picker");
    this.button = document.createElement("button");
    this.button.type = "button";
    this.button.id = id;
    this.button.className = "picker-trigger";
    this.button.setAttribute("aria-haspopup", "dialog");
    this.button.setAttribute("aria-expanded", "false");
    this.text = document.createElement("span");
    this.text.className = "picker-path";
    this.button.append(this.text);

    this.pop = document.createElement("div");
    this.pop.className = "picker-pop";
    this.pop.id = `folder-pop-${n}`;
    this.pop.setAttribute("role", "dialog");
    this.pop.setAttribute("aria-label", "Choose a folder");
    this.pop.hidden = true;
    this.button.setAttribute("aria-controls", this.pop.id);
    this.search = document.createElement("input");
    this.search.type = "search";
    this.search.className = "picker-search";
    this.search.placeholder = "Find a folder";
    this.search.autocomplete = "off";
    this.search.spellcheck = false;
    this.search.setAttribute("aria-label", "Find a folder");
    this.search.setAttribute("role", "combobox");
    this.search.setAttribute("aria-expanded", "true");
    this.search.setAttribute("aria-autocomplete", "list");
    this.list = document.createElement("ul");
    this.list.className = "picker-list";
    this.list.id = `folder-list-${n}`;
    this.list.setAttribute("role", "listbox");
    this.list.setAttribute("aria-label", "Folders");
    this.search.setAttribute("aria-controls", this.list.id);
    this.empty = document.createElement("p");
    this.empty.className = "picker-empty";
    this.empty.hidden = true;
    this.newButton = document.createElement("button");
    this.newButton.type = "button";
    this.newButton.className = "picker-new";
    this.newButton.textContent = "New subfolder…";
    this.pop.append(this.search, this.list, this.empty, this.newButton, this.buildCreate(n));
    host.replaceChildren(this.button, this.pop);
    this.newButton.addEventListener("click", () => this.showCreate(true));

    this.button.addEventListener("click", () => (this.isOpen ? this.close() : this.open()));
    this.button.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.open();
      }
    });
    this.search.addEventListener("input", () => {
      this.active = -1; // the highlight starts again on the first match, never on whatever row now has that index
      this.draw();
    });
    this.search.addEventListener("keydown", (e) => this.onKey(e));
    // mousedown keeps the focus in the search field; the click picks
    this.list.addEventListener("mousedown", (e) => e.preventDefault());
    this.list.addEventListener("click", (e) => {
      const li = e.target.closest("[role=option]");
      if (li) this.pick(Number(li.dataset.index));
    });
    this.list.addEventListener("pointermove", (e) => {
      const li = e.target.closest("[role=option]");
      if (li && li.getAttribute("aria-disabled") !== "true") this.setActive(Number(li.dataset.index), false);
    });
    host.addEventListener("focusout", (e) => {
      if (this.isOpen && !host.contains(e.relatedTarget)) this.close(false);
    });
  }

  /** The listed folders; new folders named here but not saved yet are kept. */
  setFolders(folders) {
    const fresh = folderRows(folders);
    const pending = this.rows.filter((r) => r.isNew && !fresh.some((f) => f.path === r.path));
    this.rows = [...fresh, ...pending].sort(byPath);
    if (this.isOpen) this.draw();
  }

  /** Show `folder` (no onChange). */
  setValue(folder) {
    this.value = folder ?? "";
    const parts = this.value ? this.value.split("/") : [];
    this.text.replaceChildren(...parts.flatMap((p, i) => {
      const seg = document.createElement("span");
      seg.textContent = p;
      if (i < parts.length - 1) {
        const sep = document.createElement("span");
        sep.className = "picker-sep";
        sep.setAttribute("aria-hidden", "true");
        sep.textContent = "/";
        return [seg, sep];
      }
      return [seg];
    }));
    if (!parts.length) this.text.textContent = "Choose a folder";
    this.button.classList.toggle("is-empty", !parts.length);
    const row = this.rows.find((r) => r.path === this.value);
    this.button.classList.toggle("is-external", Boolean(row?.external));
    this.button.setAttribute("aria-label", parts.length ? `Folder: ${this.value}${row?.external ? ", external" : ""}` : "Folder: none chosen");
  }

  /** A locked picker cannot change (`reason` is shown as its tooltip). */
  lock(reason) {
    this.locked = reason;
    this.button.disabled = Boolean(reason);
    this.button.title = reason;
  }

  get isOpen() {
    return !this.pop.hidden;
  }

  open() {
    if (this.locked) return;
    this.showCreate(false, false);
    this.search.value = "";
    this.pop.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.draw();
    this.place();
    const cur = this.shown.findIndex((r) => r.path === this.value);
    this.setActive(cur >= 0 ? cur : this.firstPickable(0, 1));
    this.search.focus();
  }

  /** Open upwards when the popover would run past the bottom of the pane (or window) it lives in. */
  place() {
    this.pop.classList.remove("up");
    const box = this.host.closest(".details-pane, dialog")?.getBoundingClientRect();
    const bottom = Math.min(window.innerHeight, box ? box.bottom - 72 : Infinity); // 72: the pinned action row
    const top = Math.max(0, box ? box.top : 0);
    const trigger = this.button.getBoundingClientRect();
    const h = this.pop.getBoundingClientRect().height;
    if (trigger.bottom + h + 8 > bottom && trigger.top - h - 8 > top) this.pop.classList.add("up");
  }

  close(refocus = true) {
    if (!this.isOpen) return;
    this.pop.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
    this.search.removeAttribute("aria-activedescendant");
    if (refocus) this.button.focus();
  }

  pickable(row) {
    return row && row.selectable && !this.refuse(row);
  }

  draw() {
    this.shown = filterRows(this.rows, this.search.value);
    this.list.replaceChildren(...this.shown.map((r, i) => {
      const li = document.createElement("li");
      li.id = `${this.list.id}-${i}`;
      li.dataset.index = String(i);
      li.setAttribute("role", "option");
      li.className = "picker-row";
      li.style.setProperty("--depth", String(r.depth));
      const why = r.selectable ? this.refuse(r) : "";
      li.setAttribute("aria-selected", String(r.path === this.value));
      if (!r.selectable || why) li.setAttribute("aria-disabled", "true");
      if (!r.selectable) li.classList.add("is-group");
      const name = document.createElement("span");
      name.className = "picker-name";
      name.textContent = r.name;
      li.append(name);
      if (r.external || r.isNew || why) {
        const hint = document.createElement("span");
        hint.className = "picker-hint";
        hint.textContent = r.external ? "external" : r.isNew ? "new" : "";
        if (why) li.title = why;
        li.append(hint);
      }
      return li;
    }));
    const none = !this.shown.length;
    this.empty.hidden = !none;
    this.empty.textContent = none ? `No folder matches “${this.search.value.trim()}”.` : "";
    this.setActive(this.pickable(this.shown[this.active]) ? this.active : this.firstPickable(0, 1));
  }

  // --- new subfolder ------------------------------------------------------------------------------
  buildCreate(n) {
    const box = document.createElement("div");
    box.className = "picker-create";
    box.hidden = true;
    const inLabel = document.createElement("span");
    inLabel.className = "picker-create-label";
    inLabel.id = `folder-parent-label-${n}`;
    inLabel.textContent = "Inside";
    const parentHost = document.createElement("div");
    parentHost.className = "field-menu";
    this.parentMenu = new SelectMenu(parentHost, {
      name: "Parent folder", options: [], onChange: () => this.checkCreate(),
    });
    this.parentMenu.button.setAttribute("aria-labelledby", inLabel.id);
    const nameLabel = document.createElement("label");
    nameLabel.className = "picker-create-label";
    nameLabel.htmlFor = `folder-new-${n}`;
    nameLabel.textContent = "Name";
    this.nameInput = document.createElement("input");
    this.nameInput.type = "text";
    this.nameInput.id = `folder-new-${n}`;
    this.nameInput.className = "picker-new-name";
    this.nameInput.autocomplete = "off";
    this.nameInput.spellcheck = false;
    this.createMsg = document.createElement("p");
    this.createMsg.className = "picker-create-msg";
    this.createMsg.id = `folder-new-msg-${n}`;
    this.createMsg.setAttribute("aria-live", "polite");
    this.nameInput.setAttribute("aria-describedby", this.createMsg.id);
    const row = document.createElement("div");
    row.className = "picker-create-actions";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "quiet";
    back.textContent = "Back to the list";
    back.addEventListener("click", () => this.showCreate(false));
    this.createButton = document.createElement("button");
    this.createButton.type = "button";
    this.createButton.className = "primary";
    this.createButton.textContent = "Use this folder";
    this.createButton.addEventListener("click", () => this.create());
    row.append(back, this.createButton);
    box.append(inLabel, parentHost, nameLabel, this.nameInput, this.createMsg, row);
    this.nameInput.addEventListener("input", (e) => {
      e.stopPropagation(); // not a form field of its own: the form sees the folder through onChange
      this.checkCreate();
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target === this.nameInput) {
        e.preventDefault(); // never submits the surrounding form
        this.create();
      } else if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        e.stopPropagation();
        this.close();
      }
    });
    this.createBox = box;
    return box;
  }

  /** Switch the popover between the list and the new-subfolder fields. The focus moves before anything is
   *  hidden: hiding the focused element would blur it, and the picker closes when the focus leaves it. */
  showCreate(on, focus = true) {
    if (!on) {
      this.search.hidden = false;
      this.list.hidden = false;
      this.newButton.hidden = false;
      if (focus) this.search.focus();
      this.createBox.hidden = true;
      return;
    }
    const parents = parentRows(this.rows);
    this.parentMenu.setOptions(parents.map((r) => ({ value: r.path, label: r.path })));
    const cur = this.value;
    const start = parents.find((r) => r.path === cur) ?? parents.find((r) => r.path === cur.split("/")[0]) ?? parents[0];
    this.parentMenu.setValue(start?.path ?? "");
    this.nameInput.value = "";
    this.checkCreate();
    this.createBox.hidden = false;
    if (focus) this.nameInput.focus();
    this.search.hidden = true;
    this.list.hidden = true;
    this.newButton.hidden = true;
    this.empty.hidden = true;
  }

  checkCreate() {
    const typed = this.nameInput.value.trim();
    const { path, error } = newFolderPath(this.parentMenu.value, this.nameInput.value, this.rows);
    this.createMsg.textContent = !typed ? "Lowercase words joined by dashes, such as expressionism."
      : error || `Creates ${path} when you save.`;
    this.createMsg.classList.toggle("is-error", Boolean(typed && error));
    this.nameInput.setAttribute("aria-invalid", String(Boolean(typed && error)));
    this.createButton.disabled = Boolean(error);
    return { path, error };
  }

  create() {
    const { path, error } = this.checkCreate();
    if (error) return;
    const parts = path.split("/");
    this.rows = [...this.rows, { path, name: parts.at(-1), depth: parts.length - 1, selectable: true, external: false,
                                 isNew: true }].sort(byPath);
    this.close();
    this.setValue(path);
    this.onChange(path);
  }

  /** First pickable row from `from` going `dir` (±1), or -1. */
  firstPickable(from, dir) {
    for (let i = from; i >= 0 && i < this.shown.length; i += dir) if (this.pickable(this.shown[i])) return i;
    return -1;
  }

  setActive(i, scroll = true) {
    this.active = i;
    for (const li of this.list.children) li.classList.toggle("active", Number(li.dataset.index) === i);
    const li = this.list.children[i];
    if (li) {
      this.search.setAttribute("aria-activedescendant", li.id);
      if (scroll) li.scrollIntoView({ block: "nearest" });
    } else {
      this.search.removeAttribute("aria-activedescendant");
    }
  }

  pick(i) {
    const row = this.shown[i];
    if (!this.pickable(row)) return;
    this.close();
    if (row.path === this.value) return;
    this.setValue(row.path);
    this.onChange(row.path);
  }

  onKey(e) {
    const n = this.shown.length;
    let next = null;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const dir = e.key === "ArrowDown" ? 1 : -1;
      const found = this.firstPickable(moveIndex(this.active, dir, n), dir);
      next = found >= 0 ? found : this.active;
    } else if (e.key === "Home") next = this.firstPickable(0, 1);
    else if (e.key === "End") next = this.firstPickable(n - 1, -1);
    else if (e.key === "Enter") {
      this.pick(this.active);
    } else if (e.key === "Escape") {
      this.close();
    } else if (e.key === "Tab") {
      this.close(false);
      return;
    } else return;
    e.preventDefault();
    e.stopPropagation();
    if (next !== null) this.setActive(next);
  }
}
