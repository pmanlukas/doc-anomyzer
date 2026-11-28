/* Dokumenten-Anonymisierer – Oberfläche (ohne externe Bibliotheken).
 *
 * Ansichten (Hash-Routing):
 *   #/                 Startseite: Dokumente ablegen, letzte Akten
 *   #/akte/<id>        je nach Status: Fortschritt, Prüfen oder Ergebnis
 *
 * Leitlinie: ein Weg (ablegen → kurz prüfen → speichern), wenig Text. Was
 * man selten braucht, liegt eine Ebene tiefer (aufklappbar) – nichts fehlt.
 *
 * Sicherheit: Texte werden ausschließlich als Textknoten eingefügt (nie als
 * HTML), Stile nur über das CSSOM gesetzt – die Seite kommt mit einer
 * strengen Content-Security-Policy ohne Inline-Skripte und -Stile aus.
 */
"use strict";

// ------------------------------------------------------------------ helpers

/** Create a DOM element. Children may be strings (inserted as text = safe). */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Icons: stroke paths on a 24x24 grid ("c:" circle cx,cy,r · "r:" rect x,y,w,h,rx). */
const ICONS = {
  upload: ["M12 16V4", "M7 9l5-5 5 5", "M4 20h16"],
  download: ["M12 4v11", "M7 11l5 5 5-5", "M4 20h16"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  checkCircle: ["c:12,12,9", "M8 12.5l3 3 5-6"],
  alert: ["M12 4l9.5 16h-19z", "M12 10v4.5", "M12 17.4v.1"],
  info: ["c:12,12,9", "M12 11v5.5", "M12 7.6v.1"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  lock: ["r:5,11,14,9,2", "M8 11V8a4 4 0 0 1 8 0v3"],
  shield: ["M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z", "M9 12l2 2 4-4"],
  settings: ["M4 7h9", "M19 7h1", "c:16,7,2.5", "M4 17h1", "M11 17h9", "c:8,17,2.5"],
  help: ["c:12,12,9", "M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.8.5-1.2 1-1.2 1.9", "M12 17.2v.1"],
  power: ["M12 4v8", "M7.3 7.3a7 7 0 1 0 9.4 0"],
  eye: ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z", "c:12,12,3"],
  search: ["c:11,11,6.5", "M20 20l-4.3-4.3"],
  trash: ["M5 7h14", "M10 7V5h4v2", "M7 7l1 13h8l1-13"],
  arrowRight: ["M5 12h14", "M13 6l6 6-6 6"],
  arrowLeft: ["M19 12H5", "M11 6l-6 6 6 6"],
  chevronLeft: ["M15 6l-6 6 6 6"],
  chevronRight: ["M9 6l6 6-6 6"],
  plus: ["M12 5v14", "M5 12h14"],
  minus: ["M5 12h14"],
  edit: ["M4 20l4-1L19 8l-3-3L5 16z", "M14 7l3 3"],
  external: ["M14 5h5v5", "M19 5l-8 8", "M11 7H6v11h11v-5"],
  archive: ["r:4,8,16,12,1.5", "M3 4h18v4H3z", "M10 12h4"],
  refresh: ["M20 11a8 8 0 0 0-14.5-4.5L4 8", "M4 4v4h4", "M4 13a8 8 0 0 0 14.5 4.5L20 16", "M20 20v-4h-4"],
  home: ["M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"],
  text: ["M5 6h14", "M5 10h14", "M5 14h9", "M5 18h11"],
};
const SVG_NS = "http://www.w3.org/2000/svg";
function icon(name) {
  const el = document.createElementNS(SVG_NS, "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("class", "ico");
  el.setAttribute("aria-hidden", "true");
  for (const part of ICONS[name] || []) {
    let child;
    if (part.startsWith("c:")) {
      const [cx, cy, r] = part.slice(2).split(",");
      child = document.createElementNS(SVG_NS, "circle");
      child.setAttribute("cx", cx); child.setAttribute("cy", cy); child.setAttribute("r", r);
    } else if (part.startsWith("r:")) {
      const [x, y, w, hh, rx] = part.slice(2).split(",");
      child = document.createElementNS(SVG_NS, "rect");
      child.setAttribute("x", x); child.setAttribute("y", y); child.setAttribute("width", w);
      child.setAttribute("height", hh); child.setAttribute("rx", rx || 0);
    } else {
      child = document.createElementNS(SVG_NS, "path");
      child.setAttribute("d", part);
    }
    el.append(child);
  }
  return el;
}

/** JSON API call; mutating requests carry the anti-CSRF header. */
async function api(method, url, body, extra) {
  const opts = Object.assign({ method, headers: { "X-Anonymizer": "1" }, credentials: "same-origin" }, extra || {});
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let resp;
  try { resp = await fetch(url, opts); }
  catch (e) { const err = new Error("Keine Verbindung zum Programm."); err.offline = true; throw err; }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no JSON body */ }
  if (resp.status === 401 && data && data.locked) showLocked();
  if (!resp.ok) {
    const fallback = resp.status === 413 ? "Die Datei ist zu groß (höchstens 2 GB je Übertragung)." : `Fehler ${resp.status}`;
    const err = new Error((data && data.error) || fallback);
    err.status = resp.status; err.data = data;
    throw err;
  }
  return data;
}

function toast(text, isError) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.toggle("error", !!isError);
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 2800);
}

function showError(err) {
  console.error(err);
  toast(err && err.message ? err.message : String(err), true);
}

/** Replace the children of ``el`` (arrays are flattened, null/false skipped). */
function fill(el, ...children) {
  const nodes = children.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false)
    .map((n) => (n instanceof Node ? n : document.createTextNode(String(n))));
  el.replaceChildren(...nodes);
}

const app = () => document.getElementById("app");
function render(...nodes) {
  fill(app(), nodes);
  window.scrollTo(0, 0);
}
function setActionbar(node) {
  fill(document.getElementById("actionbar-root"), node);
}
function setTitle(text) {
  document.title = text ? `${text} – Dokumenten-Anonymisierer` : "Dokumenten-Anonymisierer";
}

function notice(kind, ...children) {
  const icons = { ok: "checkCircle", bad: "alert", warn: "alert", info: "info" };
  return h("div", { class: `notice ${kind}` }, icon(icons[kind] || "info"), h("div", {}, children));
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }); }
  catch (e) { return iso; }
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const baseName = (path) => String(path || "").split(/[\\/]/).pop();
/** A folder path as <code>; long paths are shortened in the middle (tooltip: full path). */
function pathLabel(path) {
  const el = h("code", {});
  el.set = (value) => {
    const text = String(value || "–");
    el.title = text;
    el.textContent = text.length > 56 ? `${text.slice(0, 16)} … ${text.slice(-36)}` : text;
  };
  el.set(path);
  return el;
}

// ------------------------------------------------------------------ dialogs

const modalStack = [];
/** Open a modal dialog; returns { close }. Esc and a click beside it close it. */
function openModal(content, options) {
  const opt = Object.assign({ size: "", dismissable: true, onClose: null }, options || {});
  const entry = { closed: false };
  entry.close = () => {
    if (entry.closed) return;
    entry.closed = true;
    back.remove();
    modalStack.splice(modalStack.indexOf(entry), 1);
    // A dialog opened from another dialog covered it – show that one again.
    if (modalStack.length) modalStack[modalStack.length - 1].back.classList.remove("covered");
    if (opt.onClose) opt.onClose();
  };
  entry.dismiss = () => { if (opt.dismissable) entry.close(); };
  const box = h("div", { class: `modal ${opt.size}`, role: "dialog", "aria-modal": "true", tabindex: "-1" }, content);
  const back = h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === back) entry.dismiss(); } }, box);
  entry.back = back;
  modalStack.forEach((other) => other.back.classList.add("covered"));
  document.getElementById("modal-root").append(back);
  modalStack.push(entry);
  (box.querySelector("[autofocus]") || box).focus();
  return entry;
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && modalStack.length) modalStack[modalStack.length - 1].dismiss();
});

function modalHead(title, modal) {
  return h("div", { class: "modal-head" }, h("h2", {}, title),
    h("button", { class: "btn ghost icon-only", title: "Schließen", onclick: () => modal().close() }, icon("x")));
}

/** Styled replacement for confirm(): resolves to true/false. */
function confirmDialog({ title, text, ok = "OK", cancel = "Abbrechen", danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    const modal = openModal([
      h("h2", {}, title),
      (Array.isArray(text) ? text : [text]).map((t) => h("p", {}, t)),
      h("div", { class: "modal-foot" },
        h("button", { class: "btn", onclick: () => modal.close() }, cancel),
        h("button", { class: `btn ${danger ? "danger solid" : "primary"}`, autofocus: true, onclick: () => { result = true; modal.close(); } }, ok)),
    ], { size: "narrow", onClose: () => resolve(result) });
  });
}

/** Styled replacement for prompt(): resolves to the text or null. */
function promptDialog({ title, label, value = "", ok = "OK" }) {
  return new Promise((resolve) => {
    let result = null;
    const input = h("input", { type: "text", value, autofocus: true, "aria-label": label });
    const submit = () => { result = input.value.trim() || null; modal.close(); };
    const modal = openModal([
      h("h2", {}, title),
      h("form", { onsubmit: (ev) => { ev.preventDefault(); submit(); } },
        h("p", { class: "muted" }, label), input,
        h("div", { class: "modal-foot" },
          h("button", { class: "btn", type: "button", onclick: () => modal.close() }, "Abbrechen"),
          h("button", { class: "btn primary", type: "submit" }, ok))),
    ], { size: "narrow", onClose: () => resolve(result) });
    input.select();
  });
}

// ------------------------------------------------------------------- router

let pollTimer = null;
let cleanups = [];
function resetView() {
  clearTimeout(pollTimer); pollTimer = null;
  cleanups.forEach((fn) => { try { fn(); } catch (e) { /* ignore */ } });
  cleanups = [];
  document.onkeydown = null;
  setActionbar(null);
  app().classList.remove("wide");
}

async function route() {
  resetView();
  const hash = location.hash || "#/";
  try {
    if (hash === "#/" || hash === "#" || hash === "#/neu") return await viewHome();
    const m = hash.match(/^#\/akte\/([0-9a-f]{32})$/);
    if (m) return await viewCase(m[1]);
    location.hash = "#/";
  } catch (err) {
    if (err.status === 404) { toast("Diese Akte gibt es nicht mehr.", true); location.hash = "#/"; return; }
    if (!err.offline && err.status !== 401) showError(err);
  }
}
window.addEventListener("hashchange", route);

// --------------------------------------------------------------------- home

/** Collect files from a drop (files and whole folders, recursively). */
async function filesFromDrop(dataTransfer) {
  const result = [];
  const entries = [...dataTransfer.items || []].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return [...dataTransfer.files];
  async function walk(entry) {
    if (entry.isFile) {
      await new Promise((resolve) => entry.file((f) => { result.push(f); resolve(); }, resolve));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
        for (const child of batch) await walk(child);
      } while (batch.length);
    }
  }
  for (const entry of entries) await walk(entry);
  return result;
}

