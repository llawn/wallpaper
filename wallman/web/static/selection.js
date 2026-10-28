// Selection model (pure, no DOM): a set of ref keys ("folder/file") plus the anchor index for Shift ranges.
// Keys, not DOM nodes, so the selection survives virtual scrolling and node recycling. States are immutable.

/** @typedef {{keys: Set<string>, anchor: number|null}} SelectionState */

/** @returns {SelectionState} */
export function emptySelection() {
  return { keys: new Set(), anchor: null };
}

/** [lo, hi] index range a Shift-click on `index` covers (the anchor to the index, or just the index). */
export function rangeOf(state, index) {
  const a = state.anchor ?? index;
  return [Math.min(a, index), Math.max(a, index)];
}

/**
 * Apply a click (or the keyboard equivalent).
 * - plain: select only this item; clicking the only selected item deselects it.
 * - ctrl/cmd: toggle this item, the anchor moves here.
 * - shift: select the range from the anchor (replaces the selection; anchor kept).
 * - ctrl+shift: add the range from the anchor to the selection.
 * `rangeKeys` (needed with shift and an anchor) are the keys of `rangeOf(state, index)`, in order.
 * @returns {SelectionState}
 */
export function select(state, { index, key, ctrl = false, shift = false, rangeKeys = null }) {
  if (shift && state.anchor !== null) {
    const keys = ctrl ? new Set(state.keys) : new Set();
    for (const k of rangeKeys ?? [key]) keys.add(k);
    return { keys, anchor: state.anchor };
  }
  if (ctrl) {
    const keys = new Set(state.keys);
    if (!keys.delete(key)) keys.add(key);
    return { keys, anchor: index };
  }
  if (state.keys.size === 1 && state.keys.has(key)) return { keys: new Set(), anchor: index };
  return { keys: new Set([key]), anchor: index };
}

/** Esc: nothing selected, no anchor. */
export function clear() {
  return emptySelection();
}

/** Replace the selection by these keys (select all, reveal after add); the anchor goes to `anchor`. */
export function selectKeys(state, keys, anchor = null) {
  return { keys: new Set(keys), anchor };
}

/** Drop keys that no longer exist (after delete or rename). */
export function without(state, gone) {
  const keys = new Set([...state.keys].filter((k) => !gone.has(k)));
  return { keys, anchor: state.anchor };
}

/** "a/b/c.jpg" -> {folder: "a/b", file: "c.jpg"} (file names never contain "/"). */
export function keyToRef(key) {
  const i = key.lastIndexOf("/");
  return { folder: key.slice(0, i), file: key.slice(i + 1) };
}
