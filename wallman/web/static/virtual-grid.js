// Virtualised grid: pure window maths + a page cache (both tested under node) and the DOM part that keeps only the
// visible rows plus one buffer row above and below, recycling tile nodes as the scroll position changes.

/** Tile minimum width and gap per breakpoint of the grid's own width (not the window's). */
const BREAKPOINTS = [
  { upTo: 520, min: 150, gap: 12 },
  { upTo: 1000, min: 210, gap: 16 },
  { upTo: 2000, min: 320, gap: 20 },
  { upTo: Infinity, min: 340, gap: 22 },
];
/** Caption under the thumbnail: title row + artist row. */
export const CAPTION_H = 46;

/**
 * Columns, tile width and row height for a grid `width` pixels wide (thumbnails are 16:9). Without `fixed` the
 * tiles stretch to fill the width; with `fixed` ({tileW, gap}, see fixedTile) the tile keeps its size and only the
 * column count follows the width, the rest being left as margin on the right.
 */
export function layout(width, fixed = null) {
  const w = Math.max(1, width);
  let columns, tileW, gap;
  if (fixed) {
    gap = fixed.gap;
    tileW = Math.min(fixed.tileW, w);
    columns = Math.max(1, Math.floor((w + gap) / (tileW + gap)));
  } else {
    const bp = BREAKPOINTS.find((b) => w <= b.upTo);
    gap = bp.gap;
    columns = Math.max(1, Math.floor((w + gap) / (bp.min + gap)));
    tileW = Math.floor((w - gap * (columns - 1)) / columns);
  }
  const thumbH = Math.round((tileW * 9) / 16);
  return { columns, tileW, thumbH, gap, rowH: thumbH + CAPTION_H + gap };
}

/**
 * Width of the grid canvas for a window `viewportW` wide, with the details pane closed and open. Mirrors the shell
 * in style.css: folder column (--side-w), details column (--details-w), the grid's side padding and a reserve for
 * its stable scrollbar gutter (generous, so a real gallery is never narrower than this). At 900 px and below the
 * folders sit above the gallery and the pane floats over it.
 */
export function galleryWidths(viewportW) {
  const SCROLLBAR = 18;
  if (viewportW <= 900) {
    const w = viewportW - 2 * 16 - SCROLLBAR;
    return { closed: w, open: w };
  }
  const [side, pane] = viewportW <= 1280 ? [208, 340] : [236, 380];
  const closed = viewportW - side - 2 * 24 - SCROLLBAR;
  return { closed, open: closed - pane };
}

/**
 * The one tile size for a window `viewportW` wide: the stretched tile of the pane-closed gallery, made a little
 * smaller when that lets the pane-open gallery keep one column less instead of two less (at most 15 % smaller).
 * Opening or closing the details pane then changes the column count only.
 */
export function fixedTile(viewportW) {
  const { closed, open } = galleryWidths(viewportW);
  const base = layout(closed);
  const bp = BREAKPOINTS.find((b) => closed <= b.upTo);
  let { tileW } = base;
  const want = base.columns - 1;
  if (open < closed && want >= 2) {
    const fit = Math.floor((open - base.gap * (want - 1)) / want);
    if (fit < tileW && fit >= Math.floor(bp.min * 0.85)) tileW = fit;
  }
  return { tileW, gap: base.gap };
}

/** Rows (and item indices, end exclusive) to keep in the DOM: the visible rows and one buffer row each side. */
export function windowRange({ scrollTop, viewportH, total, columns, rowH }) {
  const rows = Math.ceil(total / columns);
  if (!rows) return { firstRow: 0, lastRow: -1, start: 0, end: 0 };
  const top = Math.max(0, scrollTop);
  const firstVisible = Math.floor(top / rowH);
  const lastVisible = Math.floor((top + Math.max(1, viewportH) - 1) / rowH);
  const firstRow = Math.min(rows - 1, Math.max(0, firstVisible - 1));
  const lastRow = Math.min(rows - 1, lastVisible + 1);
  return { firstRow, lastRow, start: firstRow * columns, end: Math.min(total, (lastRow + 1) * columns) };
}

export function totalHeight(total, { columns, rowH }) {
  return Math.ceil(total / columns) * rowH;
}

/** Page numbers covering items [start, end) plus the next page (prefetch), bounded by `total` when known. */
export function pagesFor(start, end, pageSize, total = Infinity) {
  if (end <= start) return [Math.floor(start / pageSize)];
  const first = Math.floor(start / pageSize);
  const last = Math.floor((end - 1) / pageSize) + 1;
  const out = [];
  for (let p = first; p <= last; p++) if (p === first || p * pageSize < total) out.push(p);
  return out;
}

/**
 * Scroll position (in grid coordinates) that keeps item `anchor` at the same height on screen when the layout
 * changes from `oldLay` to `newLay` (the details pane opening changes the column count). Clamped at 0.
 */
export function anchorScroll({ anchor, scrollTop, oldLay, newLay }) {
  const oldTop = Math.floor(anchor / oldLay.columns) * oldLay.rowH;
  const newTop = Math.floor(anchor / newLay.columns) * newLay.rowH;
  return Math.max(0, newTop - (oldTop - scrollTop));
}