/** Create a case from the files, upload in packages, start the analysis. */
async function startCase(fileList) {
  const all = [...fileList];
  const files = all.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  const skipped = all.length - files.length;
  if (!files.length) {
    toast(all.length ? "Das sind keine PDF-Dateien. Word-Dokumente oder Bilder bitte zuerst als PDF speichern." : "Keine Dateien gefunden.", true);
    return;
  }
  resetView();
  setTitle("Dokumente werden übernommen");
  const bar = h("div", { class: "progress" }, h("div"));
  const label = h("p", { class: "muted" }, "");
  render(h("div", { class: "panel progress-card" },
    h("div", { class: "spinner" }),
    h("h1", {}, "Dokumente werden übernommen …"), bar, label,
    skipped ? h("p", { class: "muted small" }, `${plural(skipped, "Datei ist", "Dateien sind")} kein PDF und ${skipped === 1 ? "wird" : "werden"} übersprungen.`) : null));
  let created = null;
  try {
    const name = files.length === 1 ? files[0].name.replace(/\.pdf$/i, "")
      : `${files.length} Dokumente vom ${new Date().toLocaleDateString("de-DE")}`;
    created = await api("POST", "/api/cases", { title: name });
    // Packages of at most 20 files / 400 MB: one request must stay below 2 GB.
    const packages = [[]];
    let size = 0;
    for (const f of files) {
      const current = packages[packages.length - 1];
      if (current.length >= 20 || (current.length && size + f.size > 400 * 1024 * 1024)) { packages.push([]); size = 0; }
      packages[packages.length - 1].push(f); size += f.size;
    }
    let done = 0;
    const rejected = [];
    for (const group of packages) {
      const form = new FormData();
      group.forEach((f) => form.append("files", f, f.webkitRelativePath || f.name));
      const res = await api("POST", `/api/cases/${created.id}/documents`, form);
      rejected.push(...(res.rejected || []));
      done += group.length;
      bar.firstChild.style.width = `${Math.round((100 * done) / files.length)}%`;
      label.textContent = `${done} von ${files.length} Dateien übernommen`;
    }
    if (rejected.length === files.length) throw new Error("Die Dateien sind keine gültigen PDF-Dokumente.");
    if (rejected.length) toast(`${plural(rejected.length, "Datei war", "Dateien waren")} kein gültiges PDF und ${rejected.length === 1 ? "wurde" : "wurden"} übersprungen.`, true);
    await api("POST", `/api/cases/${created.id}/analyse`);
    location.hash = `#/akte/${created.id}`;
  } catch (err) {
    showError(err);
    if (created) api("DELETE", `/api/cases/${created.id}`).catch(() => {});
    if (location.hash === "#/" || location.hash === "") route(); else location.hash = "#/";
  }
}

function dropZone() {
  const files = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, class: "hidden" });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true, class: "hidden" });
  files.addEventListener("change", () => startCase(files.files));
  folder.addEventListener("change", () => startCase(folder.files));
  return h("div", { class: "dropzone", id: "dropzone" },
    h("div", { class: "drop-icon" }, icon("upload")),
    h("strong", {}, "PDF-Dateien hierher ziehen"),
    h("div", { class: "row" },
      h("button", { class: "btn primary big", onclick: () => files.click() }, icon("file"), "Dateien auswählen"),
      h("button", { class: "btn big", onclick: () => folder.click() }, icon("folder"), "Ordner auswählen")),
    files, folder);
}

/** Dropping files anywhere on the start page works. */
function enablePageDrop() {
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer && e.dataTransfer.types) || []].includes("Files");
  const enter = (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth += 1; document.body.classList.add("dragging"); };
  const over = (e) => { if (hasFiles(e)) e.preventDefault(); };
  const leave = (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) document.body.classList.remove("dragging"); };
  const drop = async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); depth = 0; document.body.classList.remove("dragging");
    if (modalStack.length) return;
    startCase(await filesFromDrop(e.dataTransfer));
  };
  window.addEventListener("dragenter", enter); window.addEventListener("dragover", over);
  window.addEventListener("dragleave", leave); window.addEventListener("drop", drop);
  cleanups.push(() => {
    window.removeEventListener("dragenter", enter); window.removeEventListener("dragover", over);
    window.removeEventListener("dragleave", leave); window.removeEventListener("drop", drop);
    document.body.classList.remove("dragging");
  });
}

function caseChip(c) {
  if (c.status === "fertig") {
    if (c.passed) return h("span", { class: "chip ok" }, icon("check"), c.unreadable ? "Fertig (teilweise)" : "Fertig");
    return h("span", { class: "chip bad" }, icon("alert"), "Bitte ansehen");
  }
  const map = {
    neu: ["", c.documents ? "Noch nicht gelesen" : "Leer"], analyse: ["work", "Wird gelesen …"], pruefung: ["warn", "Bitte prüfen"],
    schwaerzen: ["work", "Wird anonymisiert …"], fehler: ["bad", "Fehler"],
  };
  const [cls, label] = map[c.status] || ["", c.status];
  return h("span", { class: `chip ${cls}` }, label);
}

async function viewHome() {
  setTitle("");
  const [cases, status] = await Promise.all([api("GET", "/api/cases"), api("GET", "/api/status")]);
  const problems = [];
  if (!status.spacy) problems.push("Das deutsche Sprachmodell fehlt.");
  if (!status.tesseract) problems.push("Die Texterkennung fehlt – eingescannte PDFs können nicht gelesen werden.");
  else if (!status.tesseract_languages.includes("deu")) problems.push("Für die Texterkennung fehlt das deutsche Sprachpaket.");

  const rows = cases.map((c) => h("div", { class: "case-row" },
    h("div", {},
      h("a", { class: "title", href: `#/akte/${c.id}` }, c.title),
      h("div", { class: "meta" }, `${plural(c.documents, "PDF", "PDFs")}${c.pages ? ` · ${plural(c.pages, "Seite", "Seiten")}` : ""} · ${fmtDate(c.created)}`)),
    caseChip(c),
    h("div", { class: "row" },
      h("a", { class: "btn small", href: `#/akte/${c.id}` }, "Öffnen"),
      h("button", {
        class: "btn small ghost icon-only danger", title: "Akte aus dieser Liste löschen",
        onclick: async () => {
          if (!await confirmDialog({
            title: "Akte löschen?",
            text: [`„${c.title}“ wird aus der Liste entfernt, zusammen mit der gespeicherten Kopie der Originaldokumente.`,
              "Die bereits anonymisierten Dateien im Ergebnisordner bleiben erhalten."],
            ok: "Löschen", danger: true,
          })) return;
          try { await api("DELETE", `/api/cases/${c.id}`); toast("Akte gelöscht"); route(); }
          catch (err) { showError(err); }
        },
      }, icon("trash")))));

  // Where the results go: the folder's name is enough, the full path is the tooltip.
  const where = h("strong", { id: "where" });
  const showWhere = (path) => { where.textContent = baseName(path) || path || "–"; where.title = path || ""; };
  showWhere(status.output_dir);

  render(
    problems.length ? notice("bad", h("strong", {}, "Die Installation ist unvollständig. "), problems.join(" "),
      " Bitte das Setup des Programms noch einmal ausführen.") : null,
    h("div", { class: "panel hero" },
      h("h1", {}, "PDF-Dokumente anonymisieren"),
      h("p", { class: "lead" }, "Namen werden zu Initialen, andere persönliche Daten zu Platzhaltern."),
      dropZone(),
      h("div", { class: "savebar" }, icon("folder"), "Speicherort:", where,
        h("button", {
          class: "btn link small", id: "btn-where", onclick: async () => {
            const chosen = await pickFolder(status.output_dir);
            if (chosen) { status.output_dir = chosen; showWhere(chosen); }
          },
        }, "ändern"),
        status.cloud ? h("span", { class: "chip warn", title: "Dieser Ordner wird vermutlich mit einem Cloud-Dienst synchronisiert." }, icon("alert"), "Cloud-Ordner") : null)),
    // The list of cases only appears once there is one.
    !cases.length ? null : h("div", { class: "panel" },
      h("div", { class: "row between" },
        h("h2", { style: "margin:0" }, "Letzte Akten"),
        cases.length > 1 ? h("button", {
          class: "btn small ghost", onclick: async () => {
            if (!await confirmDialog({
              title: "Alle Akten löschen?",
              text: ["Alle Akten in dieser Liste werden entfernt, zusammen mit den gespeicherten Kopien der Originaldokumente.",
                "Die bereits anonymisierten Dateien im Ergebnisordner bleiben erhalten."],
              ok: "Alle löschen", danger: true,
            })) return;
            try { await api("DELETE", "/api/cases"); toast("Alle Akten gelöscht"); route(); } catch (err) { showError(err); }
          },
        }, icon("trash"), "Alle löschen") : null),
      h("div", { class: "case-list", style: "margin-top:.6rem" }, rows)),
    h("p", { class: "muted small center" }, `Version ${status.version}`),
  );
  enablePageDrop();
}

// ------------------------------------------------------------------ case view

async function viewCase(id) {
  const c = await api("GET", `/api/cases/${id}`);
  if (c.status === "analyse" || c.status === "schwaerzen") return viewProgress(c);
  if (c.status === "pruefung") return viewReview(c);
  if (c.status === "fertig") return viewResult(c);
  if (c.status === "fehler") return viewError(c);
  if ((c.documents || []).length) return viewUnread(c);
  return viewHome();
}

/** A case whose documents have not been read (e.g. reading was cancelled). */
function viewUnread(c) {
  setTitle(c.title);
  render(h("div", { class: "panel" },
    h("h1", {}, "Diese Akte wurde noch nicht gelesen"),
    h("p", { class: "muted" }, `${plural(c.documents.length, "PDF-Datei liegt", "PDF-Dateien liegen")} bereit. ` +
      "Das Lesen wurde abgebrochen oder noch nicht gestartet."),
    h("div", { class: "row" },
      h("button", {
        class: "btn primary", onclick: async () => {
          try { await api("POST", `/api/cases/${c.id}/analyse`); route(); } catch (err) { showError(err); }
        },
      }, icon("refresh"), "Jetzt lesen"),
      h("button", {
        class: "btn", onclick: async () => {
          if (!await confirmDialog({ title: "Akte löschen?", text: "Die gespeicherten Kopien der Dokumente werden gelöscht. Ihre Originaldateien bleiben unberührt.", ok: "Löschen", danger: true })) return;
          try { await api("DELETE", `/api/cases/${c.id}`); location.hash = "#/"; } catch (err) { showError(err); }
        },
      }, icon("trash"), "Akte löschen"),
      h("a", { class: "btn", href: "#/" }, "Zur Startseite"))));
}

