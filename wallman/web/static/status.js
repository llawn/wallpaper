// Status bar (git changes in the repo and submodules, rule counts) and toast notifications.

import { api } from "./api.js";
import { legendRows } from "./gallery.js";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Short message at the bottom right; errors stay longer and are announced assertively. */
export function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "×";
  close.addEventListener("click", () => el.remove());
  el.append(text, close);
  host.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 12000 : 4000);
}

/**
 * A toast with a question and buttons (non-modal): resolves with the chosen value, or "dismiss" when closed or after
 * `timeout` ms. The first button gets the focus when `focus` is true.
 * @param {string} message
 * @param {{value: string, label: string, kind?: string}[]} buttons
 */
export function offer(message, buttons, { timeout = 20000, focus = false } = {}) {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast offer";
  el.setAttribute("role", "alertdialog"); // announced when it appears; Esc dismisses it while it has the focus
  const text = document.createElement("span");
  text.className = "offer-text";
  text.id = `offer-${Date.now()}`;
  text.textContent = message;
  el.setAttribute("aria-labelledby", text.id);
  const row = document.createElement("span");
  row.className = "offer-buttons";
  el.append(text, row);
  host.append(el);
  return new Promise((resolve) => {
    const done = (value) => {
      clearTimeout(timer);
      el.remove();
      resolve(value);
    };
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `offer-btn ${b.kind ?? ""}`.trim();
      btn.dataset.value = b.value;
      btn.textContent = b.label;
      btn.addEventListener("click", () => done(b.value));
      row.append(btn);
    }
    el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        done("dismiss");
      }
    });
    const timer = setTimeout(() => done("dismiss"), timeout);
    if (focus) row.firstChild?.focus();
  });
}

export function toastError(err) {
  toast(err?.message || String(err), "error");
}

// porcelain v1 codes, whitespace-trimmed by the server (so "M " and " M" both arrive as "M")
const STATUS_WORDS = {
  A: "added",
  M: "modified",
  D: "deleted",
  R: "renamed",
  C: "copied",
  U: "conflict",
  "??": "untracked",
  "!!": "ignored",
};

function describe(code) {
  const c = code.trim();
  return STATUS_WORDS[c] ?? STATUS_WORDS[c[0]] ?? STATUS_WORDS[c[1]] ?? c;
}

export class StatusBar {
  constructor() {
    this.toggle = document.getElementById("status-toggle");
    this.summary = document.getElementById("status-summary");
    this.rules = document.getElementById("status-rules");
    this.list = document.getElementById("status-list");
    this.notes = document.getElementById("status-notes");
    this.toggle.addEventListener("click", () => {
      const open = this.toggle.getAttribute("aria-expanded") !== "true";
      this.toggle.setAttribute("aria-expanded", String(open));
      this.list.hidden = !open;
    });
  }

  /** Fetch /api/status and draw it; returns the data (theme_mtime is used by the theme watcher) or null. */
  async refresh({ quiet = false } = {}) {
    let data;
    try {
      data = await api.status();
    } catch (err) {
      this.summary.textContent = "Git status unavailable";
      if (!quiet) toastError(err);
      return null;
    }
    this.showNotes(data);
    const { changes, errors, warnings } = data;
    this.summary.textContent = changes.length
      ? `${plural(changes.length, "change")} to commit`
      : "Working tree clean";
    this.rules.replaceChildren();
    for (const [n, word, cls] of [[errors, "error", "error"], [warnings, "warning", "warning"]]) {
      if (!n) continue;
      const s = document.createElement("span");
      s.className = `rule-count ${cls}`;
      s.textContent = plural(n, word);
      this.rules.append(s);
    }
    if (!errors && !warnings) this.rules.textContent = "All rules pass";
    const key = JSON.stringify(changes);
    if (key !== this.lastChanges) {  // polled every few seconds: keep an open list stable when nothing changed
      this.lastChanges = key;
      this.renderList(changes);
    }
    return data;
  }

  /** Theme config warnings and the last git status failure (both otherwise fall back silently). Also fed by the
   *  cheap 5 s theme poll. */
  showNotes({ theme_warnings: themeWarnings = [], git_error: gitError = null } = {}) {
    if (!this.notes) return;
    const notes = [...(themeWarnings || []).map((w) => ["warning", `Theme: ${w}`])];
    if (gitError) notes.push(["error", `Git: ${gitError}`]);
    const key = JSON.stringify(notes);
    if (key === this.lastNotes) return;
    this.lastNotes = key;
    this.notes.replaceChildren(...notes.map(([cls, text]) => {
      const s = document.createElement("span");
      s.className = `status-note ${cls}`;
      s.textContent = text;
      s.title = text;
      return s;
    }));
  }

  renderList(changes) {
    this.list.replaceChildren();
    try {
      this.renderChanges(changes);
    } finally {
      this.list.append(...legend());
    }
  }

  renderChanges(changes) {
    if (!changes.length) {
      const p = document.createElement("p");
      p.textContent = "Nothing staged or modified. Commit from the terminal when you are ready.";
      this.list.append(p);
      return;
    }
    const groups = Map.groupBy(changes, (c) => c.repo || ".");
    for (const [repo, rows] of groups) {
      const h = document.createElement("h3");
      h.textContent = repo === "." ? "Main repository" : `Submodule ${repo}`;
      const ul = document.createElement("ul");
      for (const c of rows) {
        const li = document.createElement("li");
        const code = document.createElement("span");
        code.className = `change ${describe(c.status)}`;
        code.textContent = describe(c.status);
        code.title = `git status ${c.status}`;
        const path = document.createElement("span");
        path.className = "path";
        path.textContent = c.path;
        li.append(code, path);
        ul.append(li);
      }
      this.list.append(h, ul);
    }
  }
}

/** What the marks on the tiles mean: [heading, list]. */
function legend() {
  const h = document.createElement("h3");
  h.textContent = "Marks on the tiles";
  const dl = document.createElement("dl");
  dl.className = "git-legend";
  for (const [glyph, state, tip] of legendRows()) {
    const dt = document.createElement("dt");
    dt.className = "git";
    dt.dataset.state = state;
    dt.textContent = glyph;
    const dd = document.createElement("dd");
    dd.textContent = tip;
    dl.append(dt, dd);
  }
  return [h, dl];
}
