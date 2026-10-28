// 16:9 crop box in source pixels: pure maths (tested under node) and the canvas dialog that drives it.

export const RATIO = 16 / 9;
export const MIN_W = 64;
export const MIN_OUT = { w: 1800, h: 900 };

const heightFor = (w) => Math.round((w * 9) / 16);
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

/** Widest 16:9 width that fits in a `w`×`h` area. */
function maxWidth(w, h) {
  return Math.max(1, Math.min(Math.floor(w), Math.floor((h * 16) / 9)));
}

/** Largest centred 16:9 box. */
export function defaultBox(width, height) {
  const w = maxWidth(width, height);
  const h = Math.min(heightFor(w), height);
  return { x: Math.floor((width - w) / 2), y: Math.floor((height - h) / 2), w, h };
}

/** Same box, shrunk to fit if needed, then moved inside the image. Integers, 16:9. */
export function clampBox(box, width, height) {
  let w = Math.round(box.w);
  let h = heightFor(w);
  if (w > width || h > height) {
    w = maxWidth(width, height);
    h = Math.min(heightFor(w), height);
  }
  return {
    x: clamp(Math.round(box.x), 0, width - w),
    y: clamp(Math.round(box.y), 0, height - h),
    w,
    h,
  };
}

export function moveBox(box, dx, dy, width, height) {
  return clampBox({ ...box, x: box.x + dx, y: box.y + dy }, width, height);
}

/**
 * Drag corner `corner` ("nw" | "ne" | "sw" | "se") to (px, py), source pixels.
 * The opposite corner stays put; the size follows the farther axis, bounded by the image.
 */
export function resizeBox(box, corner, px, py, width, height) {
  const east = corner.includes("e");
  const south = corner.includes("s");
  const ax = east ? box.x : box.x + box.w;
  const ay = south ? box.y : box.y + box.h;
  const dx = Math.max(0, east ? px - ax : ax - px);
  const dy = Math.max(0, south ? py - ay : ay - py);
  const limit = maxWidth(east ? width - ax : ax, south ? height - ay : ay);
  const w = clamp(Math.round(Math.max(dx, (dy * 16) / 9)), Math.min(MIN_W, limit), limit);
  const h = heightFor(w);
  return { x: east ? ax : ax - w, y: south ? ay : ay - h, w, h };
}

export function resizeCentred(box, factor, width, height) {
  const w = clamp(Math.round(box.w * factor), Math.min(MIN_W, width), maxWidth(width, height));
  const h = heightFor(w);
  return clampBox({ x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h }, width, height);
}

/**
 * Crop-dialog options for a listed wallpaper. Download entries are re-cropped from the original, so the box is in
 * its pixels (the browser loads the download URL). Rendered entries (SVG originals) are the exception: the browser
 * would measure the SVG's natural size, not the 3840×2160 render the server works on, so they are cropped from the
 * current rendered file and the server composes the box with the previous one.
 */
export function cropOptions(item, imageUrl) {
  const rendered = (item.processing || []).includes("render");
  if (item.download && !rendered) {
    return { fromOriginal: true, src: item.download,
             initial: item.crop && { x: item.crop[0], y: item.crop[1], w: item.crop[2], h: item.crop[3] },
             heading: "Crop the original download to 16:9" };
  }
  return { fromOriginal: false, src: imageUrl(item), width: item.resolution[0], height: item.resolution[1],
           heading: rendered ? "Crop the rendered image to 16:9 (SVG originals are re-cropped from the current render)"
                             : "Crop to 16:9" };
}

export const outputOk = (box) => box.w >= MIN_OUT.w && box.h >= MIN_OUT.h;

/** Scale that fits a `width`×`height` image in a `maxW`×`maxH` view, never enlarging. */
export function viewScale(width, height, maxW, maxH) {
  const scale = Math.min(maxW / width, maxH / height, 1);
  return { scale, width: Math.round(width * scale), height: Math.round(height * scale) };
}

// --- dialog ---------------------------------------------------------------------------------------

const HANDLE = 10; // CSS px hit radius around a corner

function hitTest(box, s, x, y) {
  const corners = { nw: [box.x, box.y], ne: [box.x + box.w, box.y], sw: [box.x, box.y + box.h], se: [box.x + box.w, box.y + box.h] };
  for (const [name, [cx, cy]] of Object.entries(corners)) {
    if (Math.abs(cx * s - x) <= HANDLE && Math.abs(cy * s - y) <= HANDLE) return name;
  }
  const inside = x >= box.x * s && x <= (box.x + box.w) * s && y >= box.y * s && y <= (box.y + box.h) * s;
  return inside ? "move" : null;
}

function loadImage(src) {
  return new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    img.decoding = "async";
    img.addEventListener("load", () => resolve(img), { once: true });
    img.addEventListener("error", () => resolve(null), { once: true });
    img.src = src;
  });
}