function viewProgress(c) {
  const isAnalyse = c.status === "analyse";
  setTitle(isAnalyse ? "Dokumente werden gelesen" : "Dokumente werden anonymisiert");
  const bar = h("div", { class: "progress" }, h("div"));
  const stepLabel = h("p", { class: "progress-step" }, "Wird vorbereitet …");
  let cancelling = false;
  const cancelBtn = h("button", {
    class: "btn small", id: "btn-cancel", onclick: async () => {
      if (!await confirmDialog({
        title: "Verarbeitung abbrechen?",
        text: isAnalyse ? "Das Lesen wird beendet. Die Akte bleibt in der Liste und kann später neu gelesen oder gelöscht werden."
          : "Das Anonymisieren wird beendet, bereits erzeugte Ergebnisse dieses Durchlaufs werden entfernt. Ihre Prüfung bleibt erhalten.",
        ok: "Abbrechen", cancel: "Weiterlaufen lassen", danger: true,
      })) return;
      try {
        await api("POST", `/api/cases/${c.id}/cancel`);
        cancelling = true;
        cancelBtn.disabled = true;
        stepLabel.textContent = "Wird abgebrochen …";
      } catch (err) { showError(err); }
    },
  }, icon("x"), "Abbrechen");
  render(
    h("div", { class: "panel progress-card" },
      h("div", { class: "spinner" }),
      h("h1", {}, isAnalyse ? "Ihre Dokumente werden gelesen …" : "Ihre Dokumente werden anonymisiert und geprüft …"),
      bar, stepLabel,
      h("p", { class: "muted small" }, "Bei großen oder eingescannten Dokumenten kann das einige Minuten dauern."),
      h("div", { class: "row center-row" }, cancelBtn)),
  );
  async function poll() {
    try {
      const cur = await api("GET", `/api/cases/${c.id}`);
      if (cur.status !== c.status) return route();
      const p = cur.progress || {};
      const pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
      bar.firstChild.style.width = `${Math.max(3, pct)}%`;
      stepLabel.textContent = cancelling ? "Wird abgebrochen …"
        : `${p.total > 1 ? `${p.done} von ${p.total} · ` : ""}${p.step || "Wartet …"}`;
    } catch (err) {
      if (err.status === 404) return route();
      stepLabel.textContent = "Verbindung zum Programm unterbrochen …";
    }
    pollTimer = setTimeout(poll, 1000);
  }
  poll();
}

function viewError(c) {
  setTitle("Problem");
  const unreadable = (c.documents || []).filter((d) => d.error);
  const reviewable = !!(c.summary && c.summary.reviewable);
  render(h("div", { class: "panel" },
    h("h1", {}, "Das hat leider nicht geklappt"),
    notice("bad", c.error || "Unbekannter Fehler"),
    unreadable.length > 1 ? h("ul", {}, unreadable.slice(0, 20).map((d) => h("li", {}, h("strong", {}, d.name), ": ", d.error))) : null,
    h("p", { class: "muted" }, "Ihre Originaldateien wurden nicht verändert."),
    h("div", { class: "row" },
      // After a problem while anonymising, the review (decisions, marked
      // areas) is still there – reading everything again would discard it.
      reviewable ? h("button", {
        class: "btn primary", onclick: async () => {
          try { await api("POST", `/api/cases/${c.id}/review`); route(); } catch (err) { showError(err); }
        },
      }, icon("edit"), "Zurück zur Prüfung") : null,
      h("button", {
        class: reviewable ? "btn" : "btn primary", onclick: async () => {
          if (reviewable && !await confirmDialog({
            title: "Dokumente neu lesen?",
            text: "Die Dokumente werden noch einmal von vorn gelesen. Ihre bisherigen Entscheidungen und markierten Bereiche in dieser Akte gehen dabei verloren.",
            ok: "Neu lesen", danger: true,
          })) return;
          try { await api("POST", `/api/cases/${c.id}/analyse`); route(); } catch (err) { showError(err); }
        },
      }, icon("refresh"), reviewable ? "Dokumente neu lesen" : "Noch einmal versuchen"),
      h("a", { class: "btn", href: "#/" }, "Zur Startseite"))));
}

// ---------------------------------------------------------------- review

const CATEGORY_CLASS = (c) =>
  ["PERSON", "ORG", "LOCATION", "ADDRESS", "BIRTH_DATE"].includes(c) ? `c-${c}` : "c-other";
const AREA_NAMES = { unterschrift: "Unterschrift", handschrift: "Handschrift", stempel: "Stempel", bild: "Bild / Logo" };

/** Text with the term highlighted (plain DOM nodes, no HTML). */
function highlighted(text, term) {
  const at = text.toLowerCase().indexOf(String(term).toLowerCase());
  if (at < 0) return [text];
  return ["… ", text.slice(0, at), h("mark", {}, text.slice(at, at + term.length)), text.slice(at + term.length), " …"];
}

