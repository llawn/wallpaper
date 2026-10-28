// Full-screen viewer: the image from /api/images fitted to the screen. Left/Right step through the current gallery
// list, a click on the image toggles 100% size, Esc or a click beside the image closes.

import { imageUrl } from "./api.js";
import { yearText } from "./years.js";

/** Index after a step of `delta`, or null at either end (no wrap) or when the position is unknown. */
export function stepIndex(index, delta, total) {
  if (index === null || index === undefined || !total) return null;
  const next = index + delta;
  return next >= 0 && next < total ? next : null;
}

export const positionText = (index, total) =>
  index === null || index === undefined || !total ? "" : `${(index + 1).toLocaleString()} of ${total.toLocaleString()}`;

const $ = (id) => document.getElementById(id);

export class Viewer {
  constructor() {
    this.dialog = $("viewer");
    this.stage = $("viewer-stage");
    this.img = $("viewer-image");
    this.title = $("viewer-title");
    this.meta = $("viewer-meta");
    this.pos = $("viewer-pos");
    this.prev = $("viewer-prev");
    this.next = $("viewer-next");
    this.state = null;
    this.seq = 0;
    this.preload = document.createElement("img");
    $("viewer-close").addEventListener("click", () => this.dialog.close());
    this.prev.addEventListener("click", () => this.step(-1));
    this.next.addEventListener("click", () => this.step(1));
    this.stage.addEventListener("click", (e) => {
      if (e.target === this.img) this.toggleZoom();
      else this.dialog.close();
    });
    this.img.addEventListener("load", () => this.stage.classList.remove("is-loading"));
    this.img.addEventListener("error", () => this.stage.classList.add("is-broken"));
    this.dialog.addEventListener("keydown", (e) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -1, ArrowDown: 1 };
      if (e.key in moves && !this.stage.classList.contains("is-zoomed")) {
        e.preventDefault();
        this.step(moves[e.key]);
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        this.go(e.key === "Home" ? 0 : this.state.total - 1);
      }
    });
    this.dialog.addEventListener("close", () => {
      const st = this.state;
      this.state = null;
      this.img.removeAttribute("src");
      if (st) st.onClose(st.item, st.index);
    });
  }

  get isOpen() {
    return this.dialog.open;
  }

  /**
   * @param {{item: object, index: number|null, total: number, itemAt: (i: number) => Promise<object|null>,
   *          onClose: (item: object, index: number|null) => void}} opts
   */
  open({ item, index, total, itemAt, onClose }) {
    this.state = { item, index, total, itemAt, onClose };
    this.show(item, index);
    if (!this.dialog.open) this.dialog.showModal();
    this.dialog.focus();
  }

  show(item, index) {
    const st = this.state;
    st.item = item;
    st.index = index;
    st.target = index;
    this.stage.classList.remove("is-zoomed", "is-broken");
    this.stage.classList.add("is-loading");
    this.img.src = imageUrl(item);
    this.img.alt = [item.title, item.artist].filter(Boolean).join(" by ");
    this.title.textContent = item.title || item.file;
    this.meta.textContent = [item.artist, yearText(item.year)].filter(Boolean).join(", ");
    this.pos.textContent = positionText(index, st.total);
    this.prev.disabled = stepIndex(index, -1, st.total) === null;
    this.next.disabled = stepIndex(index, 1, st.total) === null;
    this.warm(stepIndex(index, 1, st.total));
  }

  async go(i) {
    const st = this.state;
    if (!st || i === null || i < 0 || i >= st.total) return;
    const token = ++this.seq;
    st.target = i; // a quick second key press steps on from here, not from the image still loading
    const item = await st.itemAt(i);
    if (token !== this.seq || this.state !== st || !item) return;
    this.show(item, i);
  }

  step(delta) {
    const st = this.state;
    if (st) this.go(stepIndex(st.target ?? st.index, delta, st.total));
  }

  /** Start fetching the next image so stepping forward feels instant. */
  async warm(i) {
    const st = this.state;
    if (i === null || !st) return;
    const item = await st.itemAt(i);
    if (item && this.state === st) this.preload.src = imageUrl(item);
  }

  toggleZoom() {
    const on = this.stage.classList.toggle("is-zoomed");
    this.img.title = on ? "Click to fit the screen" : "Click for 100% size";
  }
}
