// HTTP calls to the wallman API. Auth rides on the HttpOnly cookie set by `/?token=…`.

export class ApiError extends Error {
  constructor(status, name, message) {
    super(message);
    this.status = status;
    this.name = name;
  }
}

/** `folder/…/file` as a URL path, each segment encoded. */
export function refPath(ref) {
  return [...ref.folder.split("/"), ref.file].map(encodeURIComponent).join("/");
}

export const refKey = (ref) => `${ref.folder}/${ref.file}`;

async function call(method, path, { json, body, headers } = {}) {
  const init = { method, headers: { ...headers }, credentials: "same-origin" };
  if (json !== undefined) {
    init.body = JSON.stringify(json);
    init.headers["Content-Type"] = "application/json";
  } else if (body !== undefined) {
    init.body = body;
  }
  let res;
  try {
    res = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, "NetworkError", "The wallman server is not reachable. Is `just serve` still running?");
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401) {
      throw new ApiError(401, "Unauthorized", "Session expired: open the URL printed by `just serve` again.");
    }
    throw new ApiError(res.status, data?.error ?? "HttpError", data?.message ?? `${method} ${path} failed (${res.status})`);
  }
  return data;
}

const refsBody = (refs) => ({ refs: refs.map(({ folder, file }) => ({ folder, file })) });

export const api = {
  collections: () => call("GET", "/collections"),
  /** One page of the gallery: `query` from listQuery(). */
  list: (query, offset, limit) => call("GET", `/wallpapers?${query}${query ? "&" : ""}offset=${offset}&limit=${limit}`),
  wallpaper: (ref) => call("GET", `/wallpapers/${refPath(ref)}`),
  update: (ref, changes) => call("PATCH", `/wallpapers/${refPath(ref)}`, { json: changes }),
  deletePlan: (refs) => call("POST", "/wallpapers/delete-plan", { json: refsBody(refs) }),
  remove: (refs, git = "remove") => call("DELETE", "/wallpapers", { json: { ...refsBody(refs), git } }),
  gitAdd: (refs) => call("POST", "/git/add", { json: refsBody(refs) }),
  gitRemove: (refs) => call("POST", "/git/remove", { json: refsBody(refs) }),
  crop: (ref, box) => call("POST", `/wallpapers/${refPath(ref)}/crop`, { json: box }),
  uploadFile: (file) => call("POST", "/uploads", {
    body: file,
    headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
  }),
  uploadUrl: (url) => call("POST", "/uploads/url", { json: { url } }),
  add: (meta) => call("POST", "/wallpapers", { json: meta }),
  status: () => call("GET", "/status"),
  /** Cheap status for the 5 s poll: only theme_mtime, theme_warnings and git_error. */
  themeStatus: () => call("GET", "/status?only=theme"),
  licenses: () => call("GET", "/licenses"),
  media: () => call("GET", "/media"),
  folders: () => call("GET", "/folders"),
  tags: (prefix = "", limit = 20) => call("GET", `/tags?prefix=${encodeURIComponent(prefix)}&limit=${limit}`),
  yearSuggestions: (q) => call("GET", `/years/suggest?q=${encodeURIComponent(q)}`),
};

/** Query string for /api/wallpapers from the toolbar filters; empty filters are left out, sort always present. */
export function listQuery({ folder = "", q = "", tags = [], license = "", ratio = "", year = "", issues = false, sort = "name" }) {
  const p = new URLSearchParams();
  if (folder) p.set("folder", folder);
  if (q.trim()) p.set("q", q.trim());
  for (const t of tags) p.append("tag", t);
  if (license) p.set("license", license);
  if (ratio) p.set("ratio", ratio);
  if (year.trim()) p.set("year", year.trim());
  if (issues) p.set("issues", "1");
  p.set("sort", sort);
  return p.toString();
}

/** Gallery thumbnail URL, versioned by the server's thumb key (immutable caching; a crop changes the key). */
export const thumbSrc = (item) => `/api/thumbs/${refPath(item)}?v=${encodeURIComponent(item.thumb)}`;

/** Request sequence: only the response to the latest `next()` token is current (drops out-of-order replies). */
export function createSequence() {
  let last = 0;
  return { next: () => ++last, isCurrent: (token) => token === last };
}

const versions = new Map();

/** Mark an image as changed so thumbnails and previews skip the in-page cache. */
export function bump(ref) {
  versions.set(refKey(ref), Date.now());
}

function versioned(prefix, ref) {
  const v = versions.get(refKey(ref));
  return `/api/${prefix}/${refPath(ref)}${v ? `?v=${v}` : ""}`;
}

export const thumbUrl = (ref) => versioned("thumbs", ref);
export const imageUrl = (ref) => versioned("images", ref);
