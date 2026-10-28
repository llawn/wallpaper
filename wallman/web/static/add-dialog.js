// Add dialog: pick a source (drop, file picker or URL), fill the metadata (the same form as the details pane), crop
// when the ratio needs it. After the add, offer to stage the image when its folder is already in git.

import { api } from "./api.js";
import { openCropDialog, outputOk } from "./cropper.js";
import { MetaForm, guessFromName } from "./meta-form.js";
import { offer, toast, toastError } from "./status.js";

/** Extension of the stored file: SVG is rendered to PNG, other non-PNG formats end up as JPEG. */
function storedExt(name) {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return ext === ".png" || ext === ".svg" ? ".png" : ".jpg";
}

export class AddDialog {
  /**
   * @param {{licenses: object[], currentFolder: () => string, onAdded: (item: object) => Promise<void>,
   *          onGitAdded: () => Promise<void>}} opts
   */
  constructor({ licenses, currentFolder, onAdded, onGitAdded }) {
    this.dialog = document.getElementById("add-dialog");
    this.sourceStep = document.getElementById("add-source");
    this.metaStep = document.getElementById("add-meta");
    this.busy = document.getElementById("add-busy");
    this.drop = document.getElementById("drop-zone");
    this.fileInput = document.getElementById("file-input");
    this.urlForm = document.getElementById("url-form");
    this.urlInput = document.getElementById("url-input");
    this.preview = document.getElementById("add-preview");
    this.cropState = document.getElementById("add-crop-state");
    this.currentFolder = currentFolder;
    this.onAdded = onAdded;
    this.onGitAdded = onGitAdded;
    this.upload = null;
    this.crop = null;
    this.objectUrl = null;
    this.form = new MetaForm(document.getElementById("add-form"), {
      mode: "add", licenses, submitLabel: "Add wallpaper", onSubmit: (meta) => this.submit(meta),
    });

    document.getElementById("choose-file").addEventListener("click", () => this.fileInput.click());
    this.fileInput.addEventListener("change", () => {
      const file = this.fileInput.files[0];
      if (file) this.fromFile(file);
      this.fileInput.value = "";
    });
    this.urlForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (this.urlInput.reportValidity()) this.fromUrl(this.urlInput.value.trim());
    });
    for (const type of ["dragenter", "dragover"]) {
      this.drop.addEventListener(type, (e) => {
        e.preventDefault();
        this.drop.classList.add("over");
      });
    }
    this.drop.addEventListener("dragleave", () => this.drop.classList.remove("over"));
    this.drop.addEventListener("drop", (e) => {
      e.preventDefault();
      this.drop.classList.remove("over");
      const file = e.dataTransfer?.files[0];
      if (file && this.acceptsFile()) this.fromFile(file);
    });
    document.getElementById("add-crop").addEventListener("click", () => this.openCrop());
    this.dialog.addEventListener("close", () => this.reset());
  }

  setFolders(folders) {
    this.form.setFolders(folders);
  }

  open(file = null) {
    this.reset();
    this.dialog.showModal();
    if (file) this.fromFile(file);
    else document.getElementById("choose-file").focus();
  }

  /** True while the dialog is open on its source step and not already uploading. */
  acceptsFile() {
    return this.dialog.open && !this.sourceStep.hidden && this.busy.hidden;
  }

  reset() {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.upload = null;
    this.crop = null;
    this.sourceStep.hidden = false;
    this.metaStep.hidden = true;
    this.urlInput.value = "";
    this.setBusy("");
  }

  setBusy(text) {
    this.busy.hidden = !text;
    this.busy.textContent = text;
    for (const el of this.sourceStep.querySelectorAll("button, input")) el.disabled = Boolean(text);
  }

  async fromFile(file) {
    this.setBusy(`Uploading ${file.name}…`);
    try {
      const up = await api.uploadFile(file);
      this.objectUrl = URL.createObjectURL(file);
      await this.loaded(up, this.objectUrl);
    } catch (err) {
      toastError(err);
    } finally {
      this.setBusy("");
    }
  }

  async fromUrl(url) {
    this.setBusy("Downloading…");
    try {
      const up = await api.uploadUrl(url);
      await this.loaded(up, url);
    } catch (err) {
      toastError(err);
    } finally {
      this.setBusy("");
    }
  }

  async loaded(up, previewSrc) {
    this.upload = { ...up, previewSrc };
    this.crop = null;
    this.sourceStep.hidden = true;
    this.metaStep.hidden = false;
    this.preview.src = previewSrc;
    this.preview.alt = `Preview of ${up.original_name}`;
    document.getElementById("add-source-name").textContent = up.original_name;
    document.getElementById("add-source-size").textContent =
      `${up.width} × ${up.height}, ratio ${up.ratio.toFixed(2)}${up.download ? ", downloaded" : ""}`;
    this.renderCropState();
    const here = this.currentFolder();
    this.form.fill({ folder: this.form.isFolder(here, { external: false }) ? here : "", license: "", tags: [], ...guessFromName(up.original_name) },
      { hasDownload: Boolean(up.download), ext: storedExt(up.original_name) });
    this.form.focusFirst();
    if (up.needs_crop) await this.openCrop();
  }

  renderCropState() {
    const up = this.upload;
    if (!up) return;
    if (this.crop) {
      const small = !outputOk(this.crop);
      this.cropState.textContent = `Cropped to ${this.crop.w} × ${this.crop.h}${small ? ", below 1800 × 900" : ""}.`;
      this.cropState.classList.toggle("too-small", small);
    } else {
      this.cropState.textContent = up.needs_crop
        ? "The ratio is outside 1.6 to 1.95: a 16:9 crop is needed."
        : "No crop: the image is used as it is.";
      this.cropState.classList.toggle("too-small", up.needs_crop);
    }
  }

  async openCrop() {
    const up = this.upload;
    if (!up) return;
    try {
      const box = await openCropDialog({
        src: up.previewSrc, width: up.width, height: up.height, initial: this.crop, confirm: "Use this crop",
      });
      if (box) this.crop = box;
    } catch (err) {
      toastError(err);
    }
    this.renderCropState();
  }

  async submit(meta) {
    if (this.upload.needs_crop && !this.crop) {
      await this.openCrop();
      if (!this.crop) return toast("Choose a 16:9 crop before adding: the ratio is outside 1.6 to 1.95.", "error");
    }
    this.form.submit.disabled = true;
    try {
      // a new wallpaper is never published on its own; git_suggestion asks the owner when it is relevant
      const item = await api.add({ upload_id: this.upload.upload_id, ...meta, crop: this.crop, stage: false });
      this.dialog.close();
      toast(`Added ${item.file}`);
      await this.onAdded(item);
      if (item.git_suggestion === "add") await this.offerGit(item);
    } catch (err) {
      toastError(err);
    } finally {
      this.form.validate();
    }
  }

  /** Other wallpapers of the folder are published: offer to publish this one too (never done on its own). */
  async offerGit(item) {
    const what = item.shareable ? "its image and entry" : "its entry (the image stays download-only)";
    const answer = await offer(`Add ${item.file} to git? Publishes ${what}, like the other wallpapers in ${item.folder}.`, [
      { value: "add", label: "Add to git", kind: "primary" },
      { value: "later", label: "Not now", kind: "quiet" },
    ], { focus: true });
    if (answer !== "add") return;
    try {
      const { refs } = await api.gitAdd([item]);
      toast(refs.length ? `Added ${item.file} to git` : `${item.file} is already in git`);
      await this.onGitAdded();
    } catch (err) {
      toastError(err);
    }
  }
}
