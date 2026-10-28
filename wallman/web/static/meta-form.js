// Metadata form shared by the details pane (edit mode) and the add dialog: title, artist, year (suggestions), tags
// (chips + autocomplete), license (themed menu + link to its doc), license detail, source, download (edit only),
// folder (tree picker). Inline validation; the submit button stays disabled until the form is valid (and, when
// editing, changed). The helpers at the top are pure and tested under node.

import { FolderPicker } from "./folder-picker.js";
import { SelectMenu } from "./select-menu.js";
import { TagInput } from "./tag-input.js";
import { readYear, YearInput } from "./year-input.js";
import { yearText } from "./years.js";

const URL_RX = /^https?:\/\/[^\s/$.?#][^\s]*$/i;

/** File-name stem the server will derive: artist_title[_year], lowercase kebab (mirror of naming.slug). */
export function slug(text, maxLen = 60) {
  let s = (text ?? "").normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase();
  s = s.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (s.length > maxLen) {
    let cut = s.slice(0, maxLen);
    if (s[maxLen] !== "-" && cut.includes("-")) cut = cut.slice(0, cut.lastIndexOf("-"));
    s = cut.replace(/^-+|-+$/g, "");
  }
  return s;
}

/** The file name the server gives an entry (a range is named by its start year). */
export function fileName(artist, title, year, ext) {
  const y = Array.isArray(year) ? year[0] : year;
  return [artist ? slug(artist) : "", slug(title), y ?? ""].filter(Boolean).join("_") + ext;
}

/** Guess metadata from a file name such as `claude-monet_water-lilies_1906.jpg`. */
export function guessFromName(name) {
  const stem = name.replace(/\.[^.]+$/, "");
  const words = (s) => s.replace(/[-_.]+/g, " ").trim().replace(/\b\p{L}/gu, (c) => c.toUpperCase());
  const parts = stem.split("_").filter(Boolean);
  let year = null;
  if (parts.length > 1 && /^\d{4}$/.test(parts.at(-1))) year = Number(parts.pop());
  if (parts.length >= 2) return { artist: words(parts[0]), title: words(parts.slice(1).join(" ")), year };
  return { artist: "", title: words(parts[0] ?? stem), year };
}

/** "CC BY 4.0: credit the author if you republish" -> ["CC BY 4.0", "Credit the author if you republish"]. */
export function splitLabel(label) {
  const i = String(label).indexOf(": ");
  if (i < 0) return [String(label), ""];
  const terms = label.slice(i + 2);
  return [label.slice(0, i), terms.charAt(0).toUpperCase() + terms.slice(1)];
}

/** True when the license keeps the image out of git and the entry must say where to download it from. */
export const needsDownload = (lic) => Boolean(lic && !lic.shareable && lic.id !== "personal");

/**
 * Inline errors by field for the form values (year as typed text, tags as a list).
 * @param {{mode: "edit"|"add", licenses: Map<string, {id: string, shareable: boolean}>, hasDownload?: boolean,
 *          folders?: Set<string>, external?: boolean}} ctx  `folders`: the folders that can be chosen (checked
 *          when given); `external`: the entry is in an external collection (it keeps its file name)
 */
export function metaErrors(v, { mode, licenses, hasDownload = false, folders = null, external = false }) {
  const errs = {};
  const text = (k) => String(v[k] ?? "").trim();
  if (!text("title")) errs.title = "A title is required: it names the file.";
  else if (!external && !slug(text("title")) && !slug(text("artist"))) {
    errs.title = "The file is named from the artist and title: use at least one letter or digit.";
  }
  if (!text("folder")) errs.folder = "Choose a folder.";
  else if (folders?.size && !folders.has(text("folder"))) errs.folder = "Choose one of the collection folders.";
  if (!text("license")) errs.license = "Choose the license the image is published under.";
  const { error } = readYear(v.year);
  if (error) errs.year = error;
  for (const k of ["source", "download"]) {
    if (text(k) && !URL_RX.test(text(k))) errs[k] = "Use an http:// or https:// address.";
  }
  const lic = licenses.get(text("license"));
  if (needsDownload(lic)) {
    if (mode === "edit" && !text("download") && !errs.download) {
      errs.download = "This license keeps the image out of git, so the entry needs the address it can be downloaded from.";
    }
    if (mode === "add" && !hasDownload) {
      errs.license = "This license keeps the image out of git, so it must be added from a URL, not from a local file.";
    }
  }
  return errs;
}

/** What the license means for git, shown under the license field. */
function gitNote(lic) {
  if (!lic) return "";
  if (lic.shareable) return "Shareable: the image is committed to git.";
  if (lic.id === "personal") return "Personal: the image stays out of git.";
  return "Not shareable: only the entry and its download address are committed.";
}

const TEXT_FIELDS = {
  title: { label: "Title" },
  artist: { label: "Artist", placeholder: "Unknown" },
  license_detail: { label: "Rights holder or terms" },
  source: { label: "Source page", type: "url" },
  download: { label: "Download address", type: "url", editOnly: true },
};

/**
 * The form. mode "edit": PATCH the changes; mode "add": POST a new entry (no download field: it comes from the
 * upload). `refuseFolder(row, context)` returns why a folder cannot be chosen ("" when it can).
 */
export class MetaForm {
  constructor(form, { mode, licenses, submitLabel, onSubmit, extraButtons = [] }) {
    this.form = form;
    this.mode = mode;
    this.licenses = new Map(licenses.map((l) => [l.id, l]));
    this.onSubmit = onSubmit;
    this.initial = {};
    this.context = {}; // add: {hasDownload, ext}; edit: {ext, file, external}
    this.fields = {}; // name -> {wrap, msg, hint, control (focusable), touched}
    this.errors = {};
    this.inputs = {};
    form.replaceChildren();
    form.classList.add("meta-form");

    form.append(this.textField("title"));
    const pair = document.createElement("div");
    pair.className = "field-pair";
    pair.append(this.textField("artist"), this.yearField());
    form.append(pair, this.tagField(), this.licenseField(), this.textField("license_detail"), this.textField("source"));
    if (mode === "edit") form.append(this.textField("download"));
    form.append(this.folderField());

    this.hint = document.createElement("p");
    this.hint.className = "form-hint";
    this.hint.setAttribute("aria-live", "polite");
    this.actions = document.createElement("div");
    this.actions.className = "form-actions";
    this.submit = document.createElement("button");
    this.submit.type = "submit";
    this.submit.className = "primary";
    this.submit.textContent = submitLabel;
    this.actions.append(...extraButtons, this.submit);
    form.append(this.hint, this.actions);

    form.addEventListener("input", (e) => {
      const name = e.target.name;
      if (this.fields[name]) this.fields[name].touched = true;
      this.validate();
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (this.validate(true)) this.onSubmit(this.payload());
    });
  }

  // --- building ---------------------------------------------------------------------------------
  wrap(name, label, controlId) {
    const wrap = document.createElement("div");
    wrap.className = `field field-${name.replace("_", "-")}`;
    const head = document.createElement("div");
    head.className = "field-head";
    const l = document.createElement("label");
    l.htmlFor = controlId;
    l.textContent = label;
    head.append(l);
    const msg = document.createElement("p");
    msg.className = "field-msg";
    msg.id = `${controlId}-msg`;
    wrap.append(head);
    return { wrap, head, msg };
  }

  register(name, parts, control, extra = {}) {
    control.setAttribute("aria-describedby", parts.msg.id);
    this.fields[name] = { ...parts, control, touched: false, hint: "", ...extra };
    return parts.wrap;
  }

  id(name) {
    return `${this.form.id}-${name.replace("_", "-")}`;
  }

  textField(name) {
    const f = TEXT_FIELDS[name];
    const id = this.id(name);
    const parts = this.wrap(name, f.label, id);
    const input = document.createElement("input");
    input.type = f.type ?? "text";
    input.id = id;
    input.name = name;
    input.autocomplete = "off";
    input.spellcheck = false;
    if (f.placeholder) input.placeholder = f.placeholder;
    if (name === "title") input.required = true;
    parts.wrap.append(input, parts.msg);
    this.inputs[name] = input;
    return this.register(name, parts, input);
  }

  yearField() {
    const id = this.id("year");
    const parts = this.wrap("year", "Year", id);
    const pop = document.createElement("span");
    pop.className = "field-pop";
    const input = document.createElement("input");
    input.type = "text";
    input.id = id;
    input.name = "year";
    input.autocomplete = "off";
    input.spellcheck = false;
    pop.append(input);
    parts.wrap.append(pop, parts.msg);
    this.inputs.year = input;
    this.yearInput = new YearInput(input);
    return this.register("year", parts, input);
  }

  tagField() {
    const id = this.id("tags");
    const parts = this.wrap("tags", "Tags", id);
    const host = document.createElement("div");
    parts.wrap.append(host, parts.msg);
    this.tags = new TagInput(host, {
      id,
      onChange: () => {
        this.fields.tags.touched = true;
        this.validate();
      },
    });
    return this.register("tags", parts, this.tags.input);
  }

  licenseField() {
    const id = this.id("license");
    const parts = this.wrap("license", "License", id);
    this.docLink = document.createElement("a");
    this.docLink.className = "doc-link";
    this.docLink.target = "_blank";
    this.docLink.rel = "noopener noreferrer";
    this.docLink.textContent = "?";
    this.docLink.hidden = true;
    parts.head.append(this.docLink);
    const host = document.createElement("div");
    host.className = "field-menu";
    parts.wrap.append(host, parts.msg);
    const options = [...this.licenses.values()].sort((a, b) => a.label.localeCompare(b.label))
      .map((l) => ({ value: l.id, label: splitLabel(l.label)[0], hint: l.label === l.id ? "" : l.id }));
    this.license = new SelectMenu(host, {
      name: "License", placeholder: "Choose a license", options,
      onChange: () => {
        this.fields.license.touched = true;
        this.validate();
      },
    });
    this.license.button.id = id;
    return this.register("license", parts, this.license.button);
  }

  folderField() {
    const id = this.id("folder");
    const parts = this.wrap("folder", "Folder", id);
    const host = document.createElement("div");
    parts.wrap.append(host, parts.msg);
    this.folder = new FolderPicker(host, {
      id,
      refuse: (row) => this.refuseFolder(row),
      onChange: () => {
        this.fields.folder.touched = true;
        this.validate();
      },
    });
    return this.register("folder", parts, this.folder.button);
  }

  /** Edit mode never moves a wallpaper into or out of an external collection (a separate repository). */
  refuseFolder(row) {
    if (this.mode !== "edit") return "";
    if (row.external && row.path !== this.initial.folder) {
      return "External collections are separate repositories: wallpapers cannot be moved into them.";
    }
    return "";
  }

  /** True when `path` is a collection folder the picker can choose. */
  isFolder(path, { external = true } = {}) {
    return this.folder.rows.some((r) => r.selectable && r.path === path && (external || !r.external));
  }

  setFolders(folders) {
    this.folder.setFolders(folders);
    this.folder.setValue(this.folder.value);
  }

  // --- values -----------------------------------------------------------------------------------
  /** Fill the form from API values (year as the API stores it); resets touched state and the baseline. */
  fill(values, context = {}) {
    this.context = context;
    for (const name of Object.keys(TEXT_FIELDS)) {
      if (this.inputs[name]) this.inputs[name].value = values[name] ?? "";
    }
    this.inputs.year.value = yearText(values.year);
    this.tags.input.value = "";
    this.tags.set(values.tags ?? [], true);
    this.license.setValue(values.license ?? "");
    this.folder.setValue(values.folder ?? "");
    this.folder.lock(this.mode === "edit" && context.external
      ? "This wallpaper is in an external collection: it stays in that repository." : "");
    for (const f of Object.values(this.fields)) f.touched = false;
    this.initial = this.payload();
    this.validate();
  }

  /** Raw values as typed (year as text): what metaErrors checks. */
  values() {
    const v = {};
    for (const name of Object.keys(TEXT_FIELDS)) if (this.inputs[name]) v[name] = this.inputs[name].value;
    return { ...v, year: this.inputs.year.value, tags: this.tags.value, license: this.license.value, folder: this.folder.value };
  }

  /** Values in API shape: empty optional text is null, year is null, 1889 or [start, end]. */
  payload() {
    const v = this.values();
    const out = {
      folder: v.folder, title: v.title.trim(), artist: v.artist.trim() || null,
      year: readYear(v.year).value ?? null, tags: v.tags, license: v.license,
      license_detail: v.license_detail.trim() || null, source: v.source.trim() || null,
    };
    if (this.mode === "edit") out.download = v.download.trim() || null;
    return out;
  }

  changes() {
    const now = this.payload();
    return Object.fromEntries(Object.entries(now).filter(([k, val]) => JSON.stringify(this.initial[k]) !== JSON.stringify(val)));
  }

  get dirty() {
    return Object.keys(this.changes()).length > 0;
  }

  // --- validation -------------------------------------------------------------------------------
  validate(showAll = false) {
    const v = this.values();
    const folders = new Set(this.folder.rows.filter((r) => r.selectable).map((r) => r.path));
    const row = this.folder.rows.find((r) => r.path === v.folder);
    const external = this.mode === "edit" ? Boolean(this.context.external) : Boolean(row?.external);
    const errs = metaErrors(v, { mode: this.mode, licenses: this.licenses, hasDownload: this.context.hasDownload,
                                 folders, external });
    this.errors = errs;
    const lic = this.licenses.get(v.license);
    const yr = readYear(v.year);
    const hints = {
      license: gitNote(lic),
      year: Array.isArray(yr.value) ? `Saved as ${yearText(yr.value)}.` : "",
      folder: this.folder.locked || "",
      tags: "Enter or a comma adds a tag.",
    };
    for (const [name, f] of Object.entries(this.fields)) {
      const filled = name === "tags" ? v.tags.length > 0 : String(v[name] ?? "") !== "";
      // the download requirement follows from the license, so it shows as soon as the license asks for it
      const visible = showAll || f.touched || filled || (name === "download" && errs.download);
      const err = visible ? errs[name] : undefined;
      f.control.setAttribute("aria-invalid", String(Boolean(err)));
      f.wrap.classList.toggle("invalid", Boolean(err));
      f.msg.textContent = err ?? hints[name] ?? "";
    }
    if (this.inputs.download) this.inputs.download.required = needsDownload(lic);
    this.docLink.hidden = !lic;
    if (lic) {
      this.docLink.href = lic.doc ?? `/docs/licenses/${encodeURIComponent(lic.id)}`;
      this.docLink.setAttribute("aria-label", `About ${lic.label} (opens in a new tab)`);
      this.docLink.title = `About ${lic.label}`;
    }
    this.hint.textContent = this.describe();
    const valid = !Object.keys(errs).length;
    this.submit.disabled = !valid || (this.mode === "edit" && !this.dirty);
    if (showAll && !valid) this.fields[Object.keys(this.fields).find((n) => errs[n])]?.control.focus();
    return valid;
  }

  /** The file name the entry will get (add) or a rename the save causes (edit). */
  describe() {
    const { title, artist, year } = this.payload();
    const ext = this.context.ext;
    if (!ext || !title || this.context.external) return "";
    const name = fileName(artist, title, year, ext);
    if (this.mode === "add") return `Saved as ${name}.`;
    return name !== this.context.file ? `Saving renames the file to ${name}.` : "";
  }

  focusFirst() {
    this.inputs.title.focus();
  }
}