async function viewReview(c) {
  setTitle("Prüfen");
  app().classList.add("wide");
  let [entities, settings, attention] = await Promise.all([
    api("GET", `/api/cases/${c.id}/entities`), api("GET", "/api/settings"),
    api("GET", `/api/cases/${c.id}/attention`)]);
  const docs = c.documents.map((d, index) => ({ d, index })).filter((x) => !x.d.error);
  const unreadable = c.documents.filter((d) => d.error);
  const state = { doc: docs.length ? docs[0].index : 0, page: 0, selected: null, area: null, filter: "", zoom: 1, scroll: false };
  const docLabel = (index) => (c.documents.length > 1 ? `${c.documents[index].name}, ` : "");

  const openQuestions = () => entities.filter((e) => e.uncertain && !e.decided);
  const openPages = () => attention.pages.filter((p) => !p.reviewed);

  // ---- left side: the open questions; everything else is folded away ----
  const questionsBox = h("div", { class: "side-section", id: "questions" });
  const filter = h("input", { type: "search", placeholder: "Suchen …", "aria-label": "Ersetzungen durchsuchen" });
  filter.addEventListener("input", () => { state.filter = filter.value.toLowerCase(); drawListRows(); });
  const listRows = h("div");
  const listLabel = h("span", {});
  const listCount = h("span", { class: "count" });
  const listIntro = h("div", { class: "tab-intro" }, "Schalter an = wird ersetzt. Den Ersatztext können Sie ändern.");
  const listBox = h("details", { class: "fold", id: "all-list" },
    h("summary", {}, listLabel, listCount), h("div", { class: "fold-body" }, listIntro, filter, listRows));
  const areasLabel = h("span", {});
  const areasCount = h("span", { class: "count" });
  const areasBody = h("div", { class: "fold-body" });
  const areasBox = h("details", { class: "fold", id: "areas" }, h("summary", {}, areasLabel, areasCount), areasBody);
  const sideBody = h("div", { class: "side-body" }, questionsBox, listBox, areasBox);
  listBox.addEventListener("toggle", drawFoldLabels);
  areasBox.addEventListener("toggle", drawFoldLabels);
  function drawFoldLabels() {
    listLabel.textContent = listBox.open ? "Alle Ersetzungen" : "Alle Ersetzungen anzeigen";
    listCount.textContent = plural(entities.length, "Begriff", "Begriffe");
    areasLabel.textContent = areasBox.open ? "Erkannte Bereiche" : "Erkannte Bereiche anzeigen";
    areasCount.textContent = String((attention.areas || []).length);
  }
  function drawSide() {
    drawQuestions();
    drawList();
    drawAreas();
    drawFoldLabels();
    drawSummary();
  }
  function showAt(doc, page, id, area) {
    state.selected = id || null; state.area = area ?? null; state.scroll = true;
    state.doc = doc; state.page = page; drawSide(); drawPage();
  }

  async function decide(e, redact) {
    try {
      await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { enabled: redact, decided: true });
      e.enabled = redact; e.decided = true;
      drawSide(); drawPage();
    } catch (err) { showError(err); }
  }

  function drawQuestions() {
    const open = openQuestions();
    const pages = openPages();
    if (!open.length && !pages.length) {
      fill(questionsBox, h("div", { class: "all-clear" }, icon("checkCircle"),
        h("div", {}, h("strong", {}, "Keine offenen Fragen"),
          h("div", { class: "small muted" }, "Rechts kurz durch die Seiten blättern, dann speichern."))));
      return;
    }
    const cards = open.slice(0, 150).map((e) => h("div", { class: `q-card ${state.selected === e.id ? "selected" : ""}` },
      h("div", { class: "row between" },
        h("span", { class: "q-term" }, e.original),
        h("span", { class: "count" }, `${e.count}×`)),
      h("div", { class: "q-meta" }, `${e.category_label} · ${e.uncertain}`),
      e.context ? h("div", { class: "q-context" }, highlighted(e.context, e.original)) : null,
      h("div", { class: "q-actions" },
        h("button", { class: `btn small ${e.enabled ? "primary" : ""}`, onclick: () => decide(e, true), title: "Gehört zu einer Person oder Firma – wird ersetzt" }, "Schwärzen"),
        h("button", { class: `btn small ${e.enabled ? "" : "primary"}`, onclick: () => decide(e, false), title: "Kein Personenbezug – bleibt lesbar" }, "Lesbar lassen"),
        h("button", { class: "btn link small", onclick: () => showAt(e.first.doc, e.first.page, e.id) }, "Im Dokument zeigen"))));
    const pageCards = pages.map((p) => h("div", { class: "q-card" },
      h("div", { class: "q-term" }, `${docLabel(p.doc)}Seite ${p.page + 1} ist schlecht lesbar`),
      h("div", { class: "q-meta" }, `Lesbarkeit ${p.quality} % – hier können Namen übersehen werden. Bitte ansehen und Fehlendes mit der Maus einrahmen.`),
      h("div", { class: "q-actions", style: "margin-top:.5rem" },
        h("button", { class: "btn small", onclick: () => showAt(p.doc, p.page) }, icon("eye"), "Seite ansehen"),
        h("button", {
          class: "btn small", onclick: async () => {
            try {
              await api("POST", `/api/cases/${c.id}/pages/reviewed`, { doc: p.doc, page: p.page });
              p.reviewed = true; drawSide();
            } catch (err) { showError(err); }
          },
        }, icon("check"), "Angesehen"))));
    fill(questionsBox,
      h("div", { class: "side-head" }, h("h2", {}, "Offene Fragen"),
        h("span", { class: "count attn" }, String(open.length + pages.length))),
      h("div", { class: "tab-intro" }, "Hier ist sich das Programm nicht sicher. Der blaue Knopf ist der Vorschlag."),
      open.length + pages.length > 1 ? h("button", {
        class: "btn small", style: "margin-bottom:.8rem",
        onclick: async () => {
          if (!await confirmDialog({
            title: "Alle Vorschläge übernehmen?",
            text: `${plural(open.length, "offener Vorschlag wird", "offene Vorschläge werden")} so übernommen wie angezeigt` +
              (pages.length ? ` und ${plural(pages.length, "schlecht lesbare Seite", "schlecht lesbare Seiten")} als angesehen markiert` : "") +
              ". Im Zweifel wird geschwärzt.",
            ok: "Übernehmen",
          })) return;
          try {
            await api("POST", `/api/cases/${c.id}/accept-all`, { pages: true });
            entities.forEach((e) => { if (e.uncertain) e.decided = true; });
            attention.pages.forEach((p) => { p.reviewed = true; });
            drawSide();
          } catch (err) { showError(err); }
        },
      }, icon("check"), "Alle Vorschläge übernehmen") : null,
      cards, open.length > 150 ? h("p", { class: "small muted" }, `… und ${open.length - 150} weitere`) : null, pageCards);
  }

  // ---- everything that is replaced (folded away until asked for) ----
  function drawList() {
    filter.value = state.filter;
    filter.classList.toggle("hidden", entities.length < 9);  // a short list needs no search
    drawListRows();
  }
  function drawListRows() {
    const groups = {};
    for (const e of entities) {
      if (state.filter && !(`${e.original} ${e.replacement}`.toLowerCase().includes(state.filter))) continue;
      (groups[e.category_label] ||= []).push(e);
    }
    const nodes = [];
    for (const [label, items] of Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0], "de"))) {
      nodes.push(h("div", { class: "group-head" }, h("span", {}, label), h("span", {}, String(items.length))));
      for (const e of items) nodes.push(entityRow(e));
    }
    if (!nodes.length) nodes.push(h("p", { class: "muted", style: "margin-top:1rem" }, state.filter ? "Keine Treffer." : "Es wurden keine persönlichen Daten gefunden."));
    fill(listRows, ...nodes);
  }
  function entityRow(e) {
    const cb = h("input", { type: "checkbox", checked: e.enabled, "aria-label": `${e.original} ersetzen` });
    cb.addEventListener("change", async () => {
      try {
        await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { enabled: cb.checked, decided: true });
        e.enabled = cb.checked; e.decided = true; drawQuestions(); drawListRows(); drawSummary(); drawPage();
      } catch (err) { cb.checked = e.enabled; showError(err); }
    });
    const repl = h("input", { type: "text", value: e.replacement, title: "Ersatztext – kann geändert werden", "aria-label": `Ersatztext für ${e.original}` });
    repl.addEventListener("change", async () => {
      if (!repl.value.trim()) { repl.value = e.replacement; return; }
      try {
        await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { replacement: repl.value });
        e.replacement = repl.value.trim(); toast("Ersatztext gespeichert"); drawPage();
      } catch (err) { showError(err); }
    });
    // Rarely needed, therefore one click deeper ("…"): never replace this term again.
    const extra = h("div", { class: "extra hidden" },
      h("button", {
        class: "btn link small",
        onclick: async () => {
          if (!await confirmDialog({
            title: "Nie schwärzen?",
            text: `„${e.original}“ bleibt lesbar und wird auch in künftigen Akten nicht mehr vorgeschlagen.`,
            ok: "Nie schwärzen",
          })) return;
          try {
            await api("POST", `/api/cases/${c.id}/entities/${e.id}/never`);
            e.enabled = false; e.decided = true; drawSide(); drawPage();
          } catch (err) { showError(err); }
        },
      }, "Nie schwärzen – auch nicht in künftigen Akten"));
    const more = h("button", {
      class: "btn link small more", title: "Mehr", "aria-label": `Mehr zu ${e.original}`, "aria-expanded": "false",
      onclick: () => more.setAttribute("aria-expanded", String(!extra.classList.toggle("hidden"))),
    }, "…");
    return h("div", { class: `entity ${e.enabled ? "" : "off"} ${state.selected === e.id ? "selected" : ""}`, "data-id": e.id },
      h("label", { class: "switch", title: "An = wird ersetzt" }, cb, h("span")),
      h("span", { class: "orig", title: e.variants.join(" | ") }, e.original,
        e.uncertain && !e.decided ? h("span", { class: "count attn", title: e.uncertain, style: "margin-left:.4rem" }, "offen") : null),
      h("span", { class: "count", title: "Anzahl der Fundstellen" }, `${e.count}×`),
      h("div", { class: "repl" }, icon("arrowRight"), repl,
        h("button", { class: "btn link small", title: "Erste Fundstelle im Dokument zeigen", onclick: () => showAt(e.first.doc, e.first.page, e.id) }, "zeigen"),
        more),
      extra);
  }

  // ---- "Bereiche": signatures, handwriting, stamps, images ----
  async function removeArea(index) {
    try {
      await api("DELETE", `/api/cases/${c.id}/manual/${index}`);
      attention = await api("GET", `/api/cases/${c.id}/attention`);  // indices shift
      state.area = null;
      drawSide(); drawPage();
    } catch (err) { showError(err); }
  }
  function drawAreas() {
    const areas = attention.areas || [];
    areasBox.classList.toggle("hidden", !areas.length);
    fill(areasBody,
      h("div", { class: "tab-intro" }, "Unterschriften, Handschrift, Stempel und kleine Bilder werden vollständig geschwärzt. " +
        "Enthält ein Bereich nichts Persönliches, können Sie ihn sichtbar lassen."),
      areas.slice(0, 200).map((a) => h("div", { class: `q-card ${state.area === a.index ? "selected" : ""}` },
        h("div", { class: "row between" },
          h("span", { class: "q-term" }, AREA_NAMES[a.kind] || a.kind),
          h("span", { class: "q-meta" }, `${docLabel(a.doc)}Seite ${a.page + 1}`)),
        h("div", { class: "q-actions", style: "margin-top:.45rem" },
          h("button", { class: "btn small", onclick: () => showAt(a.doc, a.page, null, a.index) }, icon("eye"), "Zeigen"),
          h("button", { class: "btn small", onclick: () => removeArea(a.index) }, "Sichtbar lassen")))),
      areas.length > 200 ? h("p", { class: "small muted" }, `… und ${areas.length - 200} weitere`) : null);
  }

  // ---- add a term ----
  const termInput = h("input", { type: "text", placeholder: "Name oder Begriff, z. B. Mustermann", "aria-label": "Begriff" });
  const termCat = h("select", { "aria-label": "Art" },
    Object.entries(settings.category_labels).map(([v, l]) => h("option", { value: v }, l)),
    h("option", { value: "MANUAL" }, "Sonstiges"));
  termCat.value = "PERSON";
  const termAlways = h("input", { type: "checkbox" });
  const addTerm = h("details", { class: "add-term" },
    h("summary", {}, icon("plus"), "Etwas fehlt? Begriff hinzufügen"),
    h("form", {
      onsubmit: async (ev) => {
        ev.preventDefault();
        try {
          const res = await api("POST", `/api/cases/${c.id}/entities`, { text: termInput.value, category: termCat.value, always: termAlways.checked });
          toast(res.count ? `${plural(res.count, "Fundstelle", "Fundstellen")} hinzugefügt` : "Der Begriff kommt im Text nicht vor. Bei Scans bitte die Stelle mit der Maus einrahmen.", !res.count);
          termInput.value = "";
          entities = await api("GET", `/api/cases/${c.id}/entities`);
          drawSide(); drawPage();
        } catch (err) { showError(err); }
      },
    },
      h("div", { class: "row", style: "flex-wrap:nowrap" }, termInput, h("button", { class: "btn primary", type: "submit" }, "Hinzufügen")),
      h("div", { class: "row", style: "margin-top:.5rem" }, h("span", { class: "small muted" }, "Art:"), h("div", { style: "flex:1;min-width:10rem" }, termCat)),
      h("label", { class: "check small muted" }, termAlways, "auch in künftigen Akten immer schwärzen")));

  // ---- page viewer (right) ----
  const img = h("img", { alt: "Seitenvorschau", draggable: "false" });
  const overlay = h("div", { class: "overlay" });
  const wrap = h("div", { class: "page-wrap" }, img, overlay);
  const scroller = h("div", { class: "page-scroll" }, wrap);
  const pageInput = h("input", { type: "number", min: "1", value: "1", "aria-label": "Seite" });
  const pageCount = h("span", { class: "muted nowrap" });
  const kindInfo = h("span", { class: "muted small" });
  const docSelect = h("select", { "aria-label": "Dokument" }, docs.map(({ d, index }) => h("option", { value: index }, d.name)));
  docSelect.addEventListener("change", () => { state.doc = +docSelect.value; state.page = 0; state.selected = null; state.area = null; drawPage(); });
  pageInput.addEventListener("change", () => { state.page = Math.max(0, (+pageInput.value || 1) - 1); drawPage(); });
  let pageData = null;
  const applyZoom = () => { wrap.style.width = `${Math.round(state.zoom * 100)}%`; wrap.style.maxWidth = state.zoom > 1 ? "none" : "100%"; };

  async function drawPage() {
    if (!docs.length) return;
    try {
      pageData = await api("GET", `/api/cases/${c.id}/docs/${state.doc}/pages/${state.page}/boxes`);
    } catch (err) {
      if (err.status === 404 && state.page > 0) { state.page = 0; return drawPage(); }
      return showError(err);
    }
    docSelect.value = state.doc;
    pageInput.value = state.page + 1;
    pageInput.max = pageData.pages;
    pageCount.textContent = `von ${pageData.pages}`;
    kindInfo.textContent = pageData.kind === "scan"
      ? `Scan${pageData.quality !== null ? ` · Lesbarkeit ${pageData.quality} %` : ""}` : "";
    kindInfo.title = pageData.kind === "scan" ? "Eingescannte Seite – per Texterkennung gelesen" : "";
    const src = `/api/cases/${c.id}/docs/${state.doc}/pages/${state.page}.png?zoom=${state.zoom > 1.2 ? 2.4 : 1.7}`;
    if (img.getAttribute("src") !== src) img.src = src;
    const W = pageData.width, H = pageData.height;
    const pct = (r) => `left:${(100 * r[0]) / W}%;top:${(100 * r[1]) / H}%;width:${(100 * (r[2] - r[0])) / W}%;height:${(100 * (r[3] - r[1])) / H}%`;
    const boxes = pageData.boxes.map((b) => {
      const ent = entities.find((e) => e.id === b.entity);
      const enabled = ent ? ent.enabled : b.enabled;
      return h("div", {
        class: `hl ${CATEGORY_CLASS(b.category)} ${enabled ? "" : "off"} ${state.selected === b.entity ? "sel" : ""}`,
        style: pct(b.rect), title: enabled ? `wird ersetzt durch: ${b.label}` : "bleibt lesbar (wird nicht geschwärzt)",
        onmousedown: (ev) => ev.stopPropagation(),
        onclick: () => {
          // A click on a marked spot shows its entry on the left: the open
          // question, otherwise the row in the (then unfolded) list.
          state.selected = b.entity; state.area = null;
          const question = ent && ent.uncertain && !ent.decided;
          if (!question) listBox.open = true;
          state.filter = "";
          drawSide(); drawPage();
          const row = (question && questionsBox.querySelector(".q-card.selected")) || listRows.querySelector(`[data-id="${b.entity}"]`);
          if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
        },
      });
    });
    const manual = pageData.manual.map((m) => {
      const auto = m.kind && m.kind !== "manuell";
      return h("div", {
        class: `hl manual ${auto ? "auto" : ""} ${state.area === m.index ? "sel" : ""}`, style: pct(m.rect),
        title: auto ? `Automatisch erkannt: ${m.label} – wird geschwärzt` : "Von Ihnen markierter Bereich – wird geschwärzt",
        onmousedown: (ev) => ev.stopPropagation(),
      }, auto ? h("span", { class: "tag" }, m.label) : null, h("span", {
        class: "x", title: auto ? "Sichtbar lassen (nicht schwärzen)" : "Markierung entfernen",
        onclick: () => removeArea(m.index),
      }, "×"));
    });
    fill(overlay, ...boxes, ...manual);
    if (state.scroll) {  // "zeigen": bring the marked spot into view
      state.scroll = false;
      const target = overlay.querySelector(".hl.sel");
      const go = () => (target ? target.scrollIntoView({ block: "center", behavior: "smooth" }) : scroller.scrollTo(0, 0));
      if (img.complete) go(); else img.addEventListener("load", go, { once: true });
    }
  }

  // Draw additional redaction areas with the mouse.
  let start = null, rubber = null;
  overlay.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    const r = overlay.getBoundingClientRect();
    start = { x: ev.clientX - r.left, y: ev.clientY - r.top, r };
    rubber = h("div", { class: "drawing" });
    overlay.append(rubber);
    ev.preventDefault();
  });
  const onMove = (ev) => {
    if (!start) return;
    const x = Math.max(0, Math.min(start.r.width, ev.clientX - start.r.left));
    const y = Math.max(0, Math.min(start.r.height, ev.clientY - start.r.top));
    Object.assign(rubber.style, {
      left: `${Math.min(x, start.x)}px`, top: `${Math.min(y, start.y)}px`,
      width: `${Math.abs(x - start.x)}px`, height: `${Math.abs(y - start.y)}px`,
    });
  };
  const onUp = async (ev) => {
    if (!start || !pageData) return;
    const s = start; start = null;
    const x = Math.max(0, Math.min(s.r.width, ev.clientX - s.r.left));
    const y = Math.max(0, Math.min(s.r.height, ev.clientY - s.r.top));
    rubber.remove();
    if (Math.abs(x - s.x) < 6 || Math.abs(y - s.y) < 6) return;
    const sx = pageData.width / s.r.width, sy = pageData.height / s.r.height;
    const rect = [Math.min(x, s.x) * sx, Math.min(y, s.y) * sy, Math.max(x, s.x) * sx, Math.max(y, s.y) * sy];
    try {
      await api("POST", `/api/cases/${c.id}/manual`, { doc: state.doc, page: state.page, rect });
      toast("Der markierte Bereich wird geschwärzt");
      drawPage();
    } catch (err) { showError(err); }
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  cleanups.push(() => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); });

  const go = (delta) => {
    if (!pageData) return;
    const next = Math.max(0, Math.min(pageData.pages - 1, state.page + delta));
    if (next !== state.page) { state.page = next; state.scroll = true; state.selected = null; state.area = null; drawPage(); }
  };
  const goDoc = (delta) => {
    const pos = docs.findIndex((x) => x.index === state.doc);
    const next = docs[Math.max(0, Math.min(docs.length - 1, pos + delta))];
    if (next && next.index !== state.doc) { state.doc = next.index; state.page = 0; state.scroll = true; drawPage(); }
  };
  document.onkeydown = (ev) => {
    if (modalStack.length || ["INPUT", "SELECT", "TEXTAREA"].includes(ev.target.tagName)) return;
    if (ev.key === "ArrowRight" || ev.key === "PageDown") { ev.preventDefault(); go(1); }
    if (ev.key === "ArrowLeft" || ev.key === "PageUp") { ev.preventDefault(); go(-1); }
  };

  // ---- bottom bar: what happens, and the one big button ----
  const summary = h("span", {});
  function drawSummary() {
    const on = entities.filter((e) => e.enabled);
    const places = on.reduce((s, e) => s + e.count, 0);
    const open = openQuestions().length + openPages().length;
    fill(summary, h("strong", {}, `${plural(places, "Stelle wird", "Stellen werden")} ersetzt`),
      open ? h("span", { class: "chip warn", style: "margin-left:.6rem" }, plural(open, "offene Frage", "offene Fragen")) : null);
  }
  const redactBtn = h("button", {
    class: "btn primary big", id: "btn-redact",
    onclick: async () => {
      const open = openQuestions().length + openPages().length;
      if (open && !await confirmDialog({
        title: `Noch ${plural(open, "offene Frage", "offene Fragen")}`,
        text: ["Sie können trotzdem fortfahren: Offene Vorschläge werden so übernommen wie angezeigt – im Zweifel wird geschwärzt.",
          "Schlecht lesbare Seiten sollten Sie vorher selbst ansehen."],
        ok: "Trotzdem anonymisieren", cancel: "Zurück zu den Fragen",
      })) { sideBody.scrollTo(0, 0); return; }
      redactBtn.disabled = true;
      try { await api("POST", `/api/cases/${c.id}/redact`); route(); }
      catch (err) { showError(err); redactBtn.disabled = false; }
    },
  }, "Anonymisieren und speichern", icon("arrowRight"));
  setActionbar(h("div", { class: "actionbar" }, h("div", { class: "inner" },
    summary, h("span", { class: "spacer" }), redactBtn)));

  const hints = [...(c.warnings || []), ...c.documents.flatMap((d) => d.notes || []).filter((n) => !n.includes("schlecht lesbar"))];
  render(
    h("div", { class: "review-head" },
      h("h1", {}, c.title),
      h("span", { class: "spacer" }),
      h("span", { class: "muted" }, `${plural(docs.length, "Dokument", "Dokumente")} · ${plural(docs.reduce((s, x) => s + x.d.pages, 0), "Seite", "Seiten")}`)),
    unreadable.length ? notice("warn", h("strong", {}, `${plural(unreadable.length, "Dokument konnte", "Dokumente konnten")} nicht gelesen werden und ${unreadable.length === 1 ? "wird" : "werden"} übersprungen: `),
      h("ul", { style: "margin:.3rem 0 0;padding-left:1.2rem" }, unreadable.slice(0, 10).map((d) => h("li", {}, h("strong", {}, d.name), " – ", d.error))),
      unreadable.length > 10 ? `… und ${unreadable.length - 10} weitere` : null) : null,
    hints.length ? h("details", { class: "hintbox" }, h("summary", {}, plural(hints.length, "Hinweis", "Hinweise") + " zur Verarbeitung"),
      h("ul", { style: "margin:.4rem 0 0;padding-left:1.2rem" }, hints.slice(0, 30).map((n) => h("li", {}, n)))) : null,
    h("div", { class: "review" },
      h("div", { class: "panel side" }, sideBody, addTerm),
      h("div", { class: "panel viewer" },
        // One line: document (only if there are several), page, zoom.
        h("div", { class: "viewer-bar" },
          docs.length > 1 ? h("span", { class: "group" },
            h("button", { class: "btn icon-only", title: "Vorheriges Dokument", "aria-label": "Vorheriges Dokument", onclick: () => goDoc(-1) }, icon("chevronLeft")),
            docSelect,
            h("button", { class: "btn icon-only", title: "Nächstes Dokument", "aria-label": "Nächstes Dokument", onclick: () => goDoc(1) }, icon("chevronRight"))) : null,
          h("span", { class: "group" },
            h("button", { class: "btn icon-only", onclick: () => go(-1), title: "Vorherige Seite (Pfeiltaste links)", "aria-label": "Vorherige Seite" }, icon("chevronLeft")),
            h("span", { class: "nowrap" }, "Seite"), pageInput, pageCount,
            h("button", { class: "btn icon-only", onclick: () => go(1), title: "Nächste Seite (Pfeiltaste rechts)", "aria-label": "Nächste Seite" }, icon("chevronRight"))),
          kindInfo,
          h("span", { class: "spacer" }),
          h("span", { class: "group" },
            h("button", { class: "btn icon-only", title: "Kleiner", "aria-label": "Kleiner", onclick: () => { state.zoom = Math.max(0.6, state.zoom - 0.25); applyZoom(); drawPage(); } }, icon("minus")),
            h("button", { class: "btn icon-only", title: "Größer", "aria-label": "Größer", onclick: () => { state.zoom = Math.min(2.5, state.zoom + 0.25); applyZoom(); drawPage(); } }, icon("plus")))),
        scroller,
        h("div", { class: "viewer-foot" },
          h("span", {}, icon("edit"), " Fehlt etwas? Mit der Maus einen Rahmen um die Stelle ziehen."),
          h("span", { class: "spacer" }),
          h("span", { class: "legend", "aria-label": "Farben der Markierungen" },
            h("span", { class: "c-PERSON" }, "Person"), h("span", { class: "c-ORG" }, "Firma"),
            h("span", { class: "c-ADDRESS" }, "Adresse"), h("span", { class: "c-BIRTH_DATE" }, "Geburtsdatum"),
            h("span", { class: "c-other" }, "Sonstiges"))))),
  );
  applyZoom();
  drawSide();
  drawPage();
}

