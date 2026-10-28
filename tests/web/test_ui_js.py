"""Pure UI modules (selection, virtual-grid window maths and page cache, menu helpers, years, request sequence),
run under node (skipped when node is absent)."""
import shutil
import subprocess
import unittest

from wallman.web.server import STATIC

PRELUDE = r"""
const mod = (name) => import(new URL(name, BASE).href);
const ok = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exitCode = 1; } };
const eq = (a, b, msg) => ok(JSON.stringify(a) === JSON.stringify(b), `${msg}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const keys = (s) => [...s.keys].sort();
const K = (i) => `f/${i}.jpg`;
const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => K(a + i));
"""


def run_node(test: unittest.TestCase, body: str) -> None:
    script = (f"const BASE = {(STATIC.as_uri() + '/')!r};\n" + PRELUDE + body
              + '\nif (!process.exitCode) console.log("js ok");\n')
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    test.assertEqual(r.returncode, 0, r.stderr + r.stdout)
    test.assertIn("js ok", r.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SelectionJsTest(unittest.TestCase):
    def test_click_toggles_single(self):
        run_node(self, r"""
const s = await mod("selection.js");
let st = s.emptySelection();
st = s.select(st, { index: 2, key: K(2) });
eq(keys(st), [K(2)], "click selects one");
st = s.select(st, { index: 4, key: K(4) });
eq(keys(st), [K(4)], "click on another replaces");
st = s.select(st, { index: 4, key: K(4) });
eq(keys(st), [], "clicking the only selected item deselects it");
st = s.select(s.select(s.emptySelection(), { index: 1, key: K(1) }), { index: 3, key: K(3), ctrl: true });
st = s.select(st, { index: 3, key: K(3) });
eq(keys(st), [K(3)], "plain click on one of several selects only it");
""")

    def test_ctrl_toggle(self):
        run_node(self, r"""
const s = await mod("selection.js");
let st = s.select(s.emptySelection(), { index: 2, key: K(2) });
st = s.select(st, { index: 4, key: K(4), ctrl: true });
eq(keys(st), [K(2), K(4)], "ctrl adds without the items between");
st = s.select(st, { index: 2, key: K(2), ctrl: true });
eq(keys(st), [K(4)], "ctrl removes");
eq(st.anchor, 2, "ctrl moves the anchor");
const before = s.select(s.emptySelection(), { index: 0, key: K(0) });
s.select(before, { index: 1, key: K(1), ctrl: true });
eq(keys(before), [K(0)], "states are immutable");
""")

    def test_shift_range_from_anchor(self):
        run_node(self, r"""
const s = await mod("selection.js");
let st = s.select(s.emptySelection(), { index: 3, key: K(3) });
eq(s.rangeOf(st, 6), [3, 6], "range forward");
eq(s.rangeOf(st, 1), [1, 3], "range backward");
st = s.select(st, { index: 6, key: K(6), shift: true, rangeKeys: range(3, 6) });
eq(keys(st), range(3, 6), "shift selects anchor..index");
eq(st.anchor, 3, "shift keeps the anchor");
st = s.select(st, { index: 1, key: K(1), shift: true, rangeKeys: range(1, 3) });
eq(keys(st), range(1, 3), "a second shift-click replaces the range");
const fresh = s.select(s.emptySelection(), { index: 5, key: K(5), shift: true, rangeKeys: [K(5)] });
eq(keys(fresh), [K(5)], "shift without an anchor selects one");
eq(fresh.anchor, 5, "and sets the anchor");
eq(s.rangeOf(s.emptySelection(), 5), [5, 5], "no anchor: a one-item range");
""")

    def test_ctrl_shift_extends(self):
        run_node(self, r"""
const s = await mod("selection.js");
let st = s.select(s.emptySelection(), { index: 0, key: K(0) });
st = s.select(st, { index: 5, key: K(5), ctrl: true });
st = s.select(st, { index: 8, key: K(8), ctrl: true, shift: true, rangeKeys: range(5, 8) });
eq(keys(st), [K(0), ...range(5, 8)], "ctrl+shift adds the range to the selection");
eq(st.anchor, 5, "ctrl+shift keeps the anchor");
""")

    def test_esc_clears(self):
        run_node(self, r"""
const s = await mod("selection.js");
let st = s.select(s.select(s.emptySelection(), { index: 0, key: K(0) }), { index: 2, key: K(2), ctrl: true });
st = s.clear(st);
eq(keys(st), [], "esc clears");
eq(st.anchor, null, "and drops the anchor");
st = s.selectKeys(s.emptySelection(), [K(1), K(2)]);
eq(keys(st), [K(1), K(2)], "selectKeys");
eq(s.keyToRef("a/b/c.jpg"), { folder: "a/b", file: "c.jpg" }, "key to ref");
""")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class VirtualGridJsTest(unittest.TestCase):
    def test_virtual_grid_window_bounds(self):
        run_node(self, r"""
const v = await mod("virtual-grid.js");
// columns per width: tile minimum per breakpoint, never below one column
const L = (w) => v.layout(w);
ok(L(300).columns === 1, "narrow: one column " + L(300).columns);
ok(L(1640).columns >= 3 && L(1640).columns <= 5, "desktop columns " + L(1640).columns);
ok(L(2600).columns > L(1640).columns, "wider: more columns");
for (const w of [200, 360, 700, 1000, 1400, 1640, 2000, 3000]) {
  const l = L(w);
  ok(l.tileW * l.columns + l.gap * (l.columns - 1) <= w + 0.5, `tiles fit ${w}`);
  ok(Number.isInteger(l.rowH) && l.rowH > 0, `integer row height ${w}`);
}
// window: visible rows + one buffer row on each side, clamped to the data
const lay = { columns: 4, rowH: 100 };
let r = v.windowRange({ scrollTop: 0, viewportH: 350, total: 1000, ...lay });
eq([r.firstRow, r.lastRow, r.start, r.end], [0, 4, 0, 20], "top: rows 0-3 visible + 1 buffer below");
r = v.windowRange({ scrollTop: 1000, viewportH: 350, total: 1000, ...lay });
eq([r.firstRow, r.lastRow, r.start, r.end], [9, 14, 36, 60], "middle: buffer both sides");
r = v.windowRange({ scrollTop: 24900, viewportH: 350, total: 1000, ...lay });
eq([r.lastRow, r.end], [249, 1000], "bottom clamps to the last row");
r = v.windowRange({ scrollTop: 0, viewportH: 350, total: 6, ...lay });
eq([r.firstRow, r.lastRow, r.start, r.end], [0, 1, 0, 6], "fewer items than the viewport");
r = v.windowRange({ scrollTop: 0, viewportH: 350, total: 0, ...lay });
eq([r.start, r.end], [0, 0], "empty");
eq(v.totalHeight(1000, lay), 25000, "height from total");
eq(v.totalHeight(0, lay), 0, "empty height");
eq(v.pagesFor(390, 830, 200), [1, 2, 3, 4, 5], "pages covering a window plus the next one");
eq(v.pagesFor(390, 830, 200, 900), [1, 2, 3, 4], "never past the known total");
eq(v.pagesFor(0, 0, 200), [0], "empty window still asks for page 0");
// the DOM budget: <= 40 tiles while scrolling a 10,000-item folder on a 1640x1000 gallery
const big = L(1640);
let most = 0;
for (let top = 0; top < v.totalHeight(10000, big); top += 37) {
  const w = v.windowRange({ scrollTop: top, viewportH: 1000, total: 10000, ...big });
  most = Math.max(most, w.end - w.start);
}
ok(most <= 40, "at most 40 tiles in the window, got " + most);
eq(v.indexAt({ x: 10, y: 250 }, { columns: 4, rowH: 100, tileW: 90, gap: 10 }), 8, "index under a point");
""")

    def test_relayout_keeps_anchor_row(self):
        run_node(self, r"""
const v = await mod("virtual-grid.js");
const four = { columns: 4, rowH: 100 }, three = { columns: 3, rowH: 120 };
// item 41: row 10 at 4 columns (top 1000), row 13 at 3 columns (top 1560); it was 150px below the viewport top
eq(v.anchorScroll({ anchor: 41, scrollTop: 850, oldLay: four, newLay: three }), 1410, "anchor keeps its height on screen");
eq(v.anchorScroll({ anchor: 0, scrollTop: 0, oldLay: four, newLay: three }), 0, "top stays top");
eq(v.anchorScroll({ anchor: 3, scrollTop: 0, oldLay: three, newLay: four }), 0, "clamped at 0");
eq(v.anchorScroll({ anchor: 1, scrollTop: 50, oldLay: four, newLay: three }), 50, "same row: same scroll");
const big4 = { columns: 4, rowH: 300, gap: 20 }, big3 = { columns: 3, rowH: 300, gap: 20 };
eq(v.relayoutScroll({ anchor: 1, scrollTop: 0, viewportH: 900, oldLay: big4, newLay: big3 }), 0, "at the top: stays at the top");
eq(v.relayoutScroll({ anchor: 5, scrollTop: 100, viewportH: 900, oldLay: big4, newLay: big3 }), 100,
   "the anchor still fully on screen: no scroll");
eq(v.relayoutScroll({ anchor: 41, scrollTop: 850, viewportH: 350, oldLay: four, newLay: three }), 1410,
   "the anchor would leave the viewport: anchored as before");
eq(v.firstVisible(250, four), 8, "first visible item");
eq(v.firstVisible(-5, four), 0, "never negative");
""")

    def test_tile_repaints_on_any_change_and_recycles_clean(self):
        run_node(self, r"""
const g = await mod("gallery.js");
const txt = () => ({ textContent: "", title: "", dataset: {}, hidden: false });
const cls = () => { const s = new Set(); return { s, toggle(c, on) { on ? s.add(c) : s.delete(c); }, add(c) { s.add(c); }, remove(c) { s.delete(c); } }; };
const node = () => { const attrs = {}; return {
  dataset: {}, attrs, classList: cls(), setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; },
  parts: { frame: { classList: cls() }, issue: txt(), title: txt(), git: txt(), artist: txt(),
           img: { a: {}, removeAttribute(k) { delete this.a[k]; }, set src(v) { this.a.src = v; }, getAttribute(k) { return this.a[k]; } } } }; };
const items = new Map();
const fake = { cache: { get: (i) => items.get(i), total: 10 }, focus: 0, state: { keys: new Set() } };
const render = (n, i) => g.Gallery.prototype.renderTile.call(fake, n, i);
const n = node();
items.set(0, { folder: "f", file: "a.jpg", title: "Old title", artist: "A", year: 1900, git: "committed", thumb: "t1", issues: 0 });
render(n, 0);
eq([n.parts.title.textContent, n.parts.artist.textContent, n.parts.issue.hidden], ["Old title", "A, 1900", true], "first paint");
const src = n.parts.img.getAttribute("src");
items.set(0, { folder: "f", file: "a.jpg", title: "New title", artist: "B", year: [1880, 1889], git: "committed", thumb: "t1", issues: 2 });
render(n, 0);
eq([n.parts.title.textContent, n.parts.artist.textContent, n.parts.issue.hidden], ["New title", "B, 1880–1889", false],
   "title, artist, year and issues edits repaint");
eq(n.parts.img.getAttribute("src"), src, "same thumb key: the image is not reloaded");
items.set(0, { ...items.get(0), git: "staged", thumb: "t2" });
render(n, 0);
ok(n.parts.git.textContent === "+" && n.parts.img.getAttribute("src").endsWith("v=t2"), "git and thumb changes repaint");
n.parts.frame.classList.add("broken");
g.clearTile(n);                                   // recycled off screen
render(n, 5);                                     // reused for an index whose page is not loaded
eq([n.parts.title.textContent, n.parts.artist.textContent, n.parts.git.textContent, n.parts.issue.hidden,
    n.parts.frame.classList.s.has("broken"), n.parts.img.getAttribute("src")], ["", "", "", true, false, undefined],
   "a recycled placeholder shows nothing of the previous item");
ok(n.classList.s.has("is-loading") && n.attrs["aria-label"] === "Loading", "placeholder state");
items.set(5, { folder: "f", file: "b.jpg", title: "B", artist: "", year: null, git: "untracked", thumb: "t9", issues: 0 });
render(n, 5);
ok(n.parts.title.textContent === "B" && !n.classList.s.has("is-loading"), "placeholder fills in when its page arrives");
ok(g.tileSignature(items.get(5)) !== g.tileSignature({ ...items.get(5), shareable: true }), "signature covers shareable");
""")

    def test_page_cache_discards_stale_query(self):
        run_node(self, r"""
const v = await mod("virtual-grid.js");
const pending = [];
const fetchPage = (query, offset, limit) => new Promise((resolve) => pending.push({ query, offset, limit, resolve }));
const items = (q, off, n) => Array.from({ length: n }, (_, i) => ({ folder: q, file: `${off + i}.jpg` }));
const loaded = [];
const c = new v.PageCache({ fetchPage, pageSize: 2, maxPages: 3, onPage: (p) => loaded.push(p) });
c.setQuery("a");
const pa = c.ensure(0);
c.setQuery("b");
const pb = c.ensure(0);
eq(pending.map((p) => [p.query, p.offset, p.limit]), [["a", 0, 2], ["b", 0, 2]], "requests carry their query");
pending[1].resolve({ items: items("b", 0, 2), total: 7, offset: 0 });
pending[0].resolve({ items: items("a", 0, 2), total: 99, offset: 0 });
await pa; await pb;
eq(c.total, 7, "total from the current query only");
eq(c.get(0).folder, "b", "stale page discarded");
eq(loaded, [0], "onPage only for the current query");
ok(c.ensure(0) === c.ensure(0), "loaded page: no new request");
eq(pending.length, 2, "cached page not refetched");
// in-flight dedupe
const p1 = c.ensure(1), p2 = c.ensure(1);
eq(pending.length, 3, "one request per page in flight");
pending[2].resolve({ items: items("b", 2, 2), total: 7, offset: 2 });
await p1; await p2;
eq(c.get(3).file, "3.jpg", "index lookup across pages");
eq(c.get(5), undefined, "unloaded index");
// a failed request is not cached: the next ensure asks again
const failing = new v.PageCache({ fetchPage: async () => { throw new Error("boom"); }, pageSize: 2 });
failing.setQuery("x");
let threw = false;
try { await failing.ensure(0); } catch { threw = true; }
ok(threw && !failing.has(0), "failure propagates and is not cached");
""")

    def test_page_cache_lru_eviction(self):
        run_node(self, r"""
const v = await mod("virtual-grid.js");
const fetchPage = async (query, offset, limit) => ({ items: [{ folder: query, file: `${offset}.jpg` }], total: 100, offset });
const c = new v.PageCache({ fetchPage, pageSize: 1, maxPages: 3 });
c.setQuery("q");
for (const p of [0, 1, 2]) await c.ensure(p);
c.get(0);                       // touch page 0: page 1 is now the oldest
await c.ensure(3);
ok(c.has(0) && !c.has(1) && c.has(2) && c.has(3), "least recently used page evicted");
eq(c.size, 3, "bounded");
c.setQuery("other");
await c.ensure(0);
eq(c.size, 3, "pages of several queries share the bound");
c.setQuery("q");
ok(c.has(0) && c.has(3) && !c.has(2), "returning to a query reuses its cached pages (oldest went first)");
c.invalidate();
ok(!c.has(0) && c.total === 0 && c.size === 0, "invalidate drops every cached page");
eq(c.get(0)?.file, "0.jpg", "old items still answer until the refetch lands (no empty flash)");
c.setQuery("other");
eq(c.get(0), undefined, "but never for another query");
""")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class MenuJsTest(unittest.TestCase):
    def test_menu_helpers(self):
        run_node(self, r"""
const m = await mod("select-menu.js");
const opts = [{ value: "", label: "Any license" }, { value: "cc0", label: "CC0 1.0" }, { value: "by", label: "CC BY 4.0" },
              { value: "pd", label: "Public domain" }, { value: "pers", label: "Personal" }];
eq(m.typeAhead(opts, "p", 0), 3, "type-ahead finds the next match");
eq(m.typeAhead(opts, "p", 3), 4, "repeating the letter cycles");
eq(m.typeAhead(opts, "p", 4), 3, "and wraps");
eq(m.typeAhead(opts, "pe", 3), 4, "longer buffer matches from the current item");
eq(m.typeAhead(opts, "zz", 1), -1, "no match");
eq(m.typeAhead(opts, "c", -1), 1, "from no active item");
eq(m.moveIndex(0, -1, 5), 0, "clamp top");
eq(m.moveIndex(4, 1, 5), 4, "clamp bottom");
eq(m.moveIndex(-1, 1, 5), 0, "first arrow from nothing");
eq(m.filterOptions(["impressionism", "japan", "abstract-japanese", "ja"], "ja"), ["ja", "japan", "abstract-japanese"],
   "prefix matches first (shortest first), then substring");
eq(m.filterOptions(["A", "b"], ""), ["A", "b"], "empty filter keeps all");
eq(m.filterOptions(["Alpha"], "ALP"), ["Alpha"], "case-insensitive");
""")

    def test_tree_deselect_and_git_glyphs(self):
        run_node(self, r"""
const t = await mod("tree.js");
eq(t.nextFolder("", "painting"), "painting", "click a folder selects it");
eq(t.nextFolder("painting", "painting"), "", "click the active folder goes back to all");
eq(t.nextFolder("painting", "photo"), "photo", "click another folder");
eq(t.nextFolder("", ""), "", "all stays all");
const g = await mod("gallery.js");
eq(Object.fromEntries(Object.entries(g.GIT_GLYPHS).map(([k, v]) => [k, v[0]])),
   { committed: "✓", metadata: "◐", staged: "+", "metadata-staged": "◐", modified: "~", untracked: "?", ignored: "⊘",
     deleted: "−", conflict: "!", pending: "…" }, "glyphs");
ok(g.GIT_GLYPHS.metadata[1].includes("metadata only"), "metadata tooltip says metadata only");
ok(g.GIT_GLYPHS["metadata-staged"][1].includes("metadata only"), "staged metadata tooltip says metadata only");
const legend = g.legendRows();
ok(legend.some(([glyph, state]) => glyph === "◐" && state === "metadata"), "legend lists ◐ metadata");
ok(!legend.some(([, state]) => state === "pending" || state === "ignored"), "legend: wallpaper states only");
ok(Object.values(g.GIT_GLYPHS).every(([, tip]) => tip.length > 5), "every glyph has a tooltip");
eq([g.yearLabel(1889), g.yearLabel([1880, 1889]), g.yearLabel(null)], ["1889", "1880–1889", ""], "year labels");
""")

    def test_years_and_sequence(self):
        run_node(self, r"""
const y = await mod("years.js");
const a = await mod("api.js");
for (const [text, want] of [["1889", [1889, 1889]], ["188x", [1880, 1889]], ["188X", [1880, 1889]], ["18xx", [1800, 1899]],
                            ["1880s", [1880, 1889]], ["1800s", [1800, 1899]], ["19th century", [1800, 1899]],
                            ["21st century", [2000, 2099]], ["1880-1895", [1880, 1895]], ["1880 – 1895", [1880, 1895]],
                            ["c. 1880", [1875, 1885]], ["circa 1880", [1875, 1885]], ["ca 1402", [1400, 1407]],
                            [" 2100 ", [2100, 2100]]]) {
  eq(y.parseYear(text), want, `parseYear ${text}`);
}
for (const bad of ["", "abc", "12", "1399", "2101", "1889-1880", "18 80", "0th century", "1880-2101"]) {
  eq(y.parseYear(bad), null, `parseYear refuses ${JSON.stringify(bad)}`);
}
eq([y.yearToApi([1889, 1889]), y.yearToApi([1880, 1889]), y.yearToApi(null)], [1889, [1880, 1889], null], "API shape");
eq([y.yearText(1889), y.yearText([1880, 1889]), y.yearText(null)], ["1889", "1880–1889", ""], "year text");
const seq = a.createSequence();
const t1 = seq.next(), t2 = seq.next();
ok(!seq.isCurrent(t1) && seq.isCurrent(t2), "older token is stale");
eq(a.listQuery({ folder: "a b", q: "", tags: ["x", "y"], license: "", ratio: "16:9", year: "", issues: true, sort: "name" }),
   "folder=a+b&tag=x&tag=y&ratio=16%3A9&issues=1&sort=name", "list query string: empty filters left out");
eq(a.thumbSrc({ folder: "a b", file: "c.jpg", thumb: "k1" }), "/api/thumbs/a%20b/c.jpg?v=k1", "versioned thumbnail url");
""")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DetailsJsTest(unittest.TestCase):
    def test_year_input_reads_text(self):
        run_node(self, r"""
const yi = await mod("year-input.js");
eq(yi.readYear(""), { value: null, error: "" }, "empty is no year");
eq(yi.readYear("1880s"), { value: [1880, 1889], error: "" }, "a decade becomes a range");
eq(yi.readYear("1906"), { value: 1906, error: "" }, "a single year stays an integer");
const bad = yi.readYear("soon");
ok(bad.value === undefined && /1400/.test(bad.error) && /1880s/.test(bad.error), "unreadable text explains: " + bad.error);
eq(yi.readYear("1889-1880").value, undefined, "reversed range refused");
eq(yi.yearSuggestable("20"), true, "digits ask for suggestions");
eq([yi.yearSuggestable(""), yi.yearSuggestable("20th"), yi.yearSuggestable("12345")], [false, false, false], "other text does not");
""")

    def test_tag_normalisation(self):
        run_node(self, r"""
const t = await mod("tag-input.js");
eq(t.normaliseTag("  Art Nouveau "), "art-nouveau", "spaces to kebab, lowercase");
eq(t.normaliseTag("Été_d'or"), "ete-d-or", "accents dropped, punctuation to dashes");
eq(t.normaliseTag("--!!--"), "", "nothing letter-like left");
const long = "alpha-" + "b".repeat(70);
ok(t.normaliseTag(long).length <= 60, "cut at 60 like the server: " + t.normaliseTag(long));
eq(t.normaliseTag("word ".repeat(20)).endsWith("-"), false, "no trailing dash after the cut");
eq(t.addTags(["blue"], "Sky, night sky,,blue"), ["blue", "night-sky", "sky"], "comma list added, unique, sorted");
eq(t.addTags([], " , "), [], "empty input adds nothing");
eq(t.removeTag(["a", "b", "c"], "b"), ["a", "c"], "remove one");
""")

    def test_folder_tree_rows_and_filter(self):
        run_node(self, r"""
const f = await mod("folder-picker.js");
const rows = f.folderRows([
  { folder: "photography/ultrawide", external: false }, { folder: "painting/japan", external: false },
  { folder: "painting/impressionism", external: false }, { folder: "illustration/ml4w", external: true },
  { folder: "painting/japan/ukiyo-e", external: false }]);
eq(rows.map((r) => [r.path, r.depth, r.selectable]), [
  ["illustration", 0, false], ["illustration/ml4w", 1, true],
  ["painting", 0, false], ["painting/impressionism", 1, true], ["painting/japan", 1, true], ["painting/japan/ukiyo-e", 2, true],
  ["photography", 0, false], ["photography/ultrawide", 1, true]], "sorted tree with intermediate folders");
eq(rows.find((r) => r.path === "illustration/ml4w").external, true, "external marked");
eq(rows.find((r) => r.path === "painting/japan").name, "japan", "row name is the last part");
eq(f.filterRows(rows, "").length, rows.length, "empty filter keeps all");
eq(f.filterRows(rows, "JAP").map((r) => r.path), ["painting", "painting/japan", "painting/japan/ukiyo-e"],
   "matches keep their ancestors for context");
eq(f.filterRows(rows, "photo/ultra").map((r) => r.path), [], "a path fragment that is not a substring finds nothing");
eq(f.filterRows(rows, "photography/ultra").map((r) => r.path), ["photography", "photography/ultrawide"], "full path substring");
eq(f.filterRows(rows, "zzz"), [], "no match");
eq(f.parentRows(rows).map((r) => r.path), ["illustration", "painting", "painting/impressionism", "painting/japan",
   "painting/japan/ukiyo-e", "photography", "photography/ultrawide"], "new subfolders only under non-external folders");
eq(f.newFolderPath("painting", "  Expressionism ", rows), { path: "painting/expressionism", error: "" }, "name slugged under the parent");
eq(f.newFolderPath("painting/japan", "Edo Period", rows).path, "painting/japan/edo-period", "nested");
ok(/name/i.test(f.newFolderPath("painting", " ../ ", rows).error), "a name with no letters is refused");
ok(/exists/i.test(f.newFolderPath("painting", "Japan", rows).error), "an existing folder is refused");
ok(/external/i.test(f.newFolderPath("illustration/ml4w", "x", rows).error), "never inside an external collection");
ok(f.newFolderPath("", "x", rows).error, "a parent is required");
""")

    def test_viewer_navigation(self):
        run_node(self, r"""
const v = await mod("viewer.js");
eq([v.stepIndex(3, 1, 10), v.stepIndex(3, -1, 10)], [4, 2], "next and previous");
eq([v.stepIndex(0, -1, 10), v.stepIndex(9, 1, 10)], [null, null], "no wrap at the ends");
eq([v.stepIndex(0, 1, 0), v.stepIndex(null, 1, 10)], [null, null], "empty list or unknown position");
eq(v.positionText(4, 40), "5 of 40", "position");
eq(v.positionText(null, 40), "", "unknown position");
""")

    def test_read_mode_helpers(self):
        run_node(self, r"""
const d = await mod("details.js");
const arr = { id: "all-rights-reserved", label: "All rights reserved" };
const by = { id: "CC-BY-4.0", label: "CC BY 4.0" };
eq(d.licenseNote("© 2010 DreamWorks Animation LLC", arr), "© 2010 DreamWorks Animation LLC", "a rights holder adds information");
eq([d.licenseNote("CC BY 4.0", by), d.licenseNote("cc-by-4.0", by), d.licenseNote("  ", by), d.licenseNote(null, by)],
   ["", "", "", ""], "a repeat of the license or nothing is hidden");
eq(d.licenseNote("All rights reserved.", arr), "", "trailing punctuation still counts as a repeat");
eq(d.splitLabel("CC BY 4.0: credit the author if you republish"), ["CC BY 4.0", "Credit the author if you republish"],
   "license label split into name and terms");
eq(d.splitLabel("Public domain"), ["Public domain", ""], "a label without terms");
eq(d.safeHref("https://example.org/a?b=1"), "https://example.org/a?b=1", "https links kept");
eq(d.safeHref("HTTP://example.org/"), "http://example.org/", "http links kept (normalised)");
for (const bad of ["javascript:alert(1)", "ftp://x.org/f", "data:text/html,x", "not a url", "", null]) {
  eq(d.safeHref(bad), null, `refused ${bad}`);
}
eq(d.displayUrl("https://www.example.org/wiki/File:Monet.jpg"), "example.org/wiki/File:Monet.jpg", "host and path, no www");
eq(d.displayUrl("https://example.org/"), "example.org", "bare host");
const shown = d.displayUrl("https://example.org/" + "x".repeat(200));
ok(shown.length <= 48 && shown.endsWith("…"), "long links shortened: " + shown);
""")

    def test_details_git_action(self):
        run_node(self, r"""
const d = await mod("details.js");
const lic = new Map([["CC0-1.0", { id: "CC0-1.0", shareable: true }], ["personal", { id: "personal", shareable: false }],
                     ["all-rights-reserved", { id: "all-rights-reserved", shareable: false }]]);
const item = (git, license = "CC0-1.0", external = false) => ({ git, license, external });
const a = d.gitAction(item("untracked"), lic);
eq([a.kind, a.label], ["add", "Add to git"], "unpublished: Add to git");
ok(a.title.includes("Publishes the image and its entry"), "shareable tooltip: " + a.title);
const m = d.gitAction(item("untracked", "all-rights-reserved"), lic);
ok(m.title.includes("Publishes its entry only (the image stays download-only)"), "download-only tooltip: " + m.title);
eq(d.gitAction(item("deleted"), lic).kind, "add", "a removal staged: publish again");
for (const st of ["committed", "staged", "modified", "metadata", "metadata-staged", "conflict"]) {
  eq(d.gitAction(item(st, st.startsWith("metadata") ? "all-rights-reserved" : "CC0-1.0"), lic).kind, "remove", st);
}
eq(d.gitAction(item("committed"), lic).label, "Remove from git", "label");
eq(d.gitAction(item("untracked", "personal"), lic), null, "personal: never published");
eq(d.gitAction(item("untracked", "CC0-1.0", true), lic), null, "external collection: no action");
eq(d.gitAction(item("pending"), lic), null, "unknown state: no action");
eq(d.gitAction(item("committed", "personal"), lic).kind, "remove", "a published personal one can still be removed");
""")

    def test_meta_form_validation(self):
        run_node(self, r"""
const m = await mod("meta-form.js");
const licenses = new Map([
  ["CC0-1.0", { id: "CC0-1.0", shareable: true }], ["personal", { id: "personal", shareable: false }],
  ["all-rights-reserved", { id: "all-rights-reserved", shareable: false }]]);
const base = { folder: "painting/japan", title: "Wave", artist: "", year: "", tags: [], license: "CC0-1.0",
               license_detail: "", source: "", download: "" };
eq(m.metaErrors(base, { mode: "edit", licenses }), {}, "valid entry");
const e = m.metaErrors({ ...base, title: " ", license: "", folder: "", year: "soon", source: "ftp://x" }, { mode: "edit", licenses });
eq(Object.keys(e).sort(), ["folder", "license", "source", "title", "year"], "each problem on its field");
ok(/download/i.test(m.metaErrors({ ...base, license: "all-rights-reserved" }, { mode: "edit", licenses }).download ?? ""),
   "a non-shareable license needs a download URL, and says why");
eq(m.metaErrors({ ...base, license: "all-rights-reserved", download: "https://x.org/a.jpg" }, { mode: "edit", licenses }), {},
   "with a download URL it is fine");
eq(m.metaErrors({ ...base, license: "personal" }, { mode: "edit", licenses }), {}, "personal files need none");
ok(m.metaErrors({ ...base, license: "all-rights-reserved" }, { mode: "add", licenses, hasDownload: false }).license,
   "add: a non-shareable license needs an upload from a URL");
eq(m.metaErrors({ ...base, license: "all-rights-reserved" }, { mode: "add", licenses, hasDownload: true }), {},
   "add from a URL is fine");
ok(m.metaErrors({ ...base, folder: "painting" }, { mode: "add", licenses, folders: new Set(["painting/japan"]) }).folder,
   "only a listed collection folder can be chosen");
eq(m.metaErrors(base, { mode: "add", licenses, folders: new Set(["painting/japan"]) }), {}, "a listed folder is fine");
ok(/letter|digit/i.test(m.metaErrors({ ...base, title: "!!!", artist: "" }, { mode: "edit", licenses }).title ?? ""),
   "a title that cannot name the file is refused");
eq(m.metaErrors({ ...base, title: "!!!", artist: "Hokusai" }, { mode: "edit", licenses }), {}, "the artist can name it");
eq(m.metaErrors({ ...base, title: "!!!" }, { mode: "edit", licenses, external: true }), {}, "external files keep their names");
eq(m.fileName("Claude Monet", "Water Lilies", 1906, ".jpg"), "claude-monet_water-lilies_1906.jpg", "file name");
eq(m.guessFromName("claude-monet_water-lilies_1906.jpg"), { artist: "Claude Monet", title: "Water Lilies", year: 1906 },
   "guess from a file name");
""")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TagsUiJsTest(unittest.TestCase):
    def test_tile_size_independent_of_the_details_pane(self):
        run_node(self, r"""
const v = await mod("virtual-grid.js");
for (const vw of [1920, 1366, 1280, 1100, 2560]) {
  const { closed, open } = v.galleryWidths(vw);
  ok(open < closed, `${vw}: the pane narrows the gallery (${closed} -> ${open})`);
  const fixed = v.fixedTile(vw);
  const a = v.layout(closed, fixed), b = v.layout(open, fixed);
  eq([a.tileW, a.thumbH, a.gap, a.rowH], [b.tileW, b.thumbH, b.gap, b.rowH], `${vw}: same tile with the pane open or closed`);
  ok(a.columns > b.columns, `${vw}: only the column count changes (${a.columns} -> ${b.columns})`);
  for (const [w, l] of [[closed, a], [open, b]]) {
    ok(l.columns * l.tileW + (l.columns - 1) * l.gap <= w, `${vw}: tiles fit ${w}`);
  }
  ok(closed - (a.columns * a.tileW + (a.columns - 1) * a.gap) < a.tileW / 2, `${vw}: the closed gallery is nearly filled`);
}
eq(v.layout(v.galleryWidths(1366).open, v.fixedTile(1366)).columns, 2, "1366 with the pane: two columns, not one");
const phone = v.galleryWidths(600);
eq(phone.open, phone.closed, "narrow windows: the pane floats over the gallery");
eq(v.layout(300, { tileW: 400, gap: 12 }).tileW, 300, "a tile never overflows a narrower gallery");
eq(v.layout(1640).columns, v.layout(1640, null).columns, "no fixed tile: the stretch layout as before");
""")

    def test_tag_filter_helpers(self):
        run_node(self, r"""
const t = await mod("toolbar.js");
eq(t.toggleTag([], "Night Sky"), ["night-sky"], "toggle adds, slugged");
eq(t.toggleTag(["a", "b"], "a"), ["b"], "toggle removes");
eq(t.toggleTag(["a"], " ! "), ["a"], "nothing letter-like: unchanged");
eq(t.withTag(["a"], "b"), ["a", "b"], "a details chip adds");
eq(t.withTag(["a", "b"], "a"), ["a", "b"], "adding a selected tag keeps the selection");
eq(t.tagsLabel([]), "Tags", "button label without tags");
eq(t.tagsLabel(["a", "b"]), "Tags · 2", "button label with the count");
const needs = [1000, 820, 700];
eq(t.collapseLevel(1200, needs), 0, "room: full labels");
eq(t.collapseLevel(1000, needs), 0, "exactly enough");
eq(t.collapseLevel(900, needs), 1, "labels become icons");
eq(t.collapseLevel(750, needs), 2, "sort goes into the overflow menu");
eq(t.collapseLevel(400, needs), 2, "never past the last level");
eq(t.collapseLevel(0, [0, 0, 0]), 0, "nothing measured (no layout): full labels");
const m = await mod("select-menu.js");
const rows = [{ tag: "blue", count: 9 }, { tag: "sea", count: 5 }, { tag: "night", count: 2 }];
eq(m.tagOptions(rows, ["night"]).map((o) => o.value), ["night", "blue", "sea"], "selected first, then most used");
eq(m.tagOptions(rows, ["gone"]).map((o) => o.value), ["gone", "blue", "sea", "night"], "a selected tag missing from the list still shows");
eq(m.tagOptions(rows, []).map((o) => o.hint), ["9", "5", "2"], "counts as hints");
eq(m.tagOptions(rows, [], "e").map((o) => o.value), ["blue", "sea"], "search filters by substring");
""")


if __name__ == "__main__":
    unittest.main()
