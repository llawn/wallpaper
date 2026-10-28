// Themed replacements for <select> and <datalist>: a popover listbox menu (SelectMenu), a multi-select menu with a
// search field (MultiSelectMenu, the tag filter) and a suggestion list for a text input (Suggest). ARIA listbox /
// combobox semantics, arrows, Home/End, Enter, Esc and type-ahead.
// The helpers at the top are pure and tested under node.

/** Index of the next option whose label starts with `buffer` (case-insensitive), or -1.
 * A single repeated letter cycles from the item after `current`; a longer buffer refines from `current` itself. */
export function typeAhead(options, buffer, current) {
  const n = options.length;
  if (!n || !buffer) return -1;
  const b = buffer.toLowerCase();
  const repeated = [...b].every((c) => c === b[0]);
  const needle = repeated ? b[0] : b;
  const from = current < 0 ? 0 : repeated ? current + 1 : current;
  for (let k = 0; k < n; k++) {
    const i = (from + k) % n;
    if (String(options[i].label).toLowerCase().startsWith(needle)) return i;
  }
  return -1;
}

/** Arrow-key movement, clamped to [0, n - 1]; with nothing active (-1) the first move lands on 0. */
export function moveIndex(i, delta, n) {
  if (n <= 0) return -1;
  if (i < 0) return 0;
  return Math.max(0, Math.min(n - 1, i + delta));
}

/** Strings matching `text`: prefix matches first (shortest first), then the other substring matches in order. */
export function filterOptions(list, text) {
  const t = text.trim().toLowerCase();
  if (!t) return [...list];
  const prefix = [], inner = [];
  for (const s of list) {
    const l = s.toLowerCase();
    if (l.startsWith(t)) prefix.push(s);
    else if (l.includes(t)) inner.push(s);
  }
  prefix.sort((a, b) => a.length - b.length);
  return [...prefix, ...inner];
}

/**
 * Options of the tag filter menu: the `selected` tags first (in the order they were picked, even one no longer in
 * `rows`), then the other tags of `rows` ([{tag, count}], most used first), each with its count as the hint; only
 * those containing `text` when there is some.
 */
export function tagOptions(rows, selected, text = "") {
  const counts = new Map(rows.map((r) => [r.tag, r.count]));
  const opt = (tag) => ({ value: tag, label: tag, hint: counts.has(tag) ? String(counts.get(tag)) : "" });
  const out = [...selected.map(opt), ...rows.filter((r) => !selected.includes(r.tag)).map((r) => opt(r.tag))];
  const t = text.trim().toLowerCase();
  return t ? out.filter((o) => o.value.includes(t)) : out;
}

/** Icon span for a menu trigger (drawn by style.css, shown when the toolbar collapses its labels). */
function iconNode(name) {
  const span = document.createElement("span");
  span.className = `menu-icon icon-${name}`;
  span.setAttribute("aria-hidden", "true");
  return span;
}

let uid = 0;
const nextId = (p) => `${p}-${++uid}`;

function optionNode(id, opt, selected) {
  const li = document.createElement("li");
  li.id = id;
  li.setAttribute("role", "option");
  li.setAttribute("aria-selected", String(selected));
  li.className = "menu-option";
  const label = document.createElement("span");
  label.className = "menu-option-label";
  label.textContent = opt.label;
  li.append(label);
  if (opt.hint) {
    const hint = document.createElement("span");
    hint.className = "menu-option-hint";
    hint.textContent = opt.hint;
    li.append(hint);
  }
  return li;
}

/** Keep a popover inside the window: open upwards / align right when there is no room. */
function place(list) {
  list.classList.remove("up", "right");
  const r = list.getBoundingClientRect();
  if (r.bottom > window.innerHeight - 8 && r.top - r.height > 8) list.classList.add("up");
  if (r.right > window.innerWidth - 8) list.classList.add("right");
}

/**
 * Button + popover listbox. Options: [{value, label, hint?}]. The trigger shows `name` while the value is the
 * empty "any" value (or `placeholder`) and the chosen label otherwise; it is marked set (accent) when the value differs from
 * `defaultValue`.
 */