// ---------------------------------------------------------------- result

function docState(d) {
  if (d.error) return "error";
  return (d.verification || {}).passed ? "ok" : "failed";
}

/** Verification stages that could not be carried out (neither passed nor failed). */
const skippedChecks = (v) => ((v || {}).checks || []).filter((ch) => ch.skipped);

function viewResult(c) {
  setTitle("Ergebnis");
  const docs = c.documents.map((d, i) => ({ d, i, v: d.verification || {}, state: docState(d) }));
  const ok = docs.filter((x) => x.state === "ok");
  const failed = docs.filter((x) => x.state === "failed");
  const errors = docs.filter((x) => x.state === "error");
  const hints = ok.filter((x) => (x.v.suspects || []).length);
  const unchecked = ok.filter((x) => skippedChecks(x.v).length);
  const order = { failed: 0, error: 1, ok: 2 };
  docs.sort((a, b) => order[a.state] - order[b.state] || ((b.v.suspects || []).length - (a.v.suspects || []).length) || a.i - b.i);

  const rows = docs.map(({ d, i, v, state }) => h("div", { class: "doc-row" },
    state === "ok" ? (skippedChecks(v).length
      ? h("span", { class: "chip warn", title: "Mindestens eine Prüfstufe wurde nicht ausgeführt" }, icon("alert"), "Teilweise geprüft")
      : h("span", { class: "chip ok" }, icon("check"), "Geprüft"))
      : state === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Nicht bestanden")
        : h("span", { class: "chip bad" }, icon("x"), "Nicht verarbeitet"),
    h("div", {},
      h("div", { class: "name" }, state === "error" ? d.name : baseName(d.output)),
      h("div", { class: "sub" }, state === "error" ? d.error
        : state === "failed" ? "Bitte nicht weitergeben. Die Datei liegt getrennt im Ordner „VERTRAULICH_nicht_weitergeben“."
          : `${plural(d.pages, "Seite", "Seiten")}${(v.suspects || []).length ? ` · ${plural(v.suspects.length, "Hinweis", "Hinweise")} der Nachkontrolle` : ""}`)),
    state === "error" ? h("span", {}) : h("div", { class: "actions" },
      h("button", {
        class: `btn small ${state === "failed" || (v.suspects || []).length ? "primary" : ""}`,
        title: "Vorschau, Vergleich mit dem Original und Prüfdetails", onclick: () => openDocDetails(c, d, i),
      }, icon("eye"), "Ansehen"))));

  let heroClass = "", heroIcon = "check", headline, sub;
  if (failed.length) {
    heroClass = "bad"; heroIcon = "alert";
    headline = `${plural(failed.length, "Dokument hat", "Dokumente haben")} die Prüfung nicht bestanden`;
    sub = "Diese Dokumente bitte nicht weitergeben. Unter „Ansehen“ steht, was gefunden wurde – mit „Nachbessern“ ergänzen Sie Schwärzungen.";
  } else if (errors.length) {
    heroClass = "warn"; heroIcon = "alert";
    headline = `${plural(ok.length, "Dokument", "Dokumente")} fertig, ${errors.length} nicht verarbeitet`;
    sub = "Die verarbeiteten Dokumente haben alle Prüfungen bestanden. Die Gründe für die übrigen stehen unten.";
  } else if (unchecked.length) {
    heroClass = "warn"; heroIcon = "alert";
    headline = ok.length === 1 ? "Fertig – aber nicht vollständig geprüft" : `Fertig – ${unchecked.length} von ${ok.length} Dokumenten nicht vollständig geprüft`;
    sub = `Nicht ausgeführt: ${[...new Set(unchecked.flatMap((x) => skippedChecks(x.v).map((ch) => ch.name.split(" – ").slice(0, 2).join(" – "))))].join(", ")}. ` +
      "Die übrigen Prüfungen sind bestanden. Sie können die Prüfstufen in den Einstellungen (Erweitert) wieder einschalten.";
  } else {
    headline = ok.length === 1 ? "Fertig – Ihr Dokument ist anonymisiert" : `Fertig – ${ok.length} Dokumente sind anonymisiert`;
    sub = "Alle Prüfungen bestanden. Der Ordner enthält nur geprüfte Ergebnisse und kann als Ganzes weitergegeben werden.";
  }
  const hasFolder = !!(c.summary ? c.summary.has_folder : c.output_dir);
  // The folder's name is enough here; the full path is the tooltip.
  const folder = hasFolder ? h("strong", { title: c.output_dir }, baseName(c.output_dir) || c.output_dir) : null;

  render(
    h("div", { class: `panel result-hero ${heroClass}` },
      h("div", { class: "big-icon" }, icon(heroIcon)),
      h("h1", {}, headline),
      h("p", { class: "muted lead" }, sub),
      h("div", { class: "row" },
        hasFolder ? h("button", {
          class: "btn primary big", id: "btn-open-folder", title: c.output_dir, onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/open-folder`); } catch (err) { showError(err); }
          },
        }, icon("folder"), "Ordner öffnen") : null,
        h("a", { class: "btn big", href: "#/" }, icon("plus"), "Weitere Dokumente")),
      h("p", { class: "muted small below" },
        hasFolder ? ["Gespeichert in ", folder] : null,
        hasFolder && ok.length ? " · " : null,
        ok.length ? h("a", { id: "link-zip", href: `/api/cases/${c.id}/zip`, title: "Alle geprüften Ergebnisse in einer ZIP-Datei – ohne die vertrauliche Zuordnungstabelle" }, "Alle als ZIP herunterladen") : null)),
    hints.length && !failed.length ? notice("info", `${plural(hints.length, "Dokument enthält", "Dokumente enthalten")} Hinweise der Nachkontrolle – meist Fehlalarme. Bitte kurz ansehen.`) : null,
    (c.warnings || []).length ? h("details", { class: "hintbox" }, h("summary", {}, plural(c.warnings.length, "Hinweis", "Hinweise") + " zur Verarbeitung"),
      h("ul", { style: "margin:.4rem 0 0;padding-left:1.2rem" }, c.warnings.slice(0, 30).map((w) => h("li", {}, w)))) : null,
    h("div", { class: "panel" },
      h("h2", {}, "Dokumente"),
      rows,
      // Rarely needed: one row of secondary links, the explanations fold out.
      h("div", { class: "result-links" },
        h("button", {
          class: "btn link small", title: "Schwärzungen ergänzen oder ändern – die bisherigen Ergebnisse dieser Akte werden dabei ersetzt", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/review`); route(); } catch (err) { showError(err); }
          },
        }, icon("edit"), "Nachbessern"),
        c.mapping ? h("a", {
          class: "btn link small", href: `/api/cases/${c.id}/mapping`, target: "_blank", rel: "noopener",
          title: "Zeigt, welcher Name zu welcher Abkürzung wurde. Liegt getrennt im Ordner „VERTRAULICH_nicht_weitergeben“ – niemals weitergeben!",
        }, icon("lock"), "Zuordnungstabelle (vertraulich)") : null,
        h("details", { class: "checks-info" },
          h("summary", { class: "btn link small" }, icon("shield"), "Was wurde geprüft?"),
          h("ol", { class: "plain" },
            h("li", {}, h("strong", {}, "Text: "), "Jeder gefundene Name und jede Nummer wird in der neuen Datei gesucht – auch in unsichtbarem Text und im Dateinamen."),
            h("li", {}, h("strong", {}, "Bild: "), "Jede fertige Seite wird per Texterkennung noch einmal gelesen und durchsucht."),
            h("li", {}, h("strong", {}, "Wiederherstellung: "), "Das Programm versucht selbst, die entfernten Daten aus der Datei zurückzuholen."),
            h("li", {}, h("strong", {}, "Nachkontrolle: "), "Die Erkennung läuft noch einmal über das Ergebnis.")),
          h("p", { class: "muted small" }, "Selbst nachprüfen: PDF öffnen, Strg+F drücken und einen der Namen suchen – er darf nicht gefunden werden."),
          c.mapping ? h("p", { class: "muted small" }, "Die Zuordnungstabelle liegt getrennt von den Ergebnissen im Ordner „VERTRAULICH_nicht_weitergeben“.") : null))),
  );
}