/**
 * Open the crop dialog for an image of `width`×`height` source pixels (omit them to use the image's
 * natural size, which then must load). Resolves to {x, y, w, h} integers, or null when cancelled.
 */
export async function openCropDialog({ src, width, height, initial = null, heading = "Crop to 16:9", confirm = "Apply crop" }) {
  const img = await loadImage(src);
  if (!width || !height) {
    if (!img) throw new Error("The image could not be loaded in the browser, so its size is unknown.");
    width = img.naturalWidth;
    height = img.naturalHeight;
  }
  const dialog = document.getElementById("crop-dialog");
  const canvas = dialog.querySelector("#crop-canvas");
  const preview = dialog.querySelector("#crop-preview");
  const out = dialog.querySelector("#crop-output");
  const stage = dialog.querySelector(".crop-stage");
  dialog.querySelector("#crop-title").textContent = heading;
  dialog.querySelector("#crop-confirm").textContent = confirm;
  dialog.querySelector("#crop-source").textContent = `Source ${width} × ${height}`;

  let box = initial ? clampBox(initial, width, height) : defaultBox(width, height);
  let view = { scale: 1, width: 1, height: 1 };
  let drag = null;
  const ctx = canvas.getContext("2d");
  const pctx = preview.getContext("2d");
  dialog.showModal();

  function layout() {
    const r = stage.getBoundingClientRect();
    view = viewScale(width, height, Math.max(r.width, 200), Math.max(r.height, 150));
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = `${view.width}px`;
    canvas.style.height = `${view.height}px`;
    canvas.width = Math.round(view.width * dpr);
    canvas.height = Math.round(view.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function draw() {
    const s = view.scale;
    const css = getComputedStyle(dialog);
    ctx.clearRect(0, 0, view.width, view.height);
    if (img) ctx.drawImage(img, 0, 0, view.width, view.height);
    else {
      ctx.fillStyle = css.getPropertyValue("--wall");
      ctx.fillRect(0, 0, view.width, view.height);
    }
    const [bx, by, bw, bh] = [box.x * s, box.y * s, box.w * s, box.h * s];
    ctx.fillStyle = "rgb(0 0 0 / 0.55)";
    ctx.beginPath();
    ctx.rect(0, 0, view.width, view.height);
    ctx.rect(bx, by, bw, bh);
    ctx.fill("evenodd");
    const ok = outputOk(box);
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
    ctx.strokeStyle = "rgb(255 255 255 / 0.35)";
    ctx.beginPath();
    for (const t of [1 / 3, 2 / 3]) {
      ctx.moveTo(bx + bw * t, by); ctx.lineTo(bx + bw * t, by + bh);
      ctx.moveTo(bx, by + bh * t); ctx.lineTo(bx + bw, by + bh * t);
    }
    ctx.stroke();
    ctx.fillStyle = "#fff";
    for (const [cx, cy] of [[bx, by], [bx + bw, by], [bx, by + bh], [bx + bw, by + bh]]) {
      ctx.fillRect(cx - 4, cy - 4, 8, 8);
    }
    out.textContent = `Output ${box.w} × ${box.h}${ok ? "" : " (below 1800 × 900)"}`;
    out.classList.toggle("too-small", !ok);
    pctx.clearRect(0, 0, preview.width, preview.height);
    if (img) pctx.drawImage(img, box.x, box.y, box.w, box.h, 0, 0, preview.width, preview.height);
  }

  const point = (e) => {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  function onDown(e) {
    const [x, y] = point(e);
    const hit = hitTest(box, view.scale, x, y);
    if (!hit) return;
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    drag = { hit, x, y, start: box };
  }
  function onMove(e) {
    const [x, y] = point(e);
    if (!drag) {
      const hit = hitTest(box, view.scale, x, y);
      canvas.style.cursor = hit === "move" ? "move" : hit ? `${hit}-resize` : "default";
      return;
    }
    const s = view.scale;
    box = drag.hit === "move"
      ? moveBox(drag.start, (x - drag.x) / s, (y - drag.y) / s, width, height)
      : resizeBox(drag.start, drag.hit, x / s, y / s, width, height);
    draw();
  }
  function onUp(e) {
    if (drag && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    drag = null;
  }
  function onKey(e) {
    const step = e.shiftKey ? Math.max(10, Math.round(width / 50)) : 1;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[e.key]) box = moveBox(box, ...moves[e.key], width, height);
    else if (e.key === "+" || e.key === "=") box = resizeCentred(box, 1.05, width, height);
    else if (e.key === "-") box = resizeCentred(box, 1 / 1.05, width, height);
    else if (e.key === "0") box = defaultBox(width, height);
    else return;
    e.preventDefault();
    draw();
  }

  const observer = new ResizeObserver(layout);
  observer.observe(stage);
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("keydown", onKey);
  layout();
  canvas.focus();

  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      observer.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("keydown", onKey);
      const applied = dialog.returnValue === "apply";
      dialog.returnValue = "";
      resolve(applied ? { ...box } : null);
    }, { once: true });
  });
}