/**
 * Scroll position after a layout change (the details pane toggled): unchanged at the top or while the anchor's row
 * stays fully on screen, so the first row never ends up half hidden; otherwise anchored (anchorScroll).
 */
export function relayoutScroll({ anchor, scrollTop, viewportH, oldLay, newLay }) {
  if (scrollTop <= 0) return scrollTop; // at the top (inside the padding above the canvas): untouched
  const top = Math.floor(anchor / newLay.columns) * newLay.rowH;
  if (top >= scrollTop && top + newLay.rowH - (newLay.gap ?? 0) <= scrollTop + viewportH) return scrollTop;
  return anchorScroll({ anchor, scrollTop, oldLay, newLay });
}

/** First item of the first row at least partly visible at `scrollTop`. */
export function firstVisible(scrollTop, { columns, rowH }) {
  return Math.max(0, Math.floor(Math.max(0, scrollTop) / rowH)) * columns;
}

/** Item index under a point given in grid coordinates (or -1 in a gap column). */
export function indexAt({ x, y }, { columns, rowH, tileW, gap }) {
  const col = Math.min(columns - 1, Math.max(0, Math.floor(x / (tileW + gap))));
  return Math.max(0, Math.floor(y / rowH)) * columns + col;
}

const DONE = Promise.resolve();

/**
 * Pages of list results keyed by query string, bounded LRU shared by every query. A response that arrives after the
 * query changed (or after `invalidate`) is discarded, so a slow old request never paints over the current view.
 */
export class PageCache {
  /** @param {{fetchPage: (query: string, offset: number, limit: number) => Promise<{items: object[], total: number}>,
   *           pageSize?: number, maxPages?: number, onPage?: (page: number) => void}} options */
  constructor({ fetchPage, pageSize = 200, maxPages = 40, onPage = () => {} }) {
    Object.assign(this, { fetchPage, pageSize, maxPages, onPage });
    this.pages = new Map();
    this.inflight = new Map();
    this.totals = new Map();
    this.query = "";
    this.generation = 0;
    this.stale = new Map(); // pages of `staleQuery` from before the last invalidate, shown until fresh ones arrive
    this.staleQuery = null;
  }

  setQuery(query) {
    this.query = query;
  }

  key(page) {
    return `${this.generation}\u0000${this.query}\u0000${page}`;
  }

  /** Total for the current query (0 until its first page arrives). */
  get total() {
    return this.totals.get(this.query) ?? 0;
  }

  get known() {
    return this.totals.has(this.query);
  }

  get size() {
    return this.pages.size;
  }

  has(page) {
    return this.pages.has(this.key(page));
  }

  /** Item at `index` of the current query, or undefined when its page is not loaded. Touches the page (LRU). */
  get(index) {
    const page = Math.floor(index / this.pageSize);
    const k = this.key(page);
    const items = this.pages.get(k);
    if (!items) return this.staleQuery === this.query ? this.stale.get(page)?.[index - page * this.pageSize] : undefined;
    this.pages.delete(k);
    this.pages.set(k, items);
    return items[index - page * this.pageSize];
  }

  /** Load a page of the current query once; concurrent calls share one request. */
  ensure(page) {
    const k = this.key(page);
    if (this.pages.has(k)) return DONE;
    if (this.inflight.has(k)) return this.inflight.get(k);
    const { query, generation } = this;
    const current = () => query === this.query && generation === this.generation;
    const p = this.fetchPage(query, page * this.pageSize, this.pageSize).then(
      (res) => {
        this.inflight.delete(k);
        if (!current()) return;
        this.pages.set(k, res.items);
        this.totals.set(query, res.total);
        while (this.pages.size > this.maxPages) this.pages.delete(this.pages.keys().next().value);
        this.onPage(page);
      },
      (err) => {
        this.inflight.delete(k);
        if (current()) throw err;
      },
    );
    this.inflight.set(k, p);
    return p;
  }

  /** Forget everything (after a change on the server); in-flight responses are discarded. Until the refetched
   *  pages arrive, `get` still answers from the old pages of the current query so the grid does not flash empty. */
  invalidate() {
    const prefix = `${this.generation}\u0000${this.query}\u0000`;
    this.stale = new Map();
    for (const [k, items] of this.pages) if (k.startsWith(prefix)) this.stale.set(Number(k.slice(prefix.length)), items);
    this.staleQuery = this.query;
    this.generation += 1;
    this.pages.clear();
    this.inflight.clear();
    this.totals.clear();
  }
}

/**
 * DOM side. `scroller` scrolls; `canvas` (inside it) is sized to the full grid height and holds the absolutely
 * positioned tiles. Only the window's tiles exist; nodes leaving it are detached and reused.
 */