function openDocDetails(c, d, i) {
  const modal = openModal([
    modalHead(d.name, () => modal),
    resultCard(c, d, i, () => modal.close()),
  ], { size: "wide" });
}

function resultCard(c, d, i, close) {
  const v = d.verification || {};
  const passed = v.passed;
  const base = `/api/cases/${c.id}/docs/${i}`;
  const view = { page: 1, original: false };
  const previewImg = h("img", { alt: "Seitenvorschau" });
  const pageInput = h("input", { type: "number", min: "1", max: d.pages, value: "1", style: "width:4.6rem", "aria-label": "Seite" });
  const btnAfter = h("button", { type: "button" }, "Anonymisiert");
  const btnBefore = h("button", { type: "button" }, "Original");
  const show = () => {
    view.page = Math.max(1, Math.min(d.pages, view.page || 1));
    pageInput.value = view.page;
    previewImg.src = `${base}/${view.original ? "" : "output/"}pages/${view.page - 1}.png?zoom=1.5`;
    btnAfter.classList.toggle("active", !view.original);
    btnBefore.classList.toggle("active", view.original);
  };
  btnAfter.addEventListener("click", () => { view.original = false; show(); });
  btnBefore.addEventListener("click", () => { view.original = true; show(); });
  pageInput.addEventListener("change", () => { view.page = +pageInput.value; show(); });
  show();

  const redoBtn = h("button", {
    class: "btn primary hidden",
    onclick: async () => {
      try { await api("POST", `/api/cases/${c.id}/redact`); close(); route(); }
      catch (err) { showError(err); }
    },
  }, icon("refresh"), "Korrekturen übernehmen und neu anonymisieren");
  const suspects = (v.suspects || []).map((s) => {
    const li = h("li", { style: "margin-bottom:.45rem" },
      h("button", { class: "btn link", onclick: () => { view.page = s.page; show(); } }, `Seite ${s.page}`), ": ",
      h("strong", {}, s.text), " ");
    const decideSuspect = async (redact) => {
      try {
        await api("POST", `/api/cases/${c.id}/suspects`, { text: s.text, category: s.category, redact });
        fill(li, h("span", { class: "muted" }, `${s.text}: ${redact ? "wird geschwärzt" : "bleibt lesbar (gemerkt)"}`));
        if (redact) redoBtn.classList.remove("hidden");
      } catch (err) { showError(err); }
    };
    li.append(h("button", { class: "btn small", onclick: () => decideSuspect(true) }, "Schwärzen"), " ",
      h("button", { class: "btn small", onclick: () => decideSuspect(false) }, "Ist in Ordnung"));
    return li;
  });
  const leaks = (v.leaks || []).map((l) => h("li", {}, `Seite ${l.page || "–"} (${l.where}): `, h("strong", {}, l.term || "nicht prüfbarer Bereich")));

  return h("div", {},
    passed
      ? (skippedChecks(v).length
        ? notice("warn", h("strong", {}, "Bestanden, aber nicht vollständig geprüft. "),
          `Nicht ausgeführt: ${skippedChecks(v).map((ch) => `${ch.name} (${ch.detail})`).join("; ")}.`)
        : notice("ok", h("strong", {}, "Prüfung bestanden. "),
          `Keine der ${v.terms_checked} bekannten Originalangaben ist im Ergebnis enthalten oder wiederherstellbar (${plural(v.pages_checked, "Seite", "Seiten")} geprüft).`))
      : notice("bad", h("strong", {}, "Prüfung nicht bestanden – bitte NICHT weitergeben. "),
        "Gehen Sie über „Nachbessern“ zurück und ergänzen Sie die Schwärzungen, z. B. indem Sie die Stelle mit der Maus einrahmen."),
    h("div", { class: "row" },
      passed ? h("button", {
        class: "btn primary", title: "Anonymisiertes PDF im PDF-Programm öffnen", onclick: async () => {
          try { await api("POST", `${base}/open/pdf`); } catch (err) { showError(err); }
        },
      }, icon("external"), "Öffnen") : null,
      passed ? h("a", { class: "btn", href: `${base}/download/pdf` }, icon("download"), "Speichern unter …") : null,
      passed ? h("a", { class: "btn", href: `${base}/download/txt`, title: "Reiner Text in Lesereihenfolge – z. B. zum Einfügen in ein KI-Werkzeug" }, icon("text"), "Textfassung") : null,
      h("a", { class: "btn", href: `${base}/download/report?inline=1`, target: "_blank", rel: "noopener" }, icon("shield"), "Prüfbericht")),
    leaks.length ? [h("h3", {}, "Noch gefundene Originaldaten"), h("ul", {}, leaks.slice(0, 50))] : null,
    suspects.length ? [
      h("h3", {}, "Nachkontrolle: bitte kurz entscheiden"),
      h("p", { class: "muted small" }, "Diese Stellen könnten noch persönliche Daten sein – oft sind es Fehlalarme (z. B. Fachbegriffe)."),
      h("ul", { style: "padding-left:1.2rem" }, suspects), redoBtn] : null,
    h("h3", {}, "Vorschau"),
    h("div", { class: "viewer-bar" },
      h("div", { class: "segmented", title: "Zum Vergleichen umschalten" }, btnAfter, btnBefore),
      h("span", { class: "spacer" }),
      h("button", { class: "btn icon-only", title: "Vorherige Seite", onclick: () => { view.page -= 1; show(); } }, icon("chevronLeft")),
      h("span", {}, "Seite"), pageInput, h("span", { class: "muted" }, `von ${d.pages}`),
      h("button", { class: "btn icon-only", title: "Nächste Seite", onclick: () => { view.page += 1; show(); } }, icon("chevronRight"))),
    h("div", { class: "preview" }, previewImg),
    h("details", { style: "margin-top:1rem" }, h("summary", { style: "cursor:pointer;font-weight:600" }, "Alle automatischen Prüfungen anzeigen"),
      h("ul", { class: "checks" }, (v.checks || []).map((ch) =>
        h("li", { class: ch.skipped ? "skip" : ch.ok ? "ok" : "bad" }, ch.name, ch.detail ? h("span", { class: "muted small" }, ` – ${ch.detail}`) : null)))));
}

