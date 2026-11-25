/* Dokumenten-Anonymisierer – Oberfläche (ohne externe Bibliotheken).
 *
 * Ansichten (Hash-Routing):
 *   #/                 Startseite: Dokumente ablegen, letzte Akten
 *   #/akte/<id>        je nach Status: Fortschritt, Prüfen oder Ergebnis
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
    el.textContent = text.length > 64 ? `${text.slice(0, 20)} … ${text.slice(-40)}` : text;
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

function steps(active) {
  const names = ["Dokumente", "Prüfen", "Ergebnis"];
  return h("div", { class: "steps" }, names.map((n, i) =>
    h("div", { class: "step " + (i === active ? "active" : i < active ? "done" : "") },
      h("span", { class: "dot" }, i < active ? icon("check") : String(i + 1)), n)));
}

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
  render(steps(0), h("div", { class: "panel progress-card" },
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
    h("span", { class: "muted" }, "einzelne Dateien oder einen ganzen Ordner – auch hunderte Dokumente auf einmal"),
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
    neu: ["", "Leer"], analyse: ["work", "Wird gelesen …"], pruefung: ["warn", "Bitte prüfen"],
    schwaerzen: ["work", "Wird anonymisiert …"], fehler: ["bad", "Fehler"],
  };
  const [cls, label] = map[c.status] || ["", c.status];
  return h("span", { class: `chip ${cls}` }, label);
}

async function viewHome() {
  setTitle("");
  const [cases, status, stats] = await Promise.all([
    api("GET", "/api/cases"), api("GET", "/api/status"), api("GET", "/api/learning")]);
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

  const where = pathLabel(status.output_dir);
  const learned = stats.entscheidungen + stats.selbstkorrekturen;

  render(
    problems.length ? notice("bad", h("strong", {}, "Die Installation ist unvollständig. "), problems.join(" "),
      " Bitte das Setup des Programms noch einmal ausführen.") : null,
    h("div", { class: "panel hero" },
      h("h1", {}, "PDF-Dokumente anonymisieren"),
      h("p", { class: "lead" }, "Namen werden zu Initialen, Adressen, Kontaktdaten und Nummern zu Platzhaltern. " +
        "Jedes Ergebnis wird automatisch geprüft und als neues PDF gespeichert – nichts verlässt diesen Computer."),
      dropZone(),
      h("div", { class: "howto" },
        h("div", { class: "step-card" }, h("span", { class: "num" }, "1"),
          h("div", {}, h("strong", {}, "Ablegen"), h("span", { class: "muted small" }, "PDFs hineinziehen. Das Programm liest sie sofort, auch Scans."))),
        h("div", { class: "step-card" }, h("span", { class: "num" }, "2"),
          h("div", {}, h("strong", {}, "Kurz prüfen"), h("span", { class: "muted small" }, "Sie sehen, was ersetzt wird, und beantworten nur die offenen Fragen."))),
        h("div", { class: "step-card" }, h("span", { class: "num" }, "3"),
          h("div", {}, h("strong", {}, "Fertig gespeichert"), h("span", { class: "muted small" }, "Die anonymisierten PDFs liegen im Ergebnisordner – vierfach geprüft.")))),
      h("div", { class: "savebar" }, icon("folder"), "Ergebnisse werden gespeichert in", where,
        h("button", {
          class: "btn small", onclick: async () => {
            const chosen = await pickFolder(status.output_dir);
            if (chosen) { status.output_dir = chosen; where.set(chosen); }
          },
        }, "Ändern"),
        status.cloud ? h("span", { class: "chip warn", title: "Dieser Ordner wird vermutlich mit einem Cloud-Dienst synchronisiert." }, icon("alert"), "Cloud-Ordner") : null)),
    h("div", { class: "panel" },
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
      cases.length ? h("div", { class: "case-list", style: "margin-top:.6rem" }, rows)
        : h("p", { class: "muted", style: "margin:.8rem 0 0" }, "Noch keine Akten. Legen Sie oben Ihre ersten PDF-Dateien ab."),
      h("p", { class: "muted small", style: "margin:1rem 0 0" },
        status.keep_days > 0
          ? `Hinweis zum Datenschutz: Zu jeder Akte speichert das Programm eine Arbeitskopie der Originale. Sie wird ${status.keep_days} Tage nach der letzten Bearbeitung automatisch gelöscht. Ihre Ergebnisse bleiben erhalten.`
          : "Hinweis zum Datenschutz: Zu jeder Akte speichert das Programm eine Arbeitskopie der Originale, bis Sie die Akte löschen.")),
    h("p", { class: "muted small center" },
      learned ? `Das Programm lernt mit: ${plural(stats.entscheidungen, "Entscheidung", "Entscheidungen")} von Ihnen ausgewertet, ${plural(stats.immer_schwaerzen, "Begriff wird", "Begriffe werden")} künftig automatisch geschwärzt. · ` : "",
      `Version ${status.version}`),
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
  return viewHome();
}

function viewProgress(c) {
  const isAnalyse = c.status === "analyse";
  setTitle(isAnalyse ? "Dokumente werden gelesen" : "Dokumente werden anonymisiert");
  const bar = h("div", { class: "progress" }, h("div"));
  const stepLabel = h("p", { class: "progress-step" }, "Wird vorbereitet …");
  render(
    steps(isAnalyse ? 0 : 2),
    h("div", { class: "panel progress-card" },
      h("div", { class: "spinner" }),
      h("h1", {}, isAnalyse ? "Ihre Dokumente werden gelesen …" : "Ihre Dokumente werden anonymisiert …"),
      h("p", { class: "muted" }, isAnalyse
        ? "Der Text wird gelesen (bei Scans per Texterkennung) und nach persönlichen Daten durchsucht."
        : "Die Daten werden unwiderruflich entfernt, neue PDFs erzeugt und jedes Ergebnis in vier Stufen geprüft."),
      bar, stepLabel,
      h("p", { class: "muted small" }, "Das kann bei großen oder eingescannten Dokumenten einige Minuten dauern. " +
        "Die Verarbeitung läuft weiter, auch wenn Sie dieses Fenster schließen.")),
  );
  async function poll() {
    try {
      const cur = await api("GET", `/api/cases/${c.id}`);
      if (cur.status !== c.status) return route();
      const p = cur.progress || {};
      const pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
      bar.firstChild.style.width = `${Math.max(3, pct)}%`;
      stepLabel.textContent = `${p.total > 1 ? `${p.done} von ${p.total} · ` : ""}${p.step || "Wartet …"}`;
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
  render(h("div", { class: "panel" },
    h("h1", {}, "Das hat leider nicht geklappt"),
    notice("bad", c.error || "Unbekannter Fehler"),
    unreadable.length > 1 ? h("ul", {}, unreadable.slice(0, 20).map((d) => h("li", {}, h("strong", {}, d.name), ": ", d.error))) : null,
    h("p", { class: "muted" }, "Ihre Originaldateien wurden nicht verändert."),
    h("div", { class: "row" },
      h("button", {
        class: "btn primary", onclick: async () => {
          try { await api("POST", `/api/cases/${c.id}/analyse`); route(); } catch (err) { showError(err); }
        },
      }, icon("refresh"), "Noch einmal versuchen"),
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
  let [entities, settings, attention, status] = await Promise.all([
    api("GET", `/api/cases/${c.id}/entities`), api("GET", "/api/settings"),
    api("GET", `/api/cases/${c.id}/attention`), api("GET", "/api/status")]);
  const docs = c.documents.map((d, index) => ({ d, index })).filter((x) => !x.d.error);
  const unreadable = c.documents.filter((d) => d.error);
  const state = { doc: docs.length ? docs[0].index : 0, page: 0, selected: null, area: null, filter: "", tab: "", zoom: 1, scroll: false };
  const docLabel = (index) => (c.documents.length > 1 ? `${c.documents[index].name}, ` : "");

  const openQuestions = () => entities.filter((e) => e.uncertain && !e.decided);
  const openPages = () => attention.pages.filter((p) => !p.reviewed);
  state.tab = openQuestions().length + openPages().length ? "fragen" : "funde";

  // ---- left side: tabs ----
  const tabBar = h("div", { class: "tabs", role: "tablist" });
  const tabBody = h("div", { class: "tab-body" });
  function drawTabs() {
    const open = openQuestions().length + openPages().length;
    const tabs = [
      ["fragen", "Offene Fragen", open, open > 0],
      ["funde", "Alle Funde", entities.length, false],
    ];
    if ((attention.areas || []).length) tabs.push(["bereiche", "Bereiche", attention.areas.length, false]);
    fill(tabBar, ...tabs.map(([key, label, count, attn]) => h("button", {
      class: `tab ${state.tab === key ? "active" : ""}`, role: "tab", "data-tab": key,
      onclick: () => { state.tab = key; drawSide(); },
    }, label, h("span", { class: `badge ${attn ? "attn" : ""}` }, String(count)))));
  }
  function drawSide() {
    drawTabs();
    if (state.tab === "fragen") drawQuestions();
    else if (state.tab === "bereiche") drawAreas();
    else drawList();
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
      fill(tabBody, h("div", { class: "empty-state" }, icon("checkCircle"),
        h("strong", {}, "Keine offenen Fragen"),
        h("p", { class: "small" }, "Blättern Sie rechts kurz durch die Seiten. Wenn alles passt, klicken Sie unten auf „Anonymisieren und speichern“.")));
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
      h("div", { class: "q-meta" }, `Lesbarkeit ${p.quality} % – hier kann die Texterkennung Namen übersehen. Bitte die Seite ansehen und Fehlendes mit der Maus einrahmen.`),
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
    fill(tabBody, 
      h("div", { class: "tab-intro" }, "Hier ist sich das Programm nicht sicher. Der farbig markierte Knopf ist der Vorschlag. " +
        "Ihre Antworten merkt sich das Programm für künftige Akten."),
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

  // ---- "Alle Funde" ----
  const filter = h("input", { type: "search", placeholder: "In den Funden suchen …", "aria-label": "Funde durchsuchen" });
  filter.addEventListener("input", () => { state.filter = filter.value.toLowerCase(); drawListRows(); });
  const listRows = h("div");
  function drawList() {
    filter.value = state.filter;
    fill(tabBody, 
      h("div", { class: "tab-intro" }, "Schalter an = wird ersetzt. Den Ersatztext können Sie direkt ändern."),
      filter, listRows);
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
        e.enabled = cb.checked; e.decided = true; drawTabs(); drawListRows(); drawSummary(); drawPage();
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
    return h("div", { class: `entity ${e.enabled ? "" : "off"} ${state.selected === e.id ? "selected" : ""}`, "data-id": e.id },
      h("label", { class: "switch", title: "An = wird ersetzt" }, cb, h("span")),
      h("span", { class: "orig", title: e.variants.join(" | ") }, e.original,
        e.uncertain && !e.decided ? h("span", { class: "count attn", title: e.uncertain, style: "margin-left:.4rem" }, "offen") : null),
      h("span", { class: "count", title: "Anzahl der Fundstellen" }, `${e.count}×`),
      h("div", { class: "repl" }, icon("arrowRight"), repl,
        h("button", { class: "btn link small", title: "Erste Fundstelle im Dokument zeigen", onclick: () => showAt(e.first.doc, e.first.page, e.id) }, "zeigen"),
        h("button", {
          class: "btn link small", title: "Diesen Begriff nie schwärzen – auch nicht in künftigen Akten",
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
        }, "nie")));
  }

  // ---- "Bereiche": signatures, handwriting, stamps, images ----
  async function removeArea(index) {
    try {
      await api("DELETE", `/api/cases/${c.id}/manual/${index}`);
      attention = await api("GET", `/api/cases/${c.id}/attention`);  // indices shift
      if (state.tab === "bereiche" && !(attention.areas || []).length) state.tab = "funde";
      state.area = null;
      drawSide(); drawPage();
    } catch (err) { showError(err); }
  }
  function drawAreas() {
    const areas = attention.areas || [];
    fill(tabBody, 
      h("div", { class: "tab-intro" }, "Unterschriften, Handschrift, Stempel und kleine Bilder (z. B. Logos) wurden automatisch erkannt und werden vollständig geschwärzt. " +
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
      ? `Scan – per Texterkennung gelesen${pageData.quality !== null ? ` (Lesbarkeit ${pageData.quality} %)` : ""}`
      : pageData.kind === "mixed" ? "Text mit Bildern – Bilder per Texterkennung gelesen" : "";
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
          state.selected = b.entity; state.area = null;
          if (state.tab !== "funde" && !(ent && ent.uncertain && !ent.decided)) state.tab = "funde";
          state.filter = "";
          drawSide(); drawPage();
          const row = tabBody.querySelector(`[data-id="${b.entity}"]`) || tabBody.querySelector(".q-card.selected");
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

  // ---- bottom bar: summary, where it is saved, the one big button ----
  const summary = h("span", {});
  function drawSummary() {
    const on = entities.filter((e) => e.enabled);
    const places = on.reduce((s, e) => s + e.count, 0);
    const open = openQuestions().length + openPages().length;
    fill(summary, h("strong", {}, `${plural(places, "Stelle wird", "Stellen werden")} ersetzt`),
      open ? h("span", { class: "chip warn", style: "margin-left:.6rem" }, plural(open, "offene Frage", "offene Fragen")) : null);
  }
  const where = pathLabel(status.output_dir);
  const redactBtn = h("button", {
    class: "btn primary big", id: "btn-redact",
    onclick: async () => {
      const open = openQuestions().length + openPages().length;
      if (open && !await confirmDialog({
        title: `Noch ${plural(open, "offene Frage", "offene Fragen")}`,
        text: ["Sie können trotzdem fortfahren: Offene Vorschläge werden so übernommen wie angezeigt – im Zweifel wird geschwärzt.",
          "Schlecht lesbare Seiten sollten Sie vorher selbst ansehen."],
        ok: "Trotzdem anonymisieren", cancel: "Zurück zu den Fragen",
      })) { state.tab = "fragen"; drawSide(); return; }
      redactBtn.disabled = true;
      try { await api("POST", `/api/cases/${c.id}/redact`); route(); }
      catch (err) { showError(err); redactBtn.disabled = false; }
    },
  }, "Anonymisieren und speichern", icon("arrowRight"));
  setActionbar(h("div", { class: "actionbar" }, h("div", { class: "inner" },
    summary, h("span", { class: "spacer" }),
    h("span", { class: "where" }, "Speichern in ", where, " ",
      h("button", {
        class: "btn link small", onclick: async () => {
          const chosen = await pickFolder(status.output_dir);
          if (chosen) { status.output_dir = chosen; where.set(chosen); }
        },
      }, "ändern")),
    redactBtn)));

  const hints = [...(c.warnings || []), ...c.documents.flatMap((d) => d.notes || []).filter((n) => !n.includes("schlecht lesbar"))];
  render(
    h("div", { class: "review-head" },
      h("div", {}, steps(1), h("h1", {}, c.title)),
      h("span", { class: "spacer" }),
      h("span", { class: "muted" }, `${plural(docs.length, "Dokument", "Dokumente")} · ${plural(docs.reduce((s, x) => s + x.d.pages, 0), "Seite", "Seiten")}`)),
    unreadable.length ? notice("warn", h("strong", {}, `${plural(unreadable.length, "Dokument konnte", "Dokumente konnten")} nicht gelesen werden und ${unreadable.length === 1 ? "wird" : "werden"} übersprungen: `),
      h("ul", { style: "margin:.3rem 0 0;padding-left:1.2rem" }, unreadable.slice(0, 10).map((d) => h("li", {}, h("strong", {}, d.name), " – ", d.error))),
      unreadable.length > 10 ? `… und ${unreadable.length - 10} weitere` : null) : null,
    hints.length ? h("details", { class: "hintbox" }, h("summary", {}, plural(hints.length, "Hinweis", "Hinweise") + " zur Verarbeitung"),
      h("ul", { style: "margin:.4rem 0 0;padding-left:1.2rem" }, hints.slice(0, 30).map((n) => h("li", {}, n)))) : null,
    h("div", { class: "review" },
      h("div", { class: "panel side" }, tabBar, tabBody, addTerm),
      h("div", { class: "panel viewer" },
        h("div", { class: "viewer-bar" },
          docs.length > 1 ? [
            h("button", { class: "btn icon-only", title: "Vorheriges Dokument", onclick: () => goDoc(-1) }, icon("arrowLeft")),
            docSelect,
            h("button", { class: "btn icon-only", title: "Nächstes Dokument", onclick: () => goDoc(1) }, icon("arrowRight"))] : null,
          h("button", { class: "btn icon-only", onclick: () => go(-1), title: "Vorherige Seite (Pfeiltaste links)" }, icon("chevronLeft")),
          h("span", {}, "Seite"), pageInput, pageCount,
          h("button", { class: "btn icon-only", onclick: () => go(1), title: "Nächste Seite (Pfeiltaste rechts)" }, icon("chevronRight")),
          kindInfo,
          h("span", { class: "spacer" }),
          h("button", { class: "btn icon-only", title: "Kleiner", onclick: () => { state.zoom = Math.max(0.6, state.zoom - 0.25); applyZoom(); drawPage(); } }, icon("minus")),
          h("button", { class: "btn icon-only", title: "Größer", onclick: () => { state.zoom = Math.min(2.5, state.zoom + 0.25); applyZoom(); drawPage(); } }, icon("plus"))),
        h("div", { class: "legend" },
          h("span", { class: "c-PERSON" }, "Person"), h("span", { class: "c-ORG" }, "Firma"),
          h("span", { class: "c-ADDRESS" }, "Adresse / Ort"), h("span", { class: "c-BIRTH_DATE" }, "Geburtsdatum"),
          h("span", { class: "c-other" }, "Nummern, Kontakt")),
        scroller,
        h("p", { class: "muted small", style: "margin:.7rem 0 0" }, icon("edit"),
          " Fehlt etwas, z. B. eine Unterschrift oder ein Stempel? Mit gedrückter Maustaste einen Rahmen um die Stelle ziehen – sie wird geschwärzt. Gestrichelte Rahmen bleiben lesbar."))),
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

function viewResult(c) {
  setTitle("Ergebnis");
  const docs = c.documents.map((d, i) => ({ d, i, v: d.verification || {}, state: docState(d) }));
  const ok = docs.filter((x) => x.state === "ok");
  const failed = docs.filter((x) => x.state === "failed");
  const errors = docs.filter((x) => x.state === "error");
  const hints = ok.filter((x) => (x.v.suspects || []).length);
  const order = { failed: 0, error: 1, ok: 2 };
  docs.sort((a, b) => order[a.state] - order[b.state] || ((b.v.suspects || []).length - (a.v.suspects || []).length) || a.i - b.i);

  const openFile = async (i, kind) => {
    try { await api("POST", `/api/cases/${c.id}/docs/${i}/open/${kind}`); }
    catch (err) { showError(err); }
  };
  const rows = docs.map(({ d, i, v, state }) => h("div", { class: "doc-row" },
    state === "ok" ? h("span", { class: "chip ok" }, icon("check"), "Geprüft")
      : state === "failed" ? h("span", { class: "chip bad" }, icon("alert"), "Nicht bestanden")
        : h("span", { class: "chip bad" }, icon("x"), "Nicht verarbeitet"),
    h("div", {},
      h("div", { class: "name" }, state === "error" ? d.name : baseName(d.output)),
      h("div", { class: "sub" }, state === "error" ? d.error
        : state === "failed" ? "Bitte nicht weitergeben. Die Datei liegt im Unterordner „NICHT BESTANDEN“."
          : `${plural(d.pages, "Seite", "Seiten")}${(v.suspects || []).length ? ` · ${plural(v.suspects.length, "Hinweis", "Hinweise")} der Nachkontrolle` : ""}`)),
    state === "error" ? h("span", {}) : h("div", { class: "actions" },
      state === "ok" ? h("button", { class: "btn small", onclick: () => openFile(i, "pdf"), title: "Anonymisiertes PDF öffnen" }, icon("external"), "Öffnen") : null,
      h("button", { class: `btn small ${state === "failed" || (v.suspects || []).length ? "primary" : ""}`, onclick: () => openDocDetails(c, d, i) }, icon("eye"), "Ansehen und Prüfdetails"))));

  let heroClass = "", heroIcon = "check", headline, sub;
  if (failed.length) {
    heroClass = "bad"; heroIcon = "alert";
    headline = `${plural(failed.length, "Dokument hat", "Dokumente haben")} die Prüfung nicht bestanden`;
    sub = "Diese Dokumente bitte nicht weitergeben. Unter „Ansehen und Prüfdetails“ steht, was gefunden wurde – mit „Nachbessern“ können Sie Schwärzungen ergänzen.";
  } else if (errors.length) {
    heroClass = "warn"; heroIcon = "alert";
    headline = `${plural(ok.length, "Dokument", "Dokumente")} fertig, ${errors.length} nicht verarbeitet`;
    sub = "Die verarbeiteten Dokumente haben alle Prüfstufen bestanden. Die Gründe für die übrigen stehen unten.";
  } else {
    headline = ok.length === 1 ? "Fertig – Ihr Dokument ist anonymisiert" : `Fertig – ${ok.length} Dokumente sind anonymisiert`;
    sub = "Alle Prüfstufen bestanden: Die entfernten Daten sind im Ergebnis weder sichtbar noch wiederherstellbar.";
  }

  render(
    steps(3),
    h("div", { class: `panel result-hero ${heroClass}` },
      h("div", { class: "big-icon" }, icon(heroIcon)),
      h("h1", {}, headline),
      h("p", { class: "muted", style: "max-width:680px;margin:0 auto" }, sub),
      h("div", { class: "where" }, h("span", { class: "muted" }, "Gespeichert in "), pathLabel(c.output_dir)),
      h("div", { class: "row" },
        h("button", {
          class: "btn primary big", id: "btn-open-folder", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/open-folder`); } catch (err) { showError(err); }
          },
        }, icon("folder"), "Ordner öffnen"),
        ok.length ? h("a", { class: "btn big", href: `/api/cases/${c.id}/zip`, title: "Alle geprüften Ergebnisse in einer ZIP-Datei – ohne die vertrauliche Zuordnungstabelle" }, icon("archive"), "Alle als ZIP") : null,
        h("a", { class: "btn big", href: "#/" }, icon("plus"), "Weitere Dokumente"))),
    hints.length && !failed.length ? notice("info", `${plural(hints.length, "Dokument enthält", "Dokumente enthalten")} Hinweise der Nachkontrolle – meist Fehlalarme wie Fachbegriffe. Ein kurzer Blick lohnt sich.`) : null,
    (c.warnings || []).length ? h("details", { class: "hintbox" }, h("summary", {}, plural(c.warnings.length, "Hinweis", "Hinweise") + " zur Verarbeitung"),
      h("ul", { style: "margin:.4rem 0 0;padding-left:1.2rem" }, c.warnings.slice(0, 30).map((w) => h("li", {}, w)))) : null,
    h("div", { class: "panel" },
      h("h2", {}, "Dokumente"),
      rows,
      h("div", { class: "row", style: "margin-top:1.1rem;padding-top:1rem;border-top:1px solid var(--line)" },
        h("button", {
          class: "btn small", title: "Schwärzungen ergänzen oder ändern und neu anonymisieren", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/review`); route(); } catch (err) { showError(err); }
          },
        }, icon("edit"), "Nachbessern"),
        c.mapping ? h("a", { class: "btn small", href: `/api/cases/${c.id}/mapping`, target: "_blank", rel: "noopener", title: "Zeigt, welcher Name zu welcher Abkürzung wurde. Vertraulich – niemals weitergeben!" }, icon("lock"), "Zuordnungstabelle (vertraulich)") : null)),
    h("details", { class: "panel" },
      h("summary", { style: "cursor:pointer;font-weight:650" }, "Was genau wurde geprüft?"),
      h("ol", { class: "plain", style: "margin-top:.8rem" },
        h("li", {}, h("strong", {}, "Textprüfung: "), "Jeder gefundene Name und jede Nummer wird im gesamten Text der neuen Datei gesucht – auch in unsichtbarem Text und im Dateinamen."),
        h("li", {}, h("strong", {}, "Bildprüfung: "), "Jede fertige Seite wird wie von einem Menschen „gelesen“ (Texterkennung) und erneut durchsucht."),
        h("li", {}, h("strong", {}, "Wiederherstellungsversuche: "), "Das Programm versucht selbst, die entfernten Daten mit mehreren unabhängigen Werkzeugen aus der Datei zurückzuholen."),
        h("li", {}, h("strong", {}, "Nachkontrolle: "), "Die Erkennung läuft noch einmal über das Ergebnis und meldet alles, was noch nach persönlichen Daten aussieht.")),
      h("p", { class: "muted small" }, "Selbst nachprüfen: Anonymisiertes PDF öffnen, Strg+F drücken und einen der Namen suchen – er darf nicht gefunden werden.")),
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
      ? notice("ok", h("strong", {}, "Prüfung bestanden. "),
        `Keine der ${v.terms_checked} bekannten Originalangaben ist im Ergebnis enthalten oder wiederherstellbar (${plural(v.pages_checked, "Seite", "Seiten")} geprüft).`)
      : notice("bad", h("strong", {}, "Prüfung nicht bestanden – bitte NICHT weitergeben. "),
        "Gehen Sie über „Nachbessern“ zurück und ergänzen Sie die Schwärzungen, z. B. indem Sie die Stelle mit der Maus einrahmen."),
    h("div", { class: "row" },
      passed ? h("a", { class: "btn primary", href: `${base}/download/pdf` }, icon("download"), "PDF speichern unter …") : null,
      passed ? h("a", { class: "btn", href: `${base}/download/txt`, title: "Reiner Text in Lesereihenfolge – z. B. zum Einfügen in ein KI-Werkzeug" }, icon("text"), "Textfassung") : null,
      h("a", { class: "btn", href: `${base}/download/report?inline=1`, target: "_blank", rel: "noopener" }, icon("shield"), "Prüfbericht")),
    leaks.length ? [h("h3", {}, "Noch gefundene Originaldaten"), h("ul", {}, leaks.slice(0, 50))] : null,
    suspects.length ? [
      h("h3", {}, "Nachkontrolle: bitte kurz entscheiden"),
      h("p", { class: "muted small" }, "Diese Stellen könnten noch persönliche Daten sein. Oft sind es Fehlalarme (z. B. Fachbegriffe). " +
        "Ihre Entscheidung merkt sich das Programm."),
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
        h("li", { class: ch.ok ? "ok" : "bad" }, ch.name, ch.detail ? h("span", { class: "muted small" }, ` – ${ch.detail}`) : null)))));
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
    ["standard", "Standard (empfohlen)", "Text bleibt als Text erhalten: kleine Dateien, gut durchsuchbar. Eingescannte Seiten werden immer als Bild neu aufgebaut."],
    ["maximum", "Maximale Sicherheit", "Jede Seite wird als Bild neu aufgebaut. Größere Dateien; der Text wird per Texterkennung neu hinterlegt."],
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
      h("h3", {}, "Was soll entfernt werden?"),
      h("div", { class: "grid2" }, cats.map((x) => x.el))),
    h("div", { class: "setting-block" },
      h("h3", {}, "Sicherheitsstufe"), modes.map((m) => m.el)),
    h("div", { class: "setting-block" },
      h("h3", {}, "Datenschutz auf diesem Computer"),
      h("div", { class: "row" }, h("span", {}, "Arbeitskopien der Originale löschen"), keep),
      h("p", { class: "muted small", style: "margin-top:.4rem" }, "Das Programm speichert zu jeder Akte eine Arbeitskopie, damit Sie später nachbessern können. Ihre Ergebnisse im Speicherort sind davon nicht betroffen."),
      h("label", { class: "check" }, learn, h("span", {}, "Aus meinen Entscheidungen lernen ", h("span", { class: "muted small" }, "(wird nur auf diesem Computer gespeichert)"))),
      h("p", { class: "muted small" },
        `Bisher gelernt: ${plural(stats.entscheidungen, "Entscheidung", "Entscheidungen")}, ${plural(stats.immer_schwaerzen, "Begriff wird", "Begriffe werden")} künftig immer geschwärzt. `,
        h("button", {
          class: "btn link small", onclick: async () => {
            if (!await confirmDialog({ title: "Gelerntes löschen?", text: "Alle gespeicherten Entscheidungen und das daraus Gelernte werden gelöscht.", ok: "Löschen", danger: true })) return;
            try { await api("POST", "/api/learning/clear"); toast("Gelerntes gelöscht"); } catch (err) { showError(err); }
          },
        }, "Gelerntes löschen"))),
    h("details", { class: "advanced setting-block" }, h("summary", {}, "Erweiterte Einstellungen"),
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
        h("label", { class: "check" }, appWindow, "In einem eigenen Fenster öffnen (statt als Tab im Browser)")] : null,
      h("div", { class: "grid2", style: "margin-top:.6rem" },
        h("div", {}, h("h3", {}, "Nie schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile, z. B. der Name Ihrer Kanzlei."), allow),
        h("div", {}, h("h3", {}, "Immer schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile – wird in jeder Akte gesucht."), deny))),
    h("div", { class: "modal-foot" },
      h("button", { class: "btn", onclick: () => modal.close() }, "Abbrechen"),
      h("button", { class: "btn primary", onclick: save }, "Speichern")),
  ]);
}

function openHelp() {
  const modal = openModal([
    modalHead("So funktioniert es", () => modal),
    h("ol", { class: "plain" },
      h("li", {}, h("strong", {}, "Ablegen: "), "PDF-Dateien oder einen ganzen Ordner auf die Startseite ziehen. Das Lesen beginnt sofort."),
      h("li", {}, h("strong", {}, "Prüfen: "), "Unter „Offene Fragen“ stehen nur die Stellen, bei denen sich das Programm nicht sicher ist. " +
        "Antworten Sie mit „Schwärzen“ oder „Lesbar lassen“. Fehlt etwas, rahmen Sie die Stelle auf der Seite mit der Maus ein."),
      h("li", {}, h("strong", {}, "Anonymisieren und speichern: "), "Ein Klick. Die Daten werden unwiderruflich entfernt und jedes Ergebnis wird in vier Stufen geprüft."),
      h("li", {}, h("strong", {}, "Ergebnis: "), "Mit „Ordner öffnen“ gelangen Sie zu den fertigen Dateien. Grün bedeutet: geprüft, kann weitergegeben werden.")),
    h("dl", { class: "faq" },
      h("dt", {}, "Werden meine Dokumente ins Internet übertragen?"),
      h("dd", {}, "Nein. Alles wird ausschließlich auf diesem Computer verarbeitet. Das Programm braucht keine Internetverbindung."),
      h("dt", {}, "Wo finde ich die fertigen Dateien?"),
      h("dd", {}, "Im Speicherort, der auf der Startseite steht (anfangs der Ordner „Anonymisiert“ in Ihrem Benutzerordner). Für jede Akte gibt es einen Unterordner mit Datum."),
      h("dt", {}, "Was ist die Zuordnungstabelle?"),
      h("dd", {}, "Sie zeigt, welcher Name zu welcher Abkürzung wurde, und liegt im Unterordner „VERTRAULICH_nicht_weitergeben“. Sie hebt die Anonymisierung auf – bitte niemals weitergeben oder hochladen."),
      h("dt", {}, "Was bedeutet „Nicht bestanden“?"),
      h("dd", {}, "Die automatische Prüfung hat im Ergebnis noch Originaldaten gefunden. Die Datei liegt dann in einem eigenen Unterordner und sollte nicht weitergegeben werden. Mit „Nachbessern“ ergänzen Sie die fehlenden Schwärzungen."),
      h("dt", {}, "Werden meine Originaldateien verändert?"),
      h("dd", {}, "Nein. Das Programm arbeitet mit einer Kopie und erzeugt neue Dateien."),
      h("dt", {}, "Wie beende ich das Programm?"),
      h("dd", {}, "Einfach das Fenster schließen – das Programm beendet sich kurz danach von selbst. Laufende Verarbeitungen werden vorher abgeschlossen.")),
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
document.getElementById("btn-help").addEventListener("click", openHelp);
api("GET", "/api/status").then((s) => {
  const quit = document.getElementById("btn-quit");
  if (s && s.managed) { quit.hidden = false; quit.addEventListener("click", quitProgram); }
}).catch(() => {});
route();
