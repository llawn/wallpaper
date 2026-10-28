// Entry point: wires tree, toolbar, gallery, details, viewer, the floating selection bar and the theme watcher, and
// owns the global keyboard shortcuts.

import { api, bump, createSequence, imageUrl, listQuery, refKey } from "./api.js";
import { AddDialog } from "./add-dialog.js";
import { cropOptions, openCropDialog } from "./cropper.js";
import { Details } from "./details.js";
import { askDelete } from "./dialogs.js";
import { Gallery } from "./gallery.js";
import { keyToRef } from "./selection.js";
import { StatusBar, toast, toastError } from "./status.js";
import { Toolbar } from "./toolbar.js";
import { Tree } from "./tree.js";
import { Viewer } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const STATUS_POLL_MS = 5000;

const state = { folder: "", filters: null };
let tree, toolbar, gallery, details, addDialog, statusBar, viewer;

// --- gallery head and empty state -----------------------------------------------------------------
function updateHead(total = gallery.total, known = true, error = null) {
  $("gallery-title").textContent = state.folder || "All wallpapers";
  $("gallery-count").textContent = known && !error ? plural(total, "wallpaper") : "";
  const empty = $("gallery-empty");
  empty.hidden = !known || total > 0;
  empty.classList.toggle("is-error", Boolean(error));
  empty.textContent = error
    ? `These filters could not be applied: ${String(error.message).replace(/\.?$/, ".")} Change or clear them to see wallpapers again.`
    : toolbar.active
      ? "No wallpapers match these filters."
      : "Nothing here yet. Press a or use Add to bring one in.";
}

/** Show the folder and filters; with an unsaved edit kept ("Keep editing") the query waits until the edit ends. */
let queryWaiting = false;
function applyQuery() {
  const go = details.confirmDiscard(); // plain true unless an unsaved edit has to be confirmed
  if (go === true) return showQuery();
  return go.then((ok) => (ok ? showQuery() : (queryWaiting = true)));
}

function showQuery() {
  queryWaiting = false;
  gallery.setQuery(listQuery({ folder: state.folder, ...state.filters }));
}

// --- selection: details pane for one, floating bar for several -------------------------------------
const detailRequests = createSequence();

function onSelection(keys) {
  return updateSelection(keys);
}

async function updateSelection(keys) {
  const n = keys.size;
  const bar = $("multibar");
  const hiding = !bar.hidden && n < 2;
  const focusInBar = bar.contains(document.activeElement);
  bar.hidden = n < 2;
  if (hiding && focusInBar) $("grid").focus({ preventScroll: true }); // never leave the focus on a hidden button
  $("multibar-count").textContent = `${n.toLocaleString()} selected`;
  const one = n === 1 ? [...keys][0] : null;
  if (!one) {
    detailRequests.next();
    showDetails(null);
    return;
  }
  if (details.current && refKey(details.current) === one) return showDetails(details.current);
  const token = detailRequests.next();
  try {
    const full = await api.wallpaper(keyToRef(one));
    if (detailRequests.isCurrent(token)) showDetails(full);
  } catch (err) {
    if (detailRequests.isCurrent(token)) toastError(err);
  }
}

function showDetails(item) {
  $("details").hidden = !item;
  $("shell").classList.toggle("with-details", Boolean(item));
  details.show(item ? [item] : []);
}

const selectedRefs = () => [...gallery.selected].map(keyToRef);

// --- actions --------------------------------------------------------------------------------------
async function refreshAfterChange(gone) {
  await gallery.reload(gone); // the visible tiles first: the full status costs seconds on a big collection
  const results = await Promise.allSettled([statusBar.refresh(), loadFolders()]);
  for (const r of results) if (r.status === "rejected") toastError(r.reason);
}

/** PATCH the changes; resolves with the updated entry, or null after showing the error. */
async function save(item, changes) {
  let updated;
  try {
    updated = await api.update(item, changes);
  } catch (err) {
    toastError(err);
    return null;
  }
  details.saved(item, updated); // new baseline even when the file key is unchanged
  const moved = updated.folder !== item.folder;
  const renamed = refKey(updated) !== refKey(item);
  toast(moved ? `Moved to ${updated.folder}` : renamed ? `Saved as ${updated.file}` : "Saved");
  // the old key is gone: select the new one first (details already show it, so nothing is refetched), or
  // nothing when the image left the folder shown
  if (moved && state.folder && !`${updated.folder}/`.startsWith(`${state.folder}/`)) gallery.clear();
  else if (renamed) gallery.selectKeys([refKey(updated)]);
  try {
    await refreshAfterChange();
  } catch (err) {
    toastError(err);
  }
  return updated;
}