export class VirtualGrid {
  /**
   * @param {HTMLElement} scroller
   * @param {HTMLElement} canvas
   * @param {{create: () => HTMLElement, render: (node: HTMLElement, index: number) => void,
   *          recycle: (node: HTMLElement) => void, onWindow: (start: number, end: number) => void,
   *          anchor?: () => number|null, onRelayout?: () => void}} hooks
   *   `anchor()` names the item to keep in place when the width changes (the focused one), `onRelayout` runs after.
   */
  constructor(scroller, canvas, hooks) {
    Object.assign(this, { scroller, canvas, hooks });
    this.total = 0;
    this.nodes = new Map();
    this.pool = [];
    this.frame = 0;
    this.range = { start: 0, end: 0 };
    this.lay = this.measure(1000);
    scroller.addEventListener("scroll", () => this.schedule(), { passive: true });
    new ResizeObserver(() => this.relayout()).observe(canvas);
  }

  /** Width changed: new columns, and the anchor item (focused one if on screen, else the first visible) stays at
   *  the same height in the viewport instead of the pixel scrollTop being kept (which would jump rows). */
  relayout() {
    const newLay = this.measure(1);
    const oldLay = this.lay;
    if (newLay.columns === oldLay.columns && newLay.rowH === oldLay.rowH && newLay.tileW === oldLay.tileW) return;
    const { scrollTop, viewportH } = this.viewport();
    let anchor = this.hooks.anchor?.();
    if (anchor === null || anchor === undefined || anchor < this.range.start || anchor >= this.range.end) {
      anchor = firstVisible(scrollTop, oldLay);
    }
    this.lay = newLay;
    this.applyHeight();
    if (this.total) {
      this.scroller.scrollTop = relayoutScroll({ anchor: Math.min(anchor, this.total - 1), scrollTop, viewportH, oldLay,
                                                 newLay }) + this.canvasTop();
    }
    this.paint(true);
    this.hooks.onRelayout?.();
  }

  /** Layout for the canvas width now, with the tile size of the window (not of the gallery) */
  measure(fallback) {
    return layout(this.canvas.clientWidth || fallback, fixedTile(window.innerWidth || 1920));
  }

  setTotal(total) {
    if (total === this.total) return;
    this.total = total;
    this.applyHeight();
    this.schedule(true);
  }

  applyHeight() {
    this.canvas.style.height = `${totalHeight(this.total, this.lay)}px`;
  }

  /** Coalesce scroll and resize work into one animation frame. `all` re-renders every kept node too. */
  schedule(all = false) {
    this.dirtyAll ||= all;
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const everything = this.dirtyAll;
      this.dirtyAll = false;
      this.paint(everything);
    });
  }

  viewport() {
    return { scrollTop: this.scroller.scrollTop - this.canvasTop(), viewportH: this.scroller.clientHeight };
  }

  /** The canvas's top inside the scroller's content. offsetTop alone is relative to the offsetParent (the
   *  positioned .gallery), which counted the gallery heading and left the grid scrolled by its height. */
  canvasTop() {
    const c = this.canvas, s = this.scroller;
    return c.offsetParent === s ? c.offsetTop : c.offsetTop - s.offsetTop - s.clientTop;
  }

  paint(everything) {
    const { start, end } = windowRange({ ...this.viewport(), total: this.total, ...this.lay });
    for (const [i, node] of this.nodes) {
      if (i >= start && i < end) continue;
      this.nodes.delete(i);
      this.hooks.recycle(node);
      node.remove();
      this.pool.push(node);
    }
    const { columns, tileW, rowH, gap } = this.lay;
    const fresh = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      let node = this.nodes.get(i);
      const isNew = !node;
      if (isNew) {
        node = this.pool.pop() ?? this.hooks.create();
        this.nodes.set(i, node);
        fresh.append(node);
      }
      if (isNew || everything || node.dataset.w !== String(tileW)) {
        const col = i % columns;
        node.style.transform = `translate(${col * (tileW + gap)}px, ${Math.floor(i / columns) * rowH}px)`;
        node.style.width = `${tileW}px`;
        node.dataset.w = String(tileW);
        node.style.setProperty("--thumb-h", `${this.lay.thumbH}px`);
        this.hooks.render(node, i);
      }
    }
    if (fresh.childNodes.length) this.canvas.append(fresh);
    this.range = { start, end };
    this.hooks.onWindow(start, end);
  }

  /** Re-render the kept nodes (new page data, selection or focus changed). */
  refresh() {
    for (const [i, node] of this.nodes) this.hooks.render(node, i);
  }

  node(index) {
    return this.nodes.get(index) ?? null;
  }

  /** Scroll the least amount that brings item `index` fully into view, then paint synchronously. */
  reveal(index) {
    const { rowH, columns, gap } = this.lay;
    const top = Math.floor(index / columns) * rowH + this.canvasTop();
    const bottom = top + rowH - gap;
    const s = this.scroller;
    if (top < s.scrollTop) s.scrollTop = top - gap;
    else if (bottom > s.scrollTop + s.clientHeight) s.scrollTop = bottom - s.clientHeight + gap;
    this.paint(false);
  }

  get columns() {
    return this.lay.columns;
  }

  /** Items per screenful, for PageUp/PageDown. */
  get pageItems() {
    return Math.max(1, Math.floor(this.scroller.clientHeight / this.lay.rowH)) * this.lay.columns;
  }
}