export class SelectMenu {
  constructor(host, { name, options, value = "", defaultValue = "", placeholder = name, icon = "", fixedText = "",
                      onChange }) {
    this.host = host;
    this.fixedText = fixedText;
    this.placeholder = placeholder;
    this.defaultValue = defaultValue;
    this.name = name;
    this.onChange = onChange;
    this.value = value;
    this.active = -1;
    this.buffer = "";
    this.bufferTimer = 0;
    host.classList.add("menu");
    this.button = document.createElement("button");
    this.button.type = "button";
    this.button.className = "menu-trigger";
    this.button.setAttribute("aria-haspopup", "listbox");
    this.button.setAttribute("aria-expanded", "false");
    this.text = document.createElement("span");
    this.text.className = "menu-trigger-text";
    if (icon) this.button.append(iconNode(icon));
    this.button.append(this.text);
    this.list = document.createElement("ul");
    this.list.className = "menu-list";
    this.list.id = nextId("menu");
    this.list.setAttribute("role", "listbox");
    this.list.setAttribute("aria-label", name);
    this.list.tabIndex = -1;
    this.list.hidden = true;
    this.button.setAttribute("aria-controls", this.list.id);
    host.append(this.button, this.list);
    this.setOptions(options);
    this.button.addEventListener("click", () => (this.isOpen ? this.close() : this.open()));
    this.button.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        this.open(e.key === "ArrowUp" ? "last" : "selected");
      }
    });
    this.list.addEventListener("keydown", (e) => this.onKey(e));
    this.list.addEventListener("click", (e) => {
      const li = e.target.closest("[role=option]");
      if (li) this.choose(Number(li.dataset.index));
    });
    this.list.addEventListener("pointermove", (e) => {
      const li = e.target.closest("[role=option]");
      if (li) this.setActive(Number(li.dataset.index), false);
    });
    this.list.addEventListener("focusout", (e) => {
      if (!host.contains(e.relatedTarget)) this.close(false);
    });
  }

  setOptions(options) {
    this.options = options;
    this.list.replaceChildren(...options.map((o, i) => {
      const li = optionNode(`${this.list.id}-${i}`, o, o.value === this.value);
      li.dataset.index = String(i);
      return li;
    }));
    this.sync();
  }

  setValue(value) {
    this.value = value;
    for (const li of this.list.children) {
      li.setAttribute("aria-selected", String(this.options[Number(li.dataset.index)].value === value));
    }
    this.sync();
  }

  sync() {
    const opt = this.options.find((o) => o.value === this.value);
    const isAny = this.value === "" || !opt;
    this.text.textContent = this.fixedText || (isAny ? this.placeholder : opt.label);
    this.button.classList.toggle("is-set", Boolean(opt) && this.value !== this.defaultValue && !this.fixedText);
    const label = isAny ? `${this.name}: any` : `${this.name}: ${opt.label}`;
    this.button.setAttribute("aria-label", label);
    this.button.title = label;
  }

  get isOpen() {
    return !this.list.hidden;
  }

  open(where = "selected") {
    this.list.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    place(this.list);
    const sel = this.options.findIndex((o) => o.value === this.value);
    this.setActive(where === "last" ? this.options.length - 1 : Math.max(0, sel));
    this.list.focus();
  }

  close(refocus = true) {
    if (!this.isOpen) return;
    this.list.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
    if (refocus) this.button.focus();
  }

  setActive(i, scroll = true) {
    this.active = i;
    for (const li of this.list.children) li.classList.toggle("active", Number(li.dataset.index) === i);
    const li = this.list.children[i];
    if (li) {
      this.list.setAttribute("aria-activedescendant", li.id);
      if (scroll) li.scrollIntoView({ block: "nearest" });
    }
  }

  choose(i) {
    const opt = this.options[i];
    this.close();
    if (!opt || opt.value === this.value) return;
    this.setValue(opt.value);
    this.onChange(opt.value);
  }

  onKey(e) {
    const n = this.options.length;
    const moves = { ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8 };
    if (e.key in moves) this.setActive(moveIndex(this.active, moves[e.key], n));
    else if (e.key === "Home") this.setActive(0);
    else if (e.key === "End") this.setActive(n - 1);
    else if (e.key === "Enter" || e.key === " ") this.choose(this.active);
    else if (e.key === "Escape") this.close();
    else if (e.key === "Tab") return this.close(false);
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      clearTimeout(this.bufferTimer);
      this.buffer += e.key;
      this.bufferTimer = setTimeout(() => (this.buffer = ""), 700);
      const i = typeAhead(this.options, this.buffer, this.active);
      if (i >= 0) this.setActive(i);
    } else return;
    e.preventDefault();
    e.stopPropagation();
  }
}

/**
 * Button + popover with a search field over a multi-selectable listbox (the tag filter). `load()` returns the rows
 * ([{tag, count}], most used first) each time the menu opens; `label(values)` names the button; `onChange(values)`
 * runs on every toggle. The focus stays in the search field: arrows move, Space or Enter toggles, Esc closes.
 */