// --- viewer, tag and folder links from the details pane ---------------------------------------------
function openViewer(item) {
  const start = gallery.locate(refKey(item));
  viewer.open({
    item, index: start, total: gallery.total, itemAt: (i) => gallery.itemAt(i),
    onClose: (last, index) => {
      if (index !== null && index !== start) gallery.selectIndex(index); // the selection follows what was viewed
    },
  });
}

function openFolder(folder) {
  tree.reveal(folder);
  state.folder = folder;
  applyQuery();
}

async function crop(item) {
  // download entries are cropped from the original download (box in its pixels); rendered SVGs from the current file
  const { fromOriginal, ...options } = cropOptions(item, imageUrl);
  let box;
  try {
    box = await openCropDialog(options);
  } catch (err) {
    return toastError(fromOriginal ? new Error(`Cannot crop: the original at ${item.download} did not load in the browser.`) : err);
  }
  if (!box) return;
  try {
    const updated = await api.crop(item, box);
    bump(updated);
    details.saved(item, updated);
    showDetails(updated);
    toast(`Cropped to ${updated.resolution[0]} × ${updated.resolution[1]}`);
    await refreshAfterChange();
  } catch (err) {
    toastError(err);
  }
}

async function remove(refs) {
  if (!refs.length) return;
  try {
    const plan = await api.deletePlan(refs);
    const git = await askDelete(plan);
    if (git === "cancel") return;
    const { deleted } = await api.remove(refs, git);
    toast(`Deleted ${plural(deleted, "wallpaper")}`);
    await refreshAfterChange(new Set(refs.map(refKey)));
  } catch (err) {
    toastError(err);
  }
}

async function gitAction(kind, refs = selectedRefs()) {
  if (!refs.length) return;
  try {
    const { refs: done } = kind === "add" ? await api.gitAdd(refs) : await api.gitRemove(refs);
    const verb = kind === "add" ? "Added" : "Removed";
    const skipped = refs.length - done.length;
    toast(`${verb} ${plural(done.length, "wallpaper")} ${kind === "add" ? "to" : "from"} git${skipped ? ` (${skipped} unchanged)` : ""}`);
    const shown = details.current;
    if (shown && refs.some((r) => refKey(r) === refKey(shown))) {
      details.saved(shown, await api.wallpaper(shown)); // its new git state (and so the other button)
    }
    await refreshAfterChange();
  } catch (err) {
    toastError(err);
  }
}

// --- data that changes rarely ---------------------------------------------------------------------
async function loadFolders() {
  const folders = await api.folders();
  tree.render(folders);
  details.setCollections(folders);
  addDialog.setFolders(folders);
}

// --- theme: reload the palette when its config changes ---------------------------------------------
let themeMtime = null;

function watchTheme(mtime) {
  if (mtime === undefined || mtime === null) return;
  if (themeMtime !== null && mtime !== themeMtime) {
    for (const [id, scheme] of [["theme-dark", "dark"], ["theme-light", "light"]]) {
      $(id).href = `/api/theme?scheme=${scheme}&t=${encodeURIComponent(mtime)}`;
    }
  }
  themeMtime = mtime;
}

/** Theme poll every 5 s (cheap ?only=theme). The full status (validator + git) runs at start, after every change
 *  and when the window regains focus: at 10,000 images it costs seconds of server CPU, too much for a 5 s poll. */
function pollStatus() {
  setTimeout(async () => {
    if (!document.hidden) {
      try {
        const theme = await api.themeStatus();
        watchTheme(theme.theme_mtime);
        statusBar.showNotes(theme);
      } catch {
        // the status bar reports an unreachable server on its next full refresh
      }
    }
    pollStatus();
  }, STATUS_POLL_MS);
}

let lastFull = Date.now();
function refreshStatusOnReturn() {
  const go = () => {
    if (document.hidden || Date.now() - lastFull < STATUS_POLL_MS) return;
    lastFull = Date.now();
    statusBar.refresh({ quiet: true }).then((d) => d && watchTheme(d.theme_mtime));
  };
  window.addEventListener("focus", go);
  document.addEventListener("visibilitychange", go);
}

// --- keyboard -------------------------------------------------------------------------------------
function typing(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName));
}

