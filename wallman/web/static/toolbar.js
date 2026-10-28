// Toolbar: search, tag filter menu (multi-select, AND) with its chips row under the toolbar, license / format / sort
// menus, year with suggestions, issues toggle. Owns the filter state and reports every change as one object. The
// toolbar stays on one row: the search field shrinks, then the menus show icons only, then Sort moves into "⋯".

import { api } from "./api.js";
import { MultiSelectMenu, SelectMenu, Suggest } from "./select-menu.js";
import { normaliseTag } from "./tag-input.js";

const $ = (id) => document.getElementById(id);

export const FORMATS = [
  ["32:9", "Super ultrawide"], ["21:9", "Ultrawide"], ["16:9", "Widescreen"], ["16:10", ""], ["3:2", ""],
  ["4:3", ""], ["5:4", ""], ["square", "Square"], ["portrait", "Portrait"],
];

/** The toolbar's collapse levels: 0 full labels, 1 icon menus, 2 Sort in the overflow menu as well. */
const MAX_LEVEL = 2;

/** `tags` with `raw` (slugged) added, or removed when it is already there. */
export function toggleTag(tags, raw) {
  const tag = normaliseTag(raw);
  if (!tag) return [...tags];
  return tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag];
}

/** `tags` with `raw` (slugged) added at the end unless already there. */
export function withTag(tags, raw) {
  const tag = normaliseTag(raw);
  return !tag || tags.includes(tag) ? [...tags] : [...tags, tag];
}

/** The Tags button: "Tags", or "Tags · N" with N tags picked. */
export const tagsLabel = (tags) => (tags.length ? `Tags · ${tags.length}` : "Tags");

/** First collapse level whose measured width `needs[level]` fits in `width`, else the last level. */
export function collapseLevel(width, needs) {
  const i = needs.findIndex((n) => n <= width);
  return i < 0 ? needs.length - 1 : i;
}

const SORTS = [
  { value: "name", label: "By name" },
  { value: "year", label: "By year" },
  { value: "added", label: "Recently added" },
];

