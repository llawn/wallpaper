// Tag field: chips plus a text input with autocomplete from /api/tags. Enter or a comma adds what is typed,
// Backspace in the empty input removes the last chip, leaving the field keeps a half-typed tag. Tags are
// normalised the way the server does it (wallman/domain/naming.py slug_tag), so a chip shows what is saved.

import { api } from "./api.js";
import { Suggest } from "./select-menu.js";

/** Lowercase kebab, accents dropped, at most 60 characters cut at a dash; "" when nothing letter-like is left. */
export function normaliseTag(text, maxLen = 60) {
  let s = String(text ?? "").normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase();
  s = s.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (s.length > maxLen) {
    let cut = s.slice(0, maxLen);
    if (s[maxLen] !== "-" && cut.includes("-")) cut = cut.slice(0, cut.lastIndexOf("-"));
    s = cut.replace(/^-+|-+$/g, "");
  }
  return s;
}

/** `tags` plus every comma-separated tag in `raw`: unique and sorted, like the server stores them. */
export function addTags(tags, raw) {
  const added = String(raw ?? "").split(",").map((t) => normaliseTag(t)).filter(Boolean);
  return [...new Set([...tags, ...added])].sort();
}

export const removeTag = (tags, tag) => tags.filter((t) => t !== tag);

export class TagInput {
  /**
   * @param {HTMLElement} host  emptied and filled with the chips and the input
   * @param {{id: string, onChange: (tags: string[]) => void}} options  onChange also runs while a tag is typed
   */
  constructor(host, { id, onChange }) {
    this.host = host;
    this.onChange = onChange;
    this.tags = [];
    host.classList.add("tag-field");
    this.chips = document.createElement("ul");
    this.chips.className = "chips";
    this.chips.setAttribute("aria-label", "Tags");
    const pop = document.createElement("span");
    pop.className = "field-pop";
    this.input = document.createElement("input");
    this.input.type = "text";
    this.input.id = id;
    this.input.autocomplete = "off";
    this.input.spellcheck = false;
    this.input.placeholder = "Add a tag";
    pop.append(this.input);
    host.replaceChildren(this.chips, pop);
    host.addEventListener("click", (e) => {
      if (e.target === host || e.target === this.chips) this.input.focus();
    });

    new Suggest(this.input, {
      source: async (text) => {
        const rows = await api.tags(normaliseTag(text), 10);
        return rows.filter((r) => !this.tags.includes(r.tag)).map((r) => ({ value: r.tag, label: r.tag, hint: String(r.count) }));
      },
      onPick: (opt) => this.add(opt.value),
    });
    this.input.addEventListener("keydown", (e) => {
      if (e.defaultPrevented) return; // the suggestion list took it
      if ((e.key === "Enter" || e.key === ",") && this.input.value.trim()) {
        e.preventDefault();
        this.add(this.input.value);
      } else if (e.key === "Enter") {
        e.preventDefault(); // an empty Enter never submits the form by accident
      } else if (e.key === "Backspace" && !this.input.value && this.tags.length) {
        e.preventDefault();
        this.set(this.tags.slice(0, -1));
      }
    });
    this.input.addEventListener("input", (e) => {
      e.stopPropagation(); // the form listens to tag changes through onChange, not to the half-typed text
      const v = this.input.value;
      if (v.includes(",")) {
        const cut = v.lastIndexOf(",");
        this.add(v.slice(0, cut));
        this.input.value = v.slice(cut + 1).trimStart();
      }
      this.onChange(this.tags); // a half-typed tag counts as a change (Save includes it)
    });
    this.input.addEventListener("change", (e) => {
      e.stopPropagation();
      if (this.input.value.trim()) this.add(this.input.value); // leaving the field keeps what was typed
    });
    this.chips.addEventListener("click", (e) => {
      const b = e.target.closest("[data-tag]");
      if (!b) return;
      this.set(removeTag(this.tags, b.dataset.tag));
      this.input.focus();
    });
  }

  add(raw) {
    this.input.value = "";
    this.set(addTags(this.tags, raw));
  }

  /** Replace the tags; `silent` skips onChange (filling the form). */
  set(tags, silent = false) {
    const changed = JSON.stringify(tags) !== JSON.stringify(this.tags);
    this.tags = [...tags];
    this.draw();
    if (changed && !silent) this.onChange(this.tags);
  }

  /** Tags including a half-typed one (so Save never drops it). */
  get value() {
    return this.input.value.trim() ? addTags(this.tags, this.input.value) : [...this.tags];
  }

  draw() {
    this.chips.replaceChildren(...this.tags.map((t) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.dataset.tag = t;
      b.setAttribute("aria-label", `Remove tag ${t}`);
      const name = document.createElement("span");
      name.textContent = t;
      const x = document.createElement("span");
      x.className = "chip-x";
      x.setAttribute("aria-hidden", "true");
      b.append(name, x);
      li.append(b);
      return li;
    }));
  }
}