export class MultiSelectMenu {
  constructor(host, { name, label, icon = "", load, onChange, placeholder = `Find a ${name.toLowerCase().replace(/s$/, "")}`,
                      emptyText = `No ${name.toLowerCase()} yet` }) {
    Object.assign(this, { host, name, label, load, onChange, emptyText });
    this.values = [];
    this.rows = [];
    this.options = [];
    this.order = [];
    this.active = -1;
    this.seq = 0;
    host.classList.add("menu");
    this.button = document.createElement("button");
    this.button.type = "button";
    this.button.className = "menu-trigger";
    this.button.setAttribute("aria-haspopup", "listbox");
    this.button.setAttribute("aria-expanded", "false");
    if (icon) this.button.append(iconNode(icon));
    this.text = document.createElement("span");
    this.text.className = "menu-trigger-text";
    this.badge = document.createElement("span");
    this.badge.className = "menu-badge";
    this.badge.setAttribute("aria-hidden", "true");
    this.button.append(this.text, this.badge);

    this.pop = document.createElement("div");
    this.pop.className = "menu-list menu-pop";
    this.pop.hidden = true;
    this.search = document.createElement("input");
    this.search.type = "search";
    this.search.className = "menu-search";
    this.search.placeholder = placeholder;
    this.search.autocomplete = "off";
    this.search.spellcheck = false;
    this.search.setAttribute("aria-label", placeholder);
    this.list = document.createElement("ul");
    this.list.className = "menu-options";
    this.list.id = nextId("multi");
    this.list.setAttribute("role", "listbox");
    this.list.setAttribute("aria-multiselectable", "true");
    this.list.setAttribute("aria-label", name);
    this.search.setAttribute("role", "combobox");
    this.search.setAttribute("aria-autocomplete", "list");
    this.search.setAttribute("aria-expanded", "false");
    this.search.setAttribute("aria-controls", this.list.id);
    this.empty = document.createElement("p");
    this.empty.className = "menu-empty";
    this.empty.hidden = true;
    this.pop.append(this.search, this.list, this.empty);
    this.button.setAttribute("aria-controls", this.list.id);
    host.append(this.button, this.pop);
    this.sync();

    this.button.addEventListener("click", () => (this.isOpen ? this.close() : this.open()));
    this.button.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        this.open();
      }
    });
    this.search.addEventListener("input", () => this.filter());
    this.search.addEventListener("keydown", (e) => this.onKey(e));
    // mousedown, not click: the focus stays in the search field
    this.list.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const li = e.target.closest("[role=option]");
      if (li) this.toggle(Number(li.dataset.index));
    });
    this.list.addEventListener("pointermove", (e) => {
      const li = e.target.closest("[role=option]");
      if (li) this.setActive(Number(li.dataset.index), false);
    });
    this.pop.addEventListener("focusout", (e) => {
      if (!host.contains(e.relatedTarget)) this.close(false);
    });
  }

  get isOpen() {
    return !this.pop.hidden;
  }

  /** The selection changed outside the menu (a chip removed, a tag added from the details pane). */
  setValues(values) {
    this.values = [...values];
    this.sync();
    if (this.isOpen) this.markSelected();
  }

  sync() {
    const n = this.values.length;
    this.text.textContent = this.label(this.values);
    this.badge.textContent = n ? String(n) : "";
    this.button.classList.toggle("is-set", n > 0);
    const label = n ? `${this.name}: ${this.values.join(", ")}` : `${this.name}: any`;
    this.button.setAttribute("aria-label", label);
    this.button.title = label;
  }

  async open() {
    this.pop.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.search.setAttribute("aria-expanded", "true");
    this.search.value = "";
    this.order = [...this.values]; // the picked tags lead while the menu is open; toggling does not reorder
    this.render();
    place(this.pop);
    this.search.focus();
    const token = ++this.seq;
    try {
      const rows = await this.load();
      if (token !== this.seq || !this.isOpen) return;
      this.rows = rows;
    } catch {
      if (token !== this.seq) return;
    }
    this.render();
  }

  close(refocus = true) {
    if (!this.isOpen) return;
    this.seq++;
    this.pop.hidden = true;
    this.active = -1;
    this.button.setAttribute("aria-expanded", "false");
    this.search.setAttribute("aria-expanded", "false");
    this.search.removeAttribute("aria-activedescendant");
    if (refocus) this.button.focus();
  }

  filter() {
    this.render();
    this.setActive(this.options.length ? 0 : -1);
  }

  render() {
    this.options = tagOptions(this.rows, this.order, this.search.value);
    this.list.replaceChildren(...this.options.map((o, i) => {
      const li = optionNode(`${this.list.id}-${i}`, o, this.values.includes(o.value));
      li.dataset.index = String(i);
      li.dataset.value = o.value;
      return li;
    }));
    this.empty.hidden = this.options.length > 0;
    this.empty.textContent = this.search.value.trim() ? `No ${this.name.toLowerCase()} match “${this.search.value.trim()}”`
      : this.emptyText;
    if (this.active >= this.options.length) this.active = this.options.length - 1;
    this.setActive(this.active, false);
  }

  markSelected() {
    for (const li of this.list.children) li.setAttribute("aria-selected", String(this.values.includes(li.dataset.value)));
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

  toggle(i) {
    const opt = this.options[i];
    if (!opt) return;
    this.setActive(i, false);
    const on = !this.values.includes(opt.value);
    this.values = on ? [...this.values, opt.value] : this.values.filter((v) => v !== opt.value);
    this.sync();
    this.markSelected();
    this.onChange([...this.values]);
  }

  onKey(e) {
    const n = this.options.length;
    const moves = { ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8 };
    if (e.key in moves) this.setActive(moveIndex(this.active, moves[e.key], n));
    else if (e.key === "Enter") this.toggle(this.active >= 0 ? this.active : n === 1 ? 0 : -1);
    else if (e.key === " " && this.active >= 0) this.toggle(this.active); // tags have no spaces
    else if (e.key === "Escape") this.close();
    else if (e.key === "Tab") return this.close(false);
    else return;
    e.preventDefault();
    e.stopPropagation();
  }
}