// ---------------------------------------------------------- folder picker

/** Choose the folder for results; saves it and resolves to the path (or null). */
function pickFolder(current) {
  return new Promise((resolve) => {
    let result = null;
    let data = null;
    const crumbs = h("div", { class: "crumbs" });
    const list = h("div", { class: "folder-list" });
    const shortcuts = h("div", { class: "shortcut-list" });
    const info = h("div", {});
    const chooseBtn = h("button", { class: "btn primary" }, icon("check"), "Diesen Ordner verwenden");
    const load = async (path) => {
      try { data = await api("GET", `/api/folders?path=${encodeURIComponent(path || "")}`); }
      catch (err) { return showError(err); }
      draw();
    };
    const draw = () => {
      // Windows: "C:\\Users\\x" (or "\\\\server\\share\\x"), elsewhere "/home/x".
      const sep = data.path.includes("\\") ? "\\" : "/";
      const unc = data.path.startsWith("\\\\");
      const parts = data.path.split(sep).filter(Boolean);
      const nodes = [];
      parts.forEach((part, n) => {
        let target;
        if (sep === "/") target = "/" + parts.slice(0, n + 1).join("/");
        else if (unc) target = "\\\\" + parts.slice(0, Math.max(2, n + 1)).join("\\");
        else target = parts.slice(0, n + 1).join("\\") + (n === 0 ? "\\" : "");
        if (n) nodes.push(h("span", { class: "muted" }, "›"));
        nodes.push(h("button", { onclick: () => load(target) }, part));
      });
      fill(crumbs, icon("folder"), ...nodes);
      fill(list, 
        data.parent ? h("button", { class: "folder-item", onclick: () => load(data.parent) }, icon("arrowLeft"), "Eine Ebene höher") : null,
        ...data.folders.map((f) => h("button", { class: "folder-item", onclick: () => load(f.path), title: "Öffnen" }, icon("folder"), f.name)),
        !data.readable ? h("p", { class: "muted small", style: "padding:.8rem" }, "Dieser Ordner kann nicht gelesen werden.")
          : !data.folders.length ? h("p", { class: "muted small", style: "padding:.8rem" }, "Keine Unterordner.") : null);
      fill(shortcuts, ...data.shortcuts.map((s) => h("button", { class: "folder-item", onclick: () => load(s.path) }, icon(s.label.startsWith("Laufwerk") ? "archive" : "folder"), s.label)));
      fill(info, 
        !data.writable ? notice("bad", "In diesen Ordner kann nicht gespeichert werden. Bitte einen anderen wählen.") : null,
        data.cloud ? notice("warn", "Dieser Ordner wird vermutlich mit einem Cloud-Dienst synchronisiert (z. B. OneDrive). Ergebnisse und die vertrauliche Zuordnungstabelle würden dorthin hochgeladen.") : null);
      chooseBtn.disabled = !data.writable;
    };
    chooseBtn.addEventListener("click", async () => {
      try {
        const res = await api("POST", "/api/settings", { output_dir: data.path });
        result = res.output_dir;
        toast("Speicherort geändert");
        modal.close();
      } catch (err) { showError(err); }
    });
    const modal = openModal([
      modalHead("Wo sollen die Ergebnisse gespeichert werden?", () => modal),
      h("p", { class: "muted small" }, "Für jede Akte legt das Programm dort einen eigenen Unterordner mit Datum an."),
      crumbs,
      h("div", { class: "folder-layout" }, shortcuts, list),
      info,
      h("div", { class: "modal-foot" },
        h("button", {
          class: "btn", onclick: async () => {
            const name = await promptDialog({ title: "Neuer Ordner", label: `Name des neuen Ordners in „${baseName(data.path) || data.path}“:`, value: "Anonymisiert", ok: "Anlegen" });
            if (!name) return;
            try { data = await api("POST", "/api/folders", { parent: data.path, name }); draw(); }
            catch (err) { showError(err); }
          },
        }, icon("plus"), "Neuer Ordner"),
        h("span", { class: "spacer" }),
        h("button", { class: "btn", onclick: () => modal.close() }, "Abbrechen"),
        chooseBtn),
    ], { onClose: () => resolve(result) });
    load(current || "");
  });
}

// -------------------------------------------------------------- settings

async function openSettings() {
  const [s, stats, status] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/learning"), api("GET", "/api/status")]);
  const toggle = (checked) => h("input", { type: "checkbox", checked });
  const cats = Object.entries(s.category_labels).map(([value, label]) => {
    const cb = h("input", { type: "checkbox", value, checked: s.categories.includes(value) });
    return { cb, el: h("label", { class: "check" }, cb, label) };
  });
  const modes = [
    ["standard", "Standard (empfohlen)", "Text bleibt Text: kleine, durchsuchbare Dateien."],
    ["maximum", "Maximale Sicherheit", "Jede Seite wird als Bild neu aufgebaut: größere Dateien."],
  ].map(([value, label, text]) => {
    const radio = h("input", { type: "radio", name: "mode", value, checked: s.security_mode === value });
    const el = h("label", { class: `choice ${s.security_mode === value ? "active" : ""}` }, radio,
      h("div", {}, h("strong", {}, label), h("div", { class: "muted small" }, text)));
    return { radio, el };
  });
  modes.forEach((m) => m.radio.addEventListener("change", () => modes.forEach((x) => x.el.classList.toggle("active", x.radio.checked))));
  const gliner = toggle(s.use_gliner), deep = toggle(s.deep_verification), mapping = toggle(s.write_mapping);
  const learn = toggle(s.use_learning), images = toggle(s.remove_small_images), signatures = toggle(s.detect_signatures);
  const forensic = toggle(s.forensic_check), autoTrain = toggle(s.auto_train), appWindow = toggle(s.app_window);
  const keep = h("select", { style: "width:auto" },
    [[7, "nach 7 Tagen"], [30, "nach 30 Tagen"], [90, "nach 90 Tagen"], [365, "nach einem Jahr"], [0, "nie automatisch"]]
      .map(([v, l]) => h("option", { value: v }, l)));
  keep.value = [7, 30, 90, 365, 0].includes(s.keep_days) ? s.keep_days : 30;
  const outDir = pathLabel(s.output_dir);
  const allow = h("textarea", { "aria-label": "Nie schwärzen" }, s.allow_terms.join("\n"));
  const deny = h("textarea", { "aria-label": "Immer schwärzen" }, s.deny_terms.map((t) => t.text).join("\n"));

  const save = async () => {
    try {
      const res = await api("POST", "/api/settings", {
        categories: cats.filter((x) => x.cb.checked).map((x) => x.cb.value),
        security_mode: modes.find((m) => m.radio.checked).radio.value,
        use_gliner: gliner.checked, deep_verification: deep.checked, write_mapping: mapping.checked,
        use_learning: learn.checked, remove_small_images: images.checked, forensic_check: forensic.checked,
        auto_train: autoTrain.checked, detect_signatures: signatures.checked, app_window: appWindow.checked,
        keep_days: +keep.value,
        allow_terms: allow.value.split("\n"),
        deny_terms: deny.value.split("\n").map((t) => t.trim()).filter(Boolean)
          .map((text) => (s.deny_terms.find((x) => x.text === text) || { text, category: "MANUAL" })),
      });
      modal.close();
      toast("Einstellungen gespeichert – sie gelten ab der nächsten Analyse");
      if (res.warning) toast(res.warning, true);
      if (location.hash === "#/" || location.hash === "") route();
    } catch (err) { showError(err); }
  };

  const modal = openModal([
    modalHead("Einstellungen", () => modal),
    h("div", { class: "setting-block" },
      h("h3", {}, "Speicherort für die Ergebnisse"),
      h("div", { class: "path-row" }, outDir,
        h("button", {
          class: "btn", onclick: async () => {
            const chosen = await pickFolder(outDir.title);
            if (chosen) outDir.set(chosen);
          },
        }, icon("folder"), "Ändern"))),
    h("div", { class: "setting-block" },
      h("h3", {}, "Sicherheitsstufe"), modes.map((m) => m.el)),
    // Everything else is rarely changed and therefore folded away.
    h("details", { class: "advanced setting-block", id: "advanced" }, h("summary", {}, "Erweitert"),
      h("h3", {}, "Was soll entfernt werden?"),
      h("div", { class: "grid2" }, cats.map((x) => x.el)),
      h("h3", {}, "Datenschutz auf diesem Computer"),
      h("div", { class: "row" }, h("span", {}, "Arbeitskopien der Originale löschen"), keep),
      h("p", { class: "muted small", style: "margin-top:.4rem" }, "Zu jeder Akte speichert das Programm eine Arbeitskopie, damit Sie nachbessern können. Ihre Ergebnisse sind davon nicht betroffen."),
      h("label", { class: "check" }, learn, h("span", {}, "Aus meinen Entscheidungen lernen ", h("span", { class: "muted small" }, "(nur auf diesem Computer; gemerkt werden der Begriff und wenige Wörter Zusammenhang – auch nach dem Löschen der Akte, bis Sie „Gelerntes löschen“ wählen)"))),
      h("p", { class: "muted small" },
        `Bisher gelernt: ${plural(stats.entscheidungen, "Entscheidung", "Entscheidungen")}, ${plural(stats.immer_schwaerzen, "Begriff wird", "Begriffe werden")} künftig immer geschwärzt. `,
        h("button", {
          class: "btn link small", onclick: async () => {
            if (!await confirmDialog({ title: "Gelerntes löschen?", text: "Alle gespeicherten Entscheidungen und das daraus Gelernte (auch das angepasste Sprachmodell) werden gelöscht. Die Listen „Nie schwärzen“ und „Immer schwärzen“ bleiben – sie stehen unter „Erweitert“.", ok: "Löschen", danger: true })) return;
            try { await api("POST", "/api/learning/clear"); toast("Gelerntes gelöscht"); } catch (err) { showError(err); }
          },
        }, "Gelerntes löschen")),
      h("h3", {}, "Erkennung und Prüfung"),
      h("label", { class: "check" }, images, "Kleine Bilder auf Textseiten entfernen (Logos, Fotos) – empfohlen"),
      h("label", { class: "check" }, signatures, "Unterschriften, Handschrift und Stempel erkennen und schwärzen – empfohlen"),
      h("label", { class: "check" }, deep, "Prüfstufe 2: jede fertige Seite per Texterkennung kontrollieren – empfohlen"),
      h("label", { class: "check" }, forensic, "Prüfstufe 3: Wiederherstellungsversuche mit unabhängigen Werkzeugen – empfohlen"),
      h("label", { class: "check" }, mapping, "Vertrauliche Zuordnungstabelle (Original → Ersetzung) anlegen"),
      status.gliner ? h("label", { class: "check" }, gliner, "KI-Zusatzmodell GLiNER verwenden (gründlicher, langsamer)") : null,
      h("label", { class: "check" }, autoTrain, "Sprachmodell automatisch nachtrainieren, wenn genug neue Beispiele vorliegen"),
      h("p", { class: "muted small" },
        h("button", {
          class: "btn link small", title: "Passt das lokale Sprachmodell an Ihre Entscheidungen an (dauert einige Minuten)",
          onclick: async () => {
            try { await api("POST", "/api/learning/train"); toast("Training gestartet"); }
            catch (err) { showError(err); }
          },
        }, "Sprachmodell jetzt nachtrainieren"),
        stats.training && stats.training.message ? ` · ${stats.training.message}` : "",
        stats.custom_model ? " · Ein an Ihre Entscheidungen angepasstes Sprachmodell ist aktiv." : ""),
      status.managed && status.platform === "win32" ? [h("h3", {}, "Programmfenster"),
        h("label", { class: "check" }, appWindow, "In einem eigenen, abgeschotteten Fenster öffnen – empfohlen ",
          h("span", { class: "muted small" }, "(ohne Browser-Erweiterungen; sonst als Tab im Standardbrowser)"))] : null,
      h("div", { class: "grid2", style: "margin-top:.6rem" },
        h("div", {}, h("h3", {}, "Nie schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile, z. B. der Name Ihrer Kanzlei."), allow),
        h("div", {}, h("h3", {}, "Immer schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile – wird in jeder Akte gesucht."), deny))),
    h("div", { class: "modal-foot" },
      // Closing the window ends the program as well; this is the explicit way.
      status.managed ? h("button", {
        class: "btn ghost", id: "btn-quit-settings", title: "Ihre Akten und Ergebnisse bleiben gespeichert",
        onclick: () => { modal.close(); quitProgram(); },
      }, icon("power"), "Programm beenden") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn", onclick: () => modal.close() }, "Abbrechen"),
      h("button", { class: "btn primary", onclick: save }, "Speichern")),
  ]);
}

