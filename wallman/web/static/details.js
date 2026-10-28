// Details pane for one wallpaper. Read mode (the default) is flat: the picture, its label and links, no input
// boxes. "Edit" switches to the shared metadata form; Save or Cancel (Esc) goes back. The helpers at the top are
// pure and tested under node.

import { refKey, thumbUrl } from "./api.js";
import { ask } from "./dialogs.js";
import { MetaForm, splitLabel } from "./meta-form.js";

export { splitLabel };
import { toast } from "./status.js";
import { yearText } from "./years.js";

const squash = (s) => String(s ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** The license detail as a secondary line under the license, or "" when it only repeats the license. */
export function licenseNote(detail, lic) {
  const d = String(detail ?? "").trim();
  if (!d) return "";
  if (lic && (squash(d) === squash(lic.label) || squash(d) === squash(lic.id))) return "";
  return d;
}

/** The URL normalised when it is http(s), else null (never a javascript:, data: or relative link). */
export function safeHref(url) {
  if (!url) return null;
  try {
    const u = new URL(String(url));
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** A link as people read it: host without www, then the path, shortened to `max` characters. */
export function displayUrl(url, max = 48) {
  let text = String(url ?? "");
  try {
    const u = new URL(text);
    let path = u.pathname.replace(/\/+$/, "");
    try {
      path = decodeURIComponent(path);
    } catch {
      // keep the encoded path
    }
    text = u.host.replace(/^www\./, "") + path;
  } catch {
    // not a URL: shown as it is
  }
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function button(text, cls, fn) {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", fn);
  return b;
}

function outLink(href, text, label) {
  const a = el("a", "out-link", text);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  if (label) a.setAttribute("aria-label", label);
  return a;
}

export class Details {
  /**
   * @param {{licenses: object[], onSave: (item, changes) => Promise<object|null>, onCrop, onDelete,
   *          onView: (item) => void, onTag: (tag: string) => void, onFolder: (folder: string) => void,
   *          onLeave?: () => void}} opts
   *        onSave resolves with the updated entry, or null when the save failed (the error is already shown);
   *        onLeave runs whenever edit mode ends
   */
  constructor({ licenses, onSave, onCrop, onDelete, onView, onTag, onFolder, onLeave = () => {} }) {
    this.pane = document.getElementById("details");
    this.image = document.getElementById("details-image");
    this.fileButton = document.getElementById("details-file");
    this.size = document.getElementById("details-size");
    this.read = document.getElementById("details-read");
    this.readActions = document.getElementById("details-actions");
    this.licenses = new Map(licenses.map((l) => [l.id, l]));
    Object.assign(this, { onSave, onTag, onFolder, onView, onLeave });
    this.item = null;
    this.editing = false;

    this.fileButton.addEventListener("click", () => this.item && onView(this.item));
    this.image.addEventListener("click", () => this.item && !this.editing && onView(this.item));
    this.editButton = button("Edit", "edit-btn", () => this.edit());
    this.editButton.setAttribute("aria-keyshortcuts", "e");
    const crop = button("Crop…", "quiet", () => this.item && onCrop(this.item));
    crop.setAttribute("aria-keyshortcuts", "c");
    const del = button("Delete", "quiet danger-text", () => this.item && onDelete([this.item]));
    del.setAttribute("aria-keyshortcuts", "Delete");
    this.readActions.replaceChildren(this.editButton, crop, el("span", "spacer"), del);

    this.cancelButton = button("Cancel", "quiet", () => this.cancel());
    this.form = new MetaForm(document.getElementById("details-form"), {
      mode: "edit", licenses, submitLabel: "Save changes", extraButtons: [this.cancelButton],
      onSubmit: () => this.save(),
    });
    this.form.form.hidden = true;
    this.pane.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !this.editing || e.defaultPrevented) return;
      e.preventDefault();
      e.stopPropagation(); // Esc here cancels the edit; it does not clear the gallery selection
      this.cancel();
    });
  }

  setCollections(folders) {
    this.form.setFolders(folders);
  }

  /** The entry shown, or null. */
  get current() {
    return this.item;
  }

  /** Show one wallpaper (full entry). The same entry shown again keeps an edit in progress; another one ends it. */
  show(items) {
    const item = items.length === 1 ? items[0] : null;
    const same = item && this.item && refKey(this.item) === refKey(item);
    if (this.editing && !same) {
      if (this.form.dirty) toast(`Unsaved edits to “${this.item.title}” were discarded.`);
      this.setMode(false);
    }
    this.item = item;
    if (!item) return;
    this.image.src = thumbUrl(item);
    this.image.alt = [item.title, item.artist].filter(Boolean).join(" by ");
    this.fileButton.textContent = item.file;
    this.fileButton.setAttribute("aria-label", `View ${item.file} full screen`);
    const [w, h] = item.resolution ?? [];
    this.size.textContent = w && h ? `${w} × ${h}` : "";
    if (!this.editing) this.render(item);
  }

  /** After a successful save or crop: the returned entry becomes what is shown (and the form's baseline). */
  saved(before, after) {
    if (!this.item || refKey(this.item) !== refKey(before)) return;
    const editing = this.editing;
    this.editing = false; // show() must not treat a renamed entry as another wallpaper
    this.item = after;
    this.show([after]);
    this.editing = editing;
    if (!editing) return;
    if (this.form.dirty && !this.saving) {
      // a crop while editing: keep what the person typed; only the file the form describes changed
      const dot = after.file.lastIndexOf(".");
      this.form.context = { ...this.form.context, ext: dot >= 0 ? after.file.slice(dot) : "", file: after.file };
      this.form.validate();
    } else {
      this.fillForm(after);
    }
  }

  // --- read mode --------------------------------------------------------------------------------
  render(item) {
    const lic = this.licenses.get(item.license);
    const nodes = [el("p", "label-artist", item.artist || "Unknown artist")];
    const title = el("h3", "label-title");
    title.append(el("cite", "", item.title || item.file));
    if (item.year) title.append(el("span", "label-year", yearText(item.year)));
    nodes.push(title);

    if (item.tags?.length) {
      const ul = el("ul", "label-tags");
      ul.setAttribute("aria-label", "Tags");
      for (const t of item.tags) {
        const li = el("li");
        const b = button(t, "tag", () => this.onTag(t));
        b.setAttribute("aria-label", `Add tag ${t} to the filter`);
        b.title = `Add ${t} to the tag filter`;
        li.append(b);
        ul.append(li);
      }
      nodes.push(ul);
    }

    const facts = el("dl", "facts");
    const fact = (name, ...values) => {
      const row = el("div", "fact");
      const dd = el("dd");
      dd.append(...values);
      row.append(el("dt", "", name), dd);
      facts.append(row);
    };
    const [licName, licTerms] = splitLabel(lic ? lic.label : item.license);
    const licLink = outLink(lic?.doc ?? `/docs/licenses/${encodeURIComponent(item.license)}`, licName,
                            `${licName}: what the license allows (opens in a new tab)`);
    licLink.id = "details-license";
    const note = licenseNote(item.license_detail, lic);
    fact("License", licLink, ...(note ? [el("span", "fact-holder", note)] : []),
         ...(licTerms ? [el("span", "fact-note", licTerms)] : []));
    for (const [name, key] of [["Source", "source"], ["Download", "download"]]) {
      const href = safeHref(item[key]);
      if (href) {
        const a = outLink(href, displayUrl(href), `${name}: ${href} (opens in a new tab)`);
        a.id = `details-${key}`;
        a.title = href;
        fact(name, a);
      } else if (item[key]) {
        fact(name, el("span", "fact-plain", item[key]));
      }
    }
    const folder = button(item.folder, "link-btn", () => this.onFolder(item.folder));
    folder.id = "details-folder";
    folder.setAttribute("aria-label", `Open the folder ${item.folder}`);
    fact("Folder", folder, ...(item.external ? [el("span", "fact-note", "External collection")] : []));
    if (item.processing?.length) fact("Processing", item.processing.join(", "));
    if (item.crop) fact("Crop", `${item.crop[2]} × ${item.crop[3]} from ${item.crop[0]}, ${item.crop[1]}`);
    nodes.push(facts);

    if (item.issues?.length) {
      const ul = el("ul", "label-issues");
      ul.setAttribute("aria-label", "Rule issues");
      for (const i of item.issues) ul.append(el("li", i.level, i.message));
      nodes.push(ul);
    }
    this.read.replaceChildren(...nodes);
  }

  // --- edit mode --------------------------------------------------------------------------------
  fillForm(item) {
    const dot = item.file.lastIndexOf(".");
    this.form.fill(item, { ext: dot >= 0 ? item.file.slice(dot) : "", file: item.file, external: item.external });
  }

  setMode(editing) {
    const ending = this.editing && !editing;
    this.editing = editing;
    this.fileButton.disabled = editing; // the viewer would change the selection under the form
    this.pane.classList.toggle("is-editing", editing);
    this.read.hidden = editing;
    this.readActions.hidden = editing;
    this.form.form.hidden = !editing;
    if (ending) this.onLeave();
  }

  edit() {
    if (!this.item || this.editing) return;
    this.fillForm(this.item);
    this.setMode(true);
    this.form.focusFirst();
  }

  /** Cancel the edit (Esc, Cancel): asks first when something changed, then focuses Edit. */
  async cancel() {
    if (this.editing && (await this.confirmDiscard())) this.editButton.focus();
  }

  /**
   * The one guard before anything leaves edit mode (another selection, Esc, a folder or filter change):
   * true (synchronously) or a promise of true when there was no edit, nothing changed, or the person chose Discard (edit mode has ended);
   * false when they chose Keep editing (the safe default). Concurrent callers share one question.
   */
  confirmDiscard() {
    if (!this.editing) return true; // plain true (not a promise): callers go on without waiting a tick
    if (!this.form.dirty) {
      this.leave(false);
      return true;
    }
    if (!this.asking) {
      this.asking = ask({
        title: "Discard your changes?",
        text: `The edits to “${this.item.title}” are not saved.`,
        buttons: [
          { value: "keep", label: "Keep editing", kind: "quiet", focus: true },
          { value: "discard", label: "Discard", kind: "danger" },
        ],
      }).then((answer) => {
        this.asking = null;
        if (answer !== "discard") {
          this.form.focusFirst();
          return false;
        }
        this.leave(false);
        return true;
      });
    }
    return this.asking;
  }

  /** Back to read mode (focus on the Edit button unless the selection is about to change). */
  leave(focus = true) {
    this.setMode(false);
    if (this.item) this.render(this.item);
    if (focus) this.editButton.focus();
  }

  async save() {
    const changes = this.form.changes();
    if (!Object.keys(changes).length || !this.item) return;
    this.form.submit.disabled = true;
    this.saving = true; // the saved entry becomes the form's baseline (see saved())
    let updated;
    try {
      updated = await this.onSave(this.item, changes);
    } finally {
      this.saving = false;
    }
    if (updated && this.editing) this.leave();
    else this.form.validate();
  }
}