/**
 * Suggestion popover for a text input (combobox). `source(text)` returns [{value, label, hint?}] (may be async);
 * `onPick(option)` runs when one is chosen with Enter or a click. Out-of-order answers are dropped.
 */
export class Suggest {
  constructor(input, { source, onPick, delay = 100 }) {
    this.input = input;
    this.source = source;
    this.onPick = onPick;
    this.options = [];
    this.active = -1;
    this.seq = 0;
    this.timer = 0;
    this.list = document.createElement("ul");
    this.list.className = "menu-list suggest-list";
    this.list.id = nextId("suggest");
    this.list.setAttribute("role", "listbox");
    this.list.hidden = true;
    input.after(this.list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", this.list.id);
    input.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.update(), delay);
    });
    input.addEventListener("focus", () => this.update());
    input.addEventListener("blur", () => this.close());
    input.addEventListener("keydown", (e) => this.onKey(e));
    // mousedown, not click: keep the focus in the input
    this.list.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const li = e.target.closest("[role=option]");
      if (li) this.pick(Number(li.dataset.index));
    });
  }

  async update() {
    const token = ++this.seq;
    let options;
    try {
      options = await this.source(this.input.value);
    } catch {
      options = [];
    }
    if (token !== this.seq || document.activeElement !== this.input) return;
    this.show(options);
  }

  show(options) {
    this.options = options;
    this.active = -1;
    this.input.removeAttribute("aria-activedescendant");
    this.list.replaceChildren(...options.map((o, i) => {
      const li = optionNode(`${this.list.id}-${i}`, o, false);
      li.dataset.index = String(i);
      return li;
    }));
    this.list.hidden = !options.length;
    this.input.setAttribute("aria-expanded", String(Boolean(options.length)));
    if (options.length) place(this.list);
  }

  close() {
    this.seq++;
    this.list.hidden = true;
    this.active = -1;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }

  get isOpen() {
    return !this.list.hidden;
  }

  setActive(i) {
    this.active = i;
    for (const li of this.list.children) li.classList.toggle("active", Number(li.dataset.index) === i);
    const li = this.list.children[i];
    if (li) {
      this.input.setAttribute("aria-activedescendant", li.id);
      li.scrollIntoView({ block: "nearest" });
    }
  }

  pick(i) {
    const opt = this.options[i];
    this.close();
    if (opt) this.onPick(opt);
  }

  onKey(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!this.isOpen) return this.update();
      this.setActive(moveIndex(this.active, e.key === "ArrowDown" ? 1 : -1, this.options.length));
    } else if (e.key === "Enter" && this.isOpen && this.active >= 0) {
      e.preventDefault();
      e.stopPropagation();
      this.pick(this.active);
    } else if (e.key === "Escape" && this.isOpen) {
      e.preventDefault();
      e.stopPropagation();
      this.close();
    } else if (e.key === "Tab") {
      this.close();
    }
  }
}