async function openHelp() {
  let keepDays = null;
  try { keepDays = (await api("GET", "/api/status")).keep_days; } catch (err) { /* the help works without it */ }
  const modal = openModal([
    modalHead("So funktioniert es", () => modal),
    h("ol", { class: "plain" },
      h("li", {}, h("strong", {}, "Ablegen: "), "PDF-Dateien oder einen ganzen Ordner ins Fenster ziehen – auch hunderte auf einmal, auch Scans."),
      h("li", {}, h("strong", {}, "Kurz prüfen: "), "Links stehen nur die Stellen, bei denen sich das Programm nicht sicher ist. " +
        "Fehlt etwas, rahmen Sie die Stelle auf der Seite mit der Maus ein."),
      h("li", {}, h("strong", {}, "Anonymisieren und speichern: "), "Die Daten werden unwiderruflich entfernt, jedes Ergebnis wird vierfach geprüft. " +
        "Mit „Ordner öffnen“ gelangen Sie zu den fertigen Dateien.")),
    h("dl", { class: "faq" },
      h("dt", {}, "Werden meine Dokumente ins Internet übertragen?"),
      h("dd", {}, "Nein. Alles wird ausschließlich auf diesem Computer verarbeitet. Das Programm braucht keine Internetverbindung."),
      h("dt", {}, "Wo finde ich die fertigen Dateien?"),
      h("dd", {}, "Im Speicherort, der auf der Startseite steht (anfangs der Ordner „Anonymisiert“ in Ihrem Benutzerordner). Für jede Akte gibt es einen Unterordner mit Datum. Er enthält nur geprüfte Ergebnisse und kann als Ganzes weitergegeben werden."),
      h("dt", {}, "Was speichert das Programm selbst?"),
      h("dd", {}, "Zu jeder Akte eine Arbeitskopie der Originale, damit Sie später nachbessern können. " +
        (keepDays > 0 ? `Sie wird ${keepDays} Tage nach der letzten Bearbeitung automatisch gelöscht`
          : keepDays === 0 ? "Sie bleibt, bis Sie die Akte löschen" : "Sie wird nach einer einstellbaren Frist automatisch gelöscht") +
        " (Einstellungen → Erweitert). Ihre Ergebnisse bleiben erhalten."),
      h("dt", {}, "Was ist die Zuordnungstabelle?"),
      h("dd", {}, "Sie zeigt, welcher Name zu welcher Abkürzung wurde. Sie liegt getrennt von den Ergebnissen im Ordner „VERTRAULICH_nicht_weitergeben“ (im Speicherort). Sie hebt die Anonymisierung auf – bitte niemals weitergeben oder hochladen."),
      h("dt", {}, "Was bedeutet „Nicht bestanden“?"),
      h("dd", {}, "Die automatische Prüfung hat im Ergebnis noch Originaldaten gefunden. Die Datei kommt dann nicht in den Ergebnisordner, sondern in den Ordner „VERTRAULICH_nicht_weitergeben“. Mit „Nachbessern“ ergänzen Sie die fehlenden Schwärzungen."),
      h("dt", {}, "Was passiert mit verstecktem Text und mit Fotos?"),
      h("dd", {}, "Ins Ergebnis kommt nur, was auf der Seite zu sehen ist. Text, der in der Datei steckt, aber nicht sichtbar ist (z. B. unter einem schwarzen Balken oder in weißer Schrift), wird nicht übernommen. Seiten mit Bildern werden neu aufgebaut – Kameradaten wie Aufnahmeort und Gerät sowie abgeschnittene Bildteile sind danach weg."),
      h("dt", {}, "Werden meine Originaldateien verändert?"),
      h("dd", {}, "Nein. Das Programm arbeitet mit einer Kopie und erzeugt neue Dateien."),
      h("dt", {}, "Wie beende ich das Programm?"),
      h("dd", {}, "Einfach das Fenster schließen – das Programm beendet sich kurz danach von selbst. Laufende Verarbeitungen werden vorher abgeschlossen. " +
        "(Ausdrücklich beenden: Einstellungen → „Programm beenden“.)")),
    h("div", { class: "modal-foot" }, h("button", { class: "btn primary", onclick: () => modal.close() }, "Verstanden")),
  ]);
}

// ------------------------------------------------------------ program state
// The installed program has no console window: the server ends itself when
// its window has been closed. This page sends signs of life, says goodbye
// when it is closed and reconnects after a restart.
const lifeline = { failures: 0, banner: null, ended: false, locked: false };

function showLocked() {
  if (lifeline.locked) return;
  lifeline.locked = true;
  openModal([
    h("h2", {}, "Bitte über das Programmsymbol neu öffnen"),
    h("p", {}, "Das Programm wurde inzwischen neu gestartet. Aus Sicherheitsgründen gilt dieses Fenster nicht mehr."),
    h("p", {}, h("strong", {}, "So geht es weiter: "), "Doppelklick auf „Dokumenten-Anonymisierer“ auf dem Desktop oder im Startmenü. Ihre Akten sind gespeichert."),
  ], { size: "narrow", dismissable: false });
}

async function heartbeat() {
  if (lifeline.ended || lifeline.locked) return;
  try {
    await api("POST", "/api/heartbeat");
    lifeline.failures = 0;
    if (lifeline.banner) location.reload();  // the program runs again
  } catch (err) {
    if (!err.offline) return;
    lifeline.failures += 1;
    if (lifeline.failures >= 2 && !lifeline.banner) {
      lifeline.banner = openModal([
        h("h2", {}, "Das Programm läuft nicht mehr"),
        h("p", {}, "Es wurde beendet – zum Beispiel automatisch nach einer längeren Pause. Ihre Akten sind gespeichert."),
        h("p", {}, h("strong", {}, "Zum Weiterarbeiten: "), "Doppelklick auf „Dokumenten-Anonymisierer“ auf dem Desktop oder im Startmenü."),
      ], { size: "narrow", dismissable: false });
    }
  }
}

async function quitProgram() {
  if (!await confirmDialog({ title: "Programm beenden?", text: "Ihre Akten und Ergebnisse bleiben gespeichert.", ok: "Beenden" })) return;
  const post = (body) => fetch("/api/shutdown", {
    method: "POST", headers: { "X-Anonymizer": "1", "Content-Type": "application/json" },
    credentials: "same-origin", body: JSON.stringify(body || {}),
  });
  try {
    let resp = await post();
    if (resp.status === 409) {
      if (!await confirmDialog({
        title: "Es läuft noch eine Verarbeitung",
        text: "Wenn Sie jetzt beenden, muss die laufende Verarbeitung später neu gestartet werden.",
        ok: "Trotzdem beenden", danger: true,
      })) return;
      resp = await post({ trotzdem: true });
    }
    if (!resp.ok) throw new Error(`Fehler ${resp.status}`);
    lifeline.ended = true;
    resetView();
    render(h("div", { class: "panel center" }, h("h1", {}, "Das Programm wurde beendet"),
      h("p", { class: "muted" }, "Sie können dieses Fenster jetzt schließen. Zum erneuten Start doppelt auf „Dokumenten-Anonymisierer“ klicken.")));
    window.close();
  } catch (err) { showError(err); }
}

setInterval(heartbeat, 20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) heartbeat(); });
window.addEventListener("pagehide", () => {
  if (lifeline.ended) return;
  // The window is being closed (or reloaded): the program ends itself
  // shortly afterwards unless another page gives a sign of life.
  try {
    fetch("/api/bye", { method: "POST", keepalive: true, credentials: "same-origin", headers: { "X-Anonymizer": "1", "Content-Type": "application/json" }, body: "{}" });
  } catch (e) { /* ignore */ }
});

document.getElementById("ico-lock").replaceWith(icon("lock"));
document.getElementById("btn-help").prepend(icon("help"));
document.getElementById("btn-settings").prepend(icon("settings"));
document.getElementById("btn-quit").prepend(icon("power"));
document.getElementById("btn-settings").addEventListener("click", () => openSettings().catch(showError));
document.getElementById("btn-help").addEventListener("click", () => openHelp().catch(showError));
/** The program's own window (no tabs, no address bar)? There, closing the window is "quit". */
const inOwnWindow = () => ["standalone", "minimal-ui", "window-controls-overlay"]
  .some((mode) => window.matchMedia(`(display-mode: ${mode})`).matches);
api("GET", "/api/status").then((s) => {
  // "Beenden" lives in the settings. Only as a tab of the normal browser
  // (fallback without the program window) it stays in the header as well.
  const quit = document.getElementById("btn-quit");
  if (s && s.managed && !inOwnWindow()) { quit.hidden = false; quit.addEventListener("click", quitProgram); }
  // On a shared computer everybody runs an own copy – show whose this is.
  const badge = document.querySelector(".badge-local");
  if (s && s.user && badge) badge.title += ` Dieses Programm läuft für das Benutzerkonto „${s.user}“.`;
}).catch(() => {});
route();
