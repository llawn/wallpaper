// Small themed question dialog (replaces confirm()): a title, a sentence, an optional note and a row of choices.

const $ = (id) => document.getElementById(id);

/**
 * Ask a question; resolves with the chosen button's value, or "cancel" when dismissed with Esc.
 * @param {{title: string, text: string, note?: string,
 *          buttons: {value: string, label: string, kind?: "primary"|"danger"|"quiet", focus?: boolean}[]}} q
 */
export function ask({ title, text, note = "", buttons }) {
  const dialog = $("ask-dialog");
  $("ask-title").textContent = title;
  $("ask-text").textContent = text;
  $("ask-note").textContent = note;
  $("ask-note").hidden = !note;
  const nodes = buttons.map((b) => {
    const el = document.createElement("button");
    el.type = "submit";
    el.value = b.value;
    el.className = b.kind ?? "";
    el.textContent = b.label;
    if (b.focus) el.autofocus = true;
    return el;
  });
  $("ask-buttons").replaceChildren(...nodes);
  dialog.returnValue = "";
  dialog.showModal();
  (nodes.find((n) => n.autofocus) ?? nodes[0])?.focus();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue || "cancel"), { once: true });
  });
}

const files = (n) => `${n} ${n === 1 ? "file" : "files"}`;

/**
 * The delete question for a delete plan ({tracked, untracked, external}).
 * Resolves "remove" | "keep" (git choice for tracked files) or "cancel".
 */
export async function askDelete(plan) {
  const n = plan.tracked.length + plan.untracked.length + plan.external.length;
  const ext = plan.external.length;
  const note = ext
    ? `${files(ext)} ${ext === 1 ? "is" : "are"} in an external collection and will be deleted inside its submodule.`
    : "";
  if (plan.tracked.length) {
    return ask({
      title: `Delete ${files(n)}?`,
      text: `Also remove ${files(plan.tracked.length)} from git?`,
      note,
      buttons: [
        { value: "cancel", label: "Cancel", kind: "quiet", focus: true },  // never default to the destructive choice
        { value: "keep", label: "Keep in git", kind: "" },
        { value: "remove", label: "Remove from git", kind: "danger" },
      ],
    });
  }
  const answer = await ask({
    title: `Delete ${files(n)}?`,
    text: n === 1 ? "The image and its entry are removed from the collection." : "The images and their entries are removed from the collection.",
    note,
    buttons: [
      { value: "cancel", label: "Cancel", kind: "quiet", focus: true },
      { value: "remove", label: "Delete", kind: "danger" },
    ],
  });
  return answer;
}
