// Gallery: the virtualised thumbnail grid (pages of 200 from /api/wallpapers, cached per query) and the selection.
// The grid element holds the keyboard focus (aria-activedescendant), so recycling tile nodes never drops it.

import { api, refKey, thumbSrc } from "./api.js";
import * as sel from "./selection.js";
import { PageCache, VirtualGrid, pagesFor } from "./virtual-grid.js";

export const PAGE = 200;
export const RANGE_CAP = 5000;

/** "1889" or "1880–1889" from the API's year (an integer or a [start, end] pair). */
export function yearLabel(year) {
  if (year == null) return "";
  if (Array.isArray(year)) return year[0] === year[1] ? String(year[0]) : `${year[0]}–${year[1]}`;
  return String(year);
}

/**
 * Git state -> glyph and the tooltip that explains it. A wallpaper is published when its entry is in the
 * wallpapers.toml of the git index (with its image when the license is shareable).
 */
export const GIT_GLYPHS = {
  committed: ["✓", "Published and committed"],
  metadata: ["◐", "Published (metadata only): its entry is committed, the image stays download-only"],
  staged: ["+", "Published, not committed yet"],
  "metadata-staged": ["◐", "Published (metadata only), not committed yet: the image stays download-only"],
  modified: ["~", "Published, but changed since it was staged, or its image is not in git"],
  untracked: ["?", "Not published: not in git"],
  ignored: ["⊘", "Kept out of git (download-only license)"],
  deleted: ["−", "Removed from git, not committed yet"],
  conflict: ["!", "Merge conflict"],
  pending: ["…", "Git state is being refreshed"],
};

/** [glyph, state, meaning] for the legend: the states a wallpaper can be in (not the transient ones). */
export function legendRows() {
  return Object.entries(GIT_GLYPHS)
    .filter(([state]) => state !== "pending" && state !== "ignored")
    .map(([state, [glyph, tip]]) => [glyph, state, tip]);
}

function describe(item) {
  const parts = [item.title, item.artist];
  if (item.year) parts.push(yearLabel(item.year));
  if (GIT_GLYPHS[item.git]) parts.push(GIT_GLYPHS[item.git][1]);
  if (item.issues) parts.push(`${item.issues} rule ${item.issues === 1 ? "issue" : "issues"}`);
  return parts.filter(Boolean).join(", ");
}

/** Everything a tile shows: it is repainted whenever this changes (an edited title, year, issue count …). */
export function tileSignature(item) {
  return JSON.stringify([item.folder, item.file, item.title, item.artist, item.year, item.issues, item.git, item.thumb,
                         item.shareable]);
}

/** Blank a tile: no image (aborts its download), no caption, glyph, issue dot or broken state. */
export function clearTile(node) {
  const p = node.parts;
  p.img.removeAttribute("src");
  p.frame.classList.remove("broken");
  p.title.textContent = "";
  p.artist.textContent = "";
  p.git.textContent = "";
  p.git.title = "";
  p.git.dataset.state = "";
  p.issue.hidden = true;
  p.issue.title = "";
  node.removeAttribute("aria-label");
  node.setAttribute("aria-selected", "false");
  delete node.dataset.sig;
  delete node.dataset.key;
}

function createTile() {
  const el = document.createElement("div");
  el.className = "tile";
  el.setAttribute("role", "option");
  const frame = document.createElement("div");
  frame.className = "frame";
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.draggable = false;
  const check = document.createElement("span");
  check.className = "check";
  check.setAttribute("aria-hidden", "true");
  const issue = document.createElement("span");
  issue.className = "issue";
  issue.hidden = true;
  frame.append(img, check, issue);
  const cap = document.createElement("div");
  cap.className = "cap";
  cap.setAttribute("aria-hidden", "true");
  const row = document.createElement("div");
  row.className = "cap-row";
  const title = document.createElement("span");
  title.className = "cap-title";
  const git = document.createElement("span");
  git.className = "git";
  row.append(title, git);
  const artist = document.createElement("span");
  artist.className = "cap-artist";
  cap.append(row, artist);
  el.append(frame, cap);
  el.parts = { frame, img, issue, title, git, artist };
  return el;
}

