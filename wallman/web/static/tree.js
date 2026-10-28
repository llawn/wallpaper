// Folder tree: folders nested by path (built once from /api/folders), submodules marked, no counts.
// Clicking the active folder deselects it (back to "All wallpapers").

function build(folders) {
  const root = { name: "", path: "", external: false, children: new Map() };
  for (const f of folders) {
    let node = root;
    const parts = f.folder.split("/");
    parts.forEach((part, i) => {
      if (!node.children.has(part)) {
        node.children.set(part, { name: part, path: parts.slice(0, i + 1).join("/"), external: false, children: new Map() });
      }
      node = node.children.get(part);
    });
    node.external = f.external;
    node.medium = f.medium;
    node.movement = f.movement;
  }
  return root;
}

/** Folder to show after a click on `clicked` while `current` is shown: a second click on it goes back to all. */
export function nextFolder(current, clicked) {
  return clicked === current ? "" : clicked;
}

export class Tree {
  /** @param {(folder: string) => void} onSelect
   *  @param {{beforeSelect?: () => Promise<boolean>}} options  false keeps the current folder (an unsaved edit) */
  constructor(element, onSelect, { beforeSelect = () => true } = {}) {
    this.el = element;
    this.onSelect = onSelect;
    this.beforeSelect = beforeSelect;
    this.selected = "";
    this.collapsed = new Set();
    this.root = build([]);
    this.el.addEventListener("click", (e) => this.onClick(e));
  }

  render(folders) {
    this.root = build(folders);
    if (!this.rendered) {
      // deep branches start folded so the top level reads at a glance
      const fold = (node, depth) => {
        for (const child of node.children.values()) {
          if (child.children.size && depth >= 1) this.collapsed.add(child.path);
          fold(child, depth + 1);
        }
      };
      fold(this.root, 0);
      this.rendered = true;
    }
    this.draw();
  }

  draw() {
    const all = { ...this.root, name: "All wallpapers", children: new Map() };
    this.el.replaceChildren(this.item(all, true), ...[...this.root.children.values()].map((n) => this.item(n)));
  }

  item(node, isRoot = false) {
    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = "tree-row";
    if (node.children.size) {
      const open = !this.collapsed.has(node.path);
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "tree-toggle";
      toggle.dataset.toggle = node.path;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", `${open ? "Collapse" : "Expand"} ${node.name}`);
      row.append(toggle);
    } else {
      const pad = document.createElement("span");
      pad.className = "tree-toggle-pad";
      row.append(pad);
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tree-folder";
    btn.dataset.folder = isRoot ? "" : node.path;
    if (this.selected === btn.dataset.folder) btn.setAttribute("aria-current", "true");
    if (node.movement) btn.title = [node.medium, node.movement].filter(Boolean).join(", ");
    const name = document.createElement("span");
    name.className = "tree-name";
    name.textContent = node.name;
    btn.append(name);
    if (node.external) {
      const mark = document.createElement("span");
      mark.className = "tree-external";
      mark.textContent = "external";
      mark.title = "Git submodule: changes stay inside it";
      btn.append(mark);
    }
    row.append(btn);
    li.append(row);
    if (node.children.size && !this.collapsed.has(node.path)) {
      const ul = document.createElement("ul");
      ul.append(...[...node.children.values()].map((n) => this.item(n)));
      li.append(ul);
    }
    return li;
  }

  async onClick(e) {
    const toggle = e.target.closest("[data-toggle]");
    if (toggle) {
      const path = toggle.dataset.toggle;
      if (this.collapsed.has(path)) this.collapsed.delete(path);
      else this.collapsed.add(path);
      this.draw();
      this.el.querySelector(`[data-toggle="${CSS.escape(path)}"]`)?.focus();
      return;
    }
    const btn = e.target.closest("[data-folder]");
    if (!btn) return;
    const go = this.beforeSelect(); // true right away unless an unsaved edit has to be confirmed
    if (go !== true && !(await go)) return;
    this.select(nextFolder(this.selected, btn.dataset.folder));
    this.onSelect(this.selected);
  }

  /** Select `folder` and unfold its ancestors so it can be seen (the caller loads it). */
  reveal(folder) {
    const parts = folder.split("/");
    for (let i = 1; i < parts.length; i++) this.collapsed.delete(parts.slice(0, i).join("/"));
    this.selected = folder;
    this.draw();
    this.el.querySelector(`[data-folder="${CSS.escape(folder)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  select(folder) {
    this.selected = folder;
    for (const b of this.el.querySelectorAll("[data-folder]")) {
      if (b.dataset.folder === folder) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    }
  }
}