function onKey(e) {
  if (document.querySelector("dialog[open]")) return;
  if (details.editing) {
    // while editing only Esc acts (the cancel path); c, a, e, Delete and / never act behind the form
    if (e.key === "Escape" && !e.defaultPrevented && !typing(e.target)) {
      e.preventDefault();
      details.cancel();
    }
    return;
  }
  if (e.key === "Escape") {
    if (typing(e.target)) {
      if (e.target === $("search")) {
        e.target.blur();
        $("grid").focus();
      }
      return;
    }
    if (e.target.closest?.(".menu")) return;
    gallery.clear();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
  const one = () => details.current;
  const actions = {
    "/": () => $("search").focus(),
    a: () => addDialog.open(),
    Delete: () => remove(selectedRefs()),
    c: () => (one() ? crop(one()) : toast("Select one wallpaper to crop it.")),
    e: () => (one() ? details.edit() : toast("Select one wallpaper to edit it.")),
  };
  const action = actions[e.key];
  if (!action) return;
  e.preventDefault();
  action();
}

function wireWindowDrop() {
  const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes("Files");
  window.addEventListener("dragover", (e) => {
    if (hasFiles(e)) e.preventDefault();
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    const handled = e.defaultPrevented; // the add dialog's own drop zone took it
    e.preventDefault(); // never let the browser navigate to a dropped file
    const file = e.dataTransfer.files[0];
    if (handled || !file) return;
    if (addDialog.acceptsFile()) addDialog.fromFile(file);
    else if (!document.querySelector("dialog[open]")) addDialog.open(file);
  });
}

function onListError(err) {
  if (/year/i.test(err?.message ?? "")) toolbar.yearRejected();
  toastError(err);
}

async function main() {
  statusBar = new StatusBar();
  let licenses;
  try {
    licenses = await api.licenses();
  } catch (err) {
    $("gallery-empty").hidden = false;
    $("gallery-empty").textContent = err.message;
    return toastError(err);
  }
  toolbar = new Toolbar({
    licenses,
    onChange: (filters) => {
      state.filters = { ...filters };
      applyQuery();
    },
  });
  state.filters = { ...toolbar.filters };
  gallery = new Gallery({
    scroller: $("grid"),
    canvas: $("grid-canvas"),
    onSelection,
    onOpen: async (key) => {
      const i = gallery.locate(key);
      const item = i === null ? null : await gallery.itemAt(i);
      if (item && !viewer.isOpen) openViewer(item);
    },
    onTotal: (total, known, error) => updateHead(total, known, error),
    onError: onListError,
    onNotice: (msg) => toast(msg),
    canLeave: () => details.confirmDiscard(),
  });
  viewer = new Viewer();
  details = new Details({
    licenses, onSave: save, onCrop: crop, onDelete: (items) => remove(items), onView: openViewer,
    onGit: (item, kind) => gitAction(kind, [{ folder: item.folder, file: item.file }]),
    onTag: (tag) => toolbar.addTag(tag), onFolder: openFolder,
    onLeave: () => {
      if (queryWaiting) showQuery();
    },
  });
  addDialog = new AddDialog({
    licenses,
    currentFolder: () => state.folder,
    onAdded: async (item) => {
      await refreshAfterChange();
      // the refresh takes a while: never pull the selection away from an edit started meanwhile
      if (!details.editing) gallery.selectKeys([refKey(item)]);
    },
    onGitAdded: () => refreshAfterChange(),
  });
  tree = new Tree($("tree"), (folder) => {
    state.folder = folder;
    applyQuery();
  }, { beforeSelect: () => details.confirmDiscard() });
  wireWindowDrop();
  $("add-button").addEventListener("click", () => addDialog.open());
  $("multi-delete").addEventListener("click", () => remove(selectedRefs()));
  $("multi-git-add").addEventListener("click", () => gitAction("add"));
  $("multi-git-remove").addEventListener("click", () => gitAction("remove"));
  $("multi-clear").addEventListener("click", () => gallery.clear());
  document.addEventListener("keydown", onKey);
  if (matchMedia("(max-width: 900px)").matches) $("tree-fold").open = false;
  applyQuery();
  const [status] = await Promise.allSettled([statusBar.refresh(), loadFolders().catch(toastError)]);
  if (status.status === "fulfilled" && status.value) watchTheme(status.value.theme_mtime);
  pollStatus();
  refreshStatusOnReturn();
}

main();