export class Gallery {
  /**
   * @param {{scroller: HTMLElement, canvas: HTMLElement, onSelection: (keys: Set<string>) => void,
   *          onOpen: (key: string) => void, onTotal: (total: number, known: boolean) => void,
   *          onError: (err: Error) => void, onNotice: (message: string) => void,
   *          canLeave?: () => boolean|Promise<boolean>}} options  `canLeave` runs before a person changes the selection
   *          (the details pane asks before dropping an unsaved edit); false keeps the selection as it is
   */
  constructor({ scroller, canvas, onSelection, onOpen, onTotal, onError, onNotice, canLeave = () => true }) {
    Object.assign(this, { scroller, canvas, onSelection, onOpen, onTotal, onError, onNotice, canLeave });
    this.state = sel.emptySelection();
    this.focus = 0;
    this.cache = new PageCache({
      fetchPage: (query, offset, limit) => api.list(query, offset, limit),
      pageSize: PAGE,
      maxPages: 40,
      onPage: () => {
        this.grid.setTotal(this.cache.total);
        this.grid.refresh();
        this.onTotal(this.cache.total, true);
      },
    });
    this.grid = new VirtualGrid(scroller, canvas, {
      create: createTile,
      render: (node, i) => this.renderTile(node, i),
      recycle: clearTile, // also aborts the thumbnail download of a tile that left the screen
      anchor: () => this.focus,
      onRelayout: () => this.syncActive(),
      // trailing 60 ms: a fast scroll through 10,000 items does not queue a page request for every window it crosses
      onWindow: (start, end) => {
        clearTimeout(this.loadTimer);
        this.loadTimer = setTimeout(() => this.load(start, end), 60);
      },
    });
    canvas.addEventListener("click", (e) => this.onClick(e));
    // the two clicks before a dblclick toggle the item off again: a double-click always ends with it selected alone
    canvas.addEventListener("dblclick", async (e) => {
      const i = this.indexOf(e.target);
      const item = i === null ? null : this.cache.get(i);
      if (!item) return;
      const go = this.allowed();
      if (go !== true && !(await go)) return;
      this.focus = i;
      this.state = sel.selectKeys(this.state, [refKey(item)], i);
      this.emit();
      this.onOpen(refKey(item));
    });
    canvas.addEventListener("error", (e) => {
      if (e.target instanceof HTMLImageElement && e.target.getAttribute("src")) e.target.parentElement.classList.add("broken");
    }, true);
    scroller.addEventListener("keydown", (e) => this.onKey(e));
    // the keyboard focus ring shows only while the keyboard drives the grid (not after a click)
    let pointer = false;
    scroller.addEventListener("pointerdown", () => {
      pointer = true;
      scroller.classList.remove("kbd");
    });
    scroller.addEventListener("focus", () => {
      if (!pointer) scroller.classList.add("kbd");
      pointer = false;
    });
    scroller.addEventListener("keydown", () => scroller.classList.add("kbd"), true);
  }

  // --- data -------------------------------------------------------------------------------------
  /** Show another query (folder or filters changed): selection cleared, back to the top. */
  setQuery(query) {
    if (query === this.cache.query && this.cache.known) return;
    this.cache.setQuery(query);
    this.state = sel.emptySelection();
    this.focus = 0;
    this.scroller.scrollTop = 0;
    this.onTotal(this.cache.total, this.cache.known);
    if (this.cache.known) this.grid.setTotal(this.cache.total);
    this.grid.refresh();
    this.emit();
    this.load(0, 0);
  }

  /** Data changed on the server: drop the cache, refetch what is on screen. Selection keys kept (minus `gone`).
   *  Resolves when the visible pages are back (or failed). */
  reload(gone = new Set()) {
    if (gone.size) this.state = sel.without(this.state, gone);
    this.cache.invalidate();
    const done = this.load(this.grid.range.start, this.grid.range.end);
    this.emit();
    return done;
  }