export class Toolbar {
  /** @param {{licenses: {id: string, label: string}[], onChange: (filters: object) => void}} options */
  constructor({ licenses, onChange }) {
    this.onChange = onChange;
    this.filters = { q: "", tags: [], license: "", ratio: "", year: "", issues: false, sort: "name" };
    this.search = $("search");
    let timer = 0;
    this.search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.set({ q: this.search.value }), 160);
    });
    this.search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        clearTimeout(timer);
        this.set({ q: this.search.value });
      }
    });

    this.license = new SelectMenu($("menu-license"), {
      name: "License", icon: "license",
      options: [{ value: "", label: "Any license" },
                ...licenses.map((l) => ({ value: l.id, label: l.label, hint: l.label === l.id ? "" : l.id }))],
      onChange: (v) => {
        this.set({ license: v });
        this.measureSoon();
      },
    });
    this.format = new SelectMenu($("menu-format"), {
      name: "Format", icon: "format",
      options: [{ value: "", label: "Any format" },
                ...FORMATS.map(([v, hint]) => ({ value: v, label: v === "square" || v === "portrait" ? hint : v,
                                                 hint: v === "square" || v === "portrait" ? "" : hint }))],
      onChange: (v) => {
        this.set({ ratio: v });
        this.measureSoon();
      },
    });
    this.sort = new SelectMenu($("menu-sort"), {
      name: "Sort", icon: "sort", options: SORTS, value: "name", defaultValue: "name",
      onChange: (v) => this.setSort(v),
    });
    // the overflow menu: Sort again, shown instead of the Sort menu when the toolbar is narrow
    this.more = new SelectMenu($("menu-more"), {
      name: "Sort", fixedText: "⋯", options: SORTS, value: "name", defaultValue: "name",
      onChange: (v) => this.setSort(v),
    });

    this.issues = $("issues-toggle");
    this.issues.addEventListener("click", () => {
      const on = this.issues.getAttribute("aria-pressed") !== "true";
      this.issues.setAttribute("aria-pressed", String(on));
      this.set({ issues: on });
    });

    this.wireTags();
    this.wireYear();
    this.wireCollapse();
  }

  setSort(v) {
    this.sort.setValue(v);
    this.more.setValue(v);
    this.set({ sort: v });
    this.measureSoon();
  }

  wireTags() {
    this.tags = new MultiSelectMenu($("menu-tags"), {
      name: "Tags", icon: "tag", label: tagsLabel, placeholder: "Find a tag", emptyText: "No tags yet",
      load: () => api.tags("", 500),
      onChange: (tags) => this.applyTags(tags),
    });
    this.chips = $("tag-chips");
    this.strip = $("tag-strip");
    this.chips.addEventListener("click", (e) => {
      const b = e.target.closest("[data-tag]");
      if (!b) return;
      const i = this.filters.tags.indexOf(b.dataset.tag);
      this.removeTag(b.dataset.tag);
      // keep the keyboard in the strip: the next chip, else the previous one, else the Tags button
      const next = this.chips.querySelectorAll("[data-tag]")[Math.min(i, this.filters.tags.length - 1)];
      (next ?? this.tags.button).focus();
    });
    $("tag-clear").addEventListener("click", () => {
      this.applyTags([]);
      this.tags.button.focus();
    });
  }

  applyTags(tags) {
    this.set({ tags: [...tags] });
    this.tags.setValues(this.filters.tags);
    this.drawChips();
  }

  /** Add one tag to the filter (a tag chip in the details pane was clicked): AND with the tags already picked. */
  addTag(raw) {
    this.applyTags(withTag(this.filters.tags, raw));
  }

  removeTag(tag) {
    this.applyTags(this.filters.tags.filter((t) => t !== tag));
  }

  drawChips() {
    const tags = this.filters.tags;
    this.strip.hidden = !tags.length;
    $("tag-clear").hidden = tags.length < 2;
    this.chips.replaceChildren(...tags.map((t) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.dataset.tag = t;
      b.setAttribute("aria-label", `Remove tag ${t}`);
      b.title = `Remove tag ${t}`;
      const name = document.createElement("span");
      name.textContent = t;
      const x = document.createElement("span");
      x.className = "chip-x";
      x.setAttribute("aria-hidden", "true");
      b.append(name, x);
      li.append(b);
      return li;
    }));
    this.measureSoon();
  }

  // --- one row at every width: labels become icons, then Sort moves into the overflow menu -------------
  wireCollapse() {
    this.bar = $("toolbar");
    this.needs = null;
    this.level = -1;
    new ResizeObserver(() => this.fit()).observe(this.bar);
  }

  /** Labels changed (a filter picked): measure the widths again on the next frame. */
  measureSoon() {
    this.needs = null;
    if (this.fitFrame) return;
    this.fitFrame = requestAnimationFrame(() => {
      this.fitFrame = 0;
      this.fit();
    });
  }

  /** Width each collapse level needs, the search field counted at its minimum width. */
  measure() {
    const bar = this.bar;
    const needs = [];
    for (let level = 0; level <= MAX_LEVEL; level++) {
      bar.dataset.collapse = String(level);
      const cs = getComputedStyle(bar); // the last level also tightens padding, gaps and the search minimum
      const gap = parseFloat(cs.columnGap) || 0;
      const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
      const searchMin = parseFloat(getComputedStyle(this.search.closest(".search")).minWidth) || 0;
      const shown = [...bar.children].filter((c) => getComputedStyle(c).display !== "none");
      let w = pad + gap * Math.max(0, shown.length - 1);
      for (const c of shown) w += c.classList.contains("search") ? searchMin : c.getBoundingClientRect().width;
      needs.push(Math.ceil(w));
    }
    return needs;
  }

  fit() {
    this.needs ??= this.measure();
    const level = collapseLevel(this.bar.clientWidth, this.needs);
    this.bar.dataset.collapse = String(level);
    if (level !== this.level) {
      this.level = level;
      if (level < 2 && this.more.isOpen) this.more.close(false);
      if (level === 2 && this.sort.isOpen) this.sort.close(false);
    }
  }

  wireYear() {
    const input = $("year-input");
    this.yearInput = input;
    new Suggest(input, {
      source: async (text) => {
        const q = text.trim();
        if (!/^\d{1,4}$/.test(q)) return [];
        return (await api.yearSuggestions(q)).map((s) => ({ value: s, label: s }));
      },
      onPick: (opt) => {
        input.value = opt.value;
        this.applyYear();
      },
    });
    input.addEventListener("keydown", (e) => {
      if (!e.defaultPrevented && e.key === "Enter") this.applyYear();
    });
    input.addEventListener("change", () => this.applyYear());
    input.addEventListener("input", () => {
      input.removeAttribute("aria-invalid");
      if (!input.value.trim()) this.applyYear();
    });
  }

  applyYear() {
    this.set({ year: this.yearInput.value.trim() });
  }

  /** The list request failed because of the year text: mark the field. */
  yearRejected() {
    this.yearInput.setAttribute("aria-invalid", "true");
  }

  set(patch) {
    const next = { ...this.filters, ...patch };
    if (JSON.stringify(next) === JSON.stringify(this.filters)) return;
    this.filters = next;
    this.onChange(this.filters);
  }

  /** Reset every filter (after the folder changes the filters stay: this is for an explicit reset). */
  reset() {
    this.search.value = "";
    this.yearInput.value = "";
    this.license.setValue("");
    this.format.setValue("");
    this.issues.setAttribute("aria-pressed", "false");
    this.filters = { ...this.filters, q: "", tags: [], license: "", ratio: "", year: "", issues: false };
    this.tags.setValues([]);
    this.drawChips();
    this.onChange(this.filters);
  }

  get active() {
    const f = this.filters;
    return Boolean(f.q || f.tags.length || f.license || f.ratio || f.year || f.issues);
  }
}