  load(start, end) {
    const total = this.cache.known ? this.cache.total : Infinity;
    return Promise.all(pagesFor(start, end, PAGE, total).map((p) => this.cache.ensure(p).catch((err) => this.failed(err))));
  }

  /** A page of the current query failed: one report per query (and cache generation), and an empty grid with the
   *  error instead of placeholders that never fill. */
  failed(err) {
    const key = `${this.cache.generation}\u0000${this.cache.query}`;
    if (this.failedKey === key) return;
    this.failedKey = key;
    this.grid.setTotal(0);
    this.onTotal(0, true, err);
    this.onError(err);
  }

  get total() {
    return this.cache.total;
  }

  /** Item at index, loading its page if needed (null when out of range or the query changed meanwhile). */
  async itemAt(i) {
    const query = this.cache.query;
    await this.cache.ensure(Math.floor(i / PAGE));
    return query === this.cache.query ? this.cache.get(i) ?? null : null;
  }

  /** Keys of items lo..hi in order, fetching missing pages; null when too large or the query changed. */
  async keysIn(lo, hi) {
    if (hi - lo + 1 > RANGE_CAP) {
      this.onNotice(`Range too large: select at most ${RANGE_CAP.toLocaleString()} wallpapers at once.`);
      return null;
    }
    const query = this.cache.query;
    const keys = [];
    for (let p = Math.floor(lo / PAGE); p <= Math.floor(hi / PAGE); p++) {
      await this.cache.ensure(p);
      if (query !== this.cache.query) return null;
      for (let i = Math.max(lo, p * PAGE); i <= Math.min(hi, p * PAGE + PAGE - 1); i++) {
        const it = this.cache.get(i);
        if (it) keys.push(refKey(it));
      }
    }
    return keys;
  }

  // --- rendering --------------------------------------------------------------------------------
  renderTile(node, i) {
    const item = this.cache.get(i);
    const p = node.parts;
    node.id = `tile-${i}`;
    node.dataset.index = String(i);
    node.setAttribute("aria-posinset", String(i + 1));
    node.setAttribute("aria-setsize", String(this.cache.total));
    node.classList.toggle("is-focus", i === this.focus);
    if (!item) {
      if (node.dataset.sig) clearTile(node);
      node.classList.add("is-loading");
      node.setAttribute("aria-selected", "false");
      node.setAttribute("aria-label", "Loading");
      return;
    }
    const key = refKey(item);
    node.classList.remove("is-loading");
    const sig = tileSignature(item);
    if (node.dataset.sig !== sig) {
      const thumbChanged = node.dataset.key !== key || node.dataset.thumb !== item.thumb;
      node.dataset.sig = sig;
      node.dataset.key = key;
      node.dataset.thumb = item.thumb;
      if (thumbChanged || !p.img.getAttribute("src")) {
        p.frame.classList.remove("broken");
        p.img.src = thumbSrc(item);
      }
      p.title.textContent = item.title || item.file;
      p.artist.textContent = [item.artist, yearLabel(item.year)].filter(Boolean).join(", ");
      const [glyph, tip] = GIT_GLYPHS[item.git] ?? ["", ""];
      p.git.textContent = glyph;
      p.git.title = tip;
      p.git.dataset.state = item.git ?? "";
      p.issue.hidden = !item.issues;
      p.issue.title = item.issues ? `${item.issues} rule ${item.issues === 1 ? "issue" : "issues"}` : "";
      node.setAttribute("aria-label", describe(item));
    }
    node.setAttribute("aria-selected", String(this.state.keys.has(key)));
  }

  // --- selection --------------------------------------------------------------------------------
  emit() {
    this.grid.refresh();
    this.syncActive();
    this.onSelection(this.state.keys);
  }

  /** aria-activedescendant names the focused tile only while that tile is rendered. */
  syncActive() {
    const tile = this.grid.node(this.focus);
    if (tile) this.scroller.setAttribute("aria-activedescendant", tile.id);
    else this.scroller.removeAttribute("aria-activedescendant");
  }

  get selected() {
    return this.state.keys;
  }

  /** The canLeave answer: `true` right away (no extra tick) unless the pane has to ask. */
  allowed() {
    return this.canLeave();
  }

  /** Clear the selection (Esc, Clear); resolves false when the details pane kept an unsaved edit. */
  async clear() {
    if (!this.state.keys.size) return true;
    const go = this.allowed();
    if (go !== true && !(await go)) return false;
    this.state = sel.clear(this.state);
    this.emit();
  }

  /** Select exactly these keys (after an add or a rename). */
  selectKeys(keys) {
    this.state = sel.selectKeys(this.state, keys);
    this.emit();
  }

  /** Index of the item with this key in the current list, when its page is loaded (the focused tile first). */
  locate(key) {
    const at = (i) => {
      const it = this.cache.get(i);
      return it && refKey(it) === key;
    };
    if (at(this.focus)) return this.focus;
    const { start, end } = this.grid.range;
    for (let i = start; i <= end; i++) if (at(i)) return i;
    return null;
  }

  /** Select the item at `i` alone, focus and reveal it (the viewer stepped to it). */
  selectIndex(i) {
    return this.moveFocus(i, {});
  }

  indexOf(target) {
    const el = target.closest?.(".tile");
    return el && this.canvas.contains(el) ? Number(el.dataset.index) : null;
  }

  async apply(i, { ctrl = false, shift = false } = {}) {
    const item = await this.itemAt(i);
    if (!item) return;
    let rangeKeys = null;
    if (shift && this.state.anchor !== null) {
      const [lo, hi] = sel.rangeOf(this.state, i);
      rangeKeys = await this.keysIn(lo, hi);
      if (!rangeKeys) return;
    }
    const go = this.allowed();
    if (go !== true && !(await go)) return;
    this.state = sel.select(this.state, { index: i, key: refKey(item), ctrl, shift, rangeKeys });
    this.emit();
  }

  onClick(e) {
    const i = this.indexOf(e.target);
    if (i === null) return;
    this.focus = i;
    this.scroller.focus({ preventScroll: true });
    this.apply(i, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
  }

  async moveFocus(target, e) {
    const total = this.cache.total;
    if (!total) return;
    const i = Math.max(0, Math.min(total - 1, target));
    this.focus = i;
    this.grid.reveal(i);
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.shiftKey) return this.apply(i, { shift: true, ctrl });
    if (ctrl) return this.emit(); // move the focus only
    const item = await this.itemAt(i);
    if (!item) return;
    if (!(this.state.keys.size === 1 && this.state.keys.has(refKey(item)))) {
      const go = this.allowed();
      if (go !== true && !(await go)) return;
    }
    this.state = sel.selectKeys(this.state, [refKey(item)], i);
    this.emit();
  }

  async selectAll() {
    const keys = await this.keysIn(0, this.cache.total - 1);
    if (!keys) return;
    const go = this.allowed();
    if (go === true || (await go)) this.selectKeys(keys);
  }

  onKey(e) {
    if (e.altKey || !this.cache.total) return;
    const cols = this.grid.columns;
    const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols,
                    PageDown: this.grid.pageItems, PageUp: -this.grid.pageItems };
    let target = null;
    if (e.key in steps) target = this.focus + steps[e.key];
    else if (e.key === "Home") target = 0;
    else if (e.key === "End") target = this.cache.total - 1;
    if (target !== null) {
      e.preventDefault();
      this.moveFocus(target, e);
      return;
    }
    if (e.key === " ") {
      e.preventDefault();
      this.grid.reveal(this.focus);
      this.apply(this.focus, { ctrl: true });
    } else if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      e.stopPropagation();
      this.selectAll();
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      this.itemAt(this.focus).then(async (item) => {
        if (!item) return;
        if (!this.state.keys.has(refKey(item))) {
          const go = this.allowed();
          if (go !== true && !(await go)) return;
          this.selectKeys([refKey(item)]);
        }
        this.onOpen(refKey(item));
      });
    }
  }
}
