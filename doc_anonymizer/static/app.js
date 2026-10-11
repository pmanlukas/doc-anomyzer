/* Dokumenten-Anonymisierer – Oberfläche (ohne externe Bibliotheken).
 *
 * Ansichten (Hash-Routing):
 *   #/                 Übersicht aller Akten
 *   #/neu              neue Akte anlegen und PDFs hinzufügen
 *   #/akte/<id>        je nach Status: Fortschritt, Prüfung oder Ergebnis
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
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** JSON API call; mutating requests carry the anti-CSRF header. */
async function api(method, url, body) {
  const opts = { method, headers: { "X-Anonymizer": "1" } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* no JSON body */ }
  if (!resp.ok) throw new Error((data && data.error) || `Fehler ${resp.status}`);
  return data;
}

function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

function showError(err) {
  console.error(err);
  toast("⚠ " + (err.message || err));
}

const app = () => document.getElementById("app");
function render(...nodes) {
  app().replaceChildren(...nodes.flat().filter((n) => n !== null && n !== undefined && n !== false));
  window.scrollTo(0, 0);
}

const STATUS = {
  neu: "Dokumente hinzufügen",
  analyse: "Wird analysiert …",
  pruefung: "Bitte prüfen",
  schwaerzen: "Wird anonymisiert …",
  fertig: "Fertig",
  fehler: "Fehler",
};
const CATEGORY_CLASS = (c) =>
  ["PERSON", "ORG", "LOCATION", "ADDRESS", "BIRTH_DATE"].includes(c) ? `c-${c}` : "c-other";

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }); }
  catch (e) { return iso; }
}

function steps(active) {
  const names = ["1. Dokumente", "2. Prüfen", "3. Ergebnis"];
  return h("div", { class: "steps" }, names.map((n, i) =>
    h("span", { class: "step " + (i === active ? "active" : i < active ? "done" : "") }, n)));
}

let pollTimer = null;
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

// ------------------------------------------------------------------- router

async function route() {
  stopPolling();
  const hash = location.hash || "#/";
  try {
    if (hash === "#/" || hash === "#") return await viewHome();
    if (hash === "#/neu") return viewNew();
    const m = hash.match(/^#\/akte\/([0-9a-f]{32})$/);
    if (m) return await viewCase(m[1]);
    location.hash = "#/";
  } catch (err) {
    showError(err);
  }
}
window.addEventListener("hashchange", route);

// --------------------------------------------------------------------- home

/** Collect PDF files from a drop (files and whole folders, recursively). */
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
async function startCase(fileList, title) {
  const files = [...fileList].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  const skipped = fileList.length - files.length;
  if (!files.length) { toast("Keine PDF-Dateien gefunden"); return; }
  const bar = h("div", { class: "progress" }, h("div"));
  const label = h("p", { class: "muted" }, "");
  render(steps(0), h("div", { class: "panel" },
    h("h1", {}, "Dokumente werden übernommen …"), bar, label,
    skipped ? h("p", { class: "muted small" }, `${skipped} Datei(en) ohne PDF-Endung wurden übersprungen.`) : null));
  try {
    const name = title || (files.length === 1 ? files[0].name.replace(/\.pdf$/i, "")
      : `${files.length} Dokumente vom ${new Date().toLocaleDateString("de-DE")}`);
    const c = await api("POST", "/api/cases", { title: name });
    const size = 20;  // files per request
    for (let i = 0; i < files.length; i += size) {
      const form = new FormData();
      files.slice(i, i + size).forEach((f) => form.append("files", f, f.webkitRelativePath || f.name));
      await api("POST", `/api/cases/${c.id}/documents`, form);
      const done = Math.min(i + size, files.length);
      bar.firstChild.style.width = `${Math.round((100 * done) / files.length)}%`;
      label.textContent = `${done} von ${files.length} Dateien übernommen`;
    }
    await api("POST", `/api/cases/${c.id}/analyse`);
    location.hash = `#/akte/${c.id}`;
  } catch (err) {
    showError(err);
    location.hash = "#/";
  }
}

function dropZone() {
  const files = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, style: "display:none" });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true, style: "display:none" });
  files.addEventListener("change", () => startCase(files.files));
  folder.addEventListener("change", () => startCase(folder.files));
  const zone = h("div", {
    class: "dropzone big",
    ondragover: (e) => { e.preventDefault(); zone.classList.add("over"); },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: async (e) => {
      e.preventDefault(); zone.classList.remove("over");
      startCase(await filesFromDrop(e.dataTransfer));
    },
  },
    h("div", { class: "drop-icon", "aria-hidden": "true" }, "⇩"),
    h("strong", {}, "PDF-Dateien oder einen ganzen Ordner hierher ziehen"),
    h("p", { class: "muted" }, "Die Analyse startet sofort. Alle Dateien einer Ablage werden als eine Akte behandelt – " +
      "dieselbe Person erhält in allen Dokumenten dieselben Initialen."),
    h("div", { class: "row", style: "justify-content:center" },
      h("button", { class: "btn primary big", onclick: () => files.click() }, "Dateien auswählen"),
      h("button", { class: "btn big", onclick: () => folder.click() }, "Ordner auswählen")),
    files, folder);
  return zone;
}

async function viewHome() {
  const [cases, status, stats] = await Promise.all([
    api("GET", "/api/cases"), api("GET", "/api/status"), api("GET", "/api/learning")]);
  const problems = [];
  if (!status.spacy) problems.push("Das deutsche Sprachmodell fehlt. Bitte „Installieren.bat“ ausführen.");
  if (!status.tesseract) problems.push("Die Texterkennung (Tesseract) fehlt – eingescannte PDFs können nicht verarbeitet werden.");
  else if (!status.tesseract_languages.includes("deu")) problems.push("Für die Texterkennung fehlt das deutsche Sprachpaket.");

  const rows = cases.map((c) => {
    const statusLabel = c.status === "fertig" && c.passed === false ? "Prüfung fehlgeschlagen" : STATUS[c.status];
    return h("tr", {},
      h("td", {}, h("a", { href: `#/akte/${c.id}` }, c.title)),
      h("td", { class: "muted" }, fmtDate(c.created)),
      h("td", {}, `${c.documents} PDF · ${c.pages || "–"} Seiten`),
      h("td", {}, h("span", { class: `status ${c.passed === false ? "failed" : c.status}` }, statusLabel)),
      h("td", { style: "text-align:right" },
        h("a", { class: "btn", href: `#/akte/${c.id}` }, "Öffnen"), " ",
        h("button", {
          class: "btn danger", title: "Akte inkl. aller Arbeitsdaten löschen (die fertigen Dateien im Ergebnisordner bleiben erhalten)",
          onclick: async () => {
            if (!confirm(`Akte „${c.title}“ und alle zugehörigen Arbeitsdaten löschen?\n\nDie bereits erzeugten anonymisierten Dateien im Ergebnisordner bleiben erhalten.`)) return;
            try { await api("DELETE", `/api/cases/${c.id}`); toast("Akte gelöscht"); route(); }
            catch (err) { showError(err); }
          },
        }, "Löschen")));
  });
  const learned = stats.entscheidungen + stats.selbstkorrekturen;

  render(
    problems.length ? h("div", { class: "notice bad" }, h("strong", {}, "Installation unvollständig: "),
      problems.join(" ")) : null,
    h("div", { class: "panel" },
      h("h1", {}, "PDF-Dokumente anonymisieren"),
      h("p", { class: "muted" },
        "Namen werden durch Initialen ersetzt (z. B. „M. M.“), alle übrigen persönlichen Daten durch Platzhalter " +
        "(z. B. „[Adresse 1]“). Die Daten werden unwiderruflich entfernt und das Ergebnis viermal automatisch geprüft. " +
        "Alles geschieht auf diesem Computer – nichts wird ins Internet übertragen."),
      dropZone()),
    learned ? h("div", { class: "panel learn" },
      h("strong", {}, "🎓 Das Programm lernt mit: "),
      `${stats.entscheidungen} Ihrer Entscheidungen und ${stats.selbstkorrekturen} Selbstkorrekturen ausgewertet, ` +
      `${stats.immer_schwaerzen} Begriffe werden künftig automatisch geschwärzt, ${stats.gelernte_vornamen} neue Vornamen gelernt.` +
      (stats.letztes_training ? ` Letztes Training: ${stats.letztes_training} – ${stats.trainingsergebnis}` : "")) : null,
    h("div", { class: "panel" },
      h("h2", { style: "margin-top:0" }, "Bisherige Akten"),
      cases.length ? h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Bezeichnung"), h("th", {}, "Angelegt"), h("th", {}, "Umfang"),
          h("th", {}, "Status"), h("th", {})),
        rows) : h("p", { class: "muted" }, "Noch keine Akten vorhanden.")),
  );
}

function viewNew() { return viewHome(); }

// ------------------------------------------------------------------ case view

async function viewCase(id) {
  const c = await api("GET", `/api/cases/${id}`);
  if (c.status === "analyse" || c.status === "schwaerzen") return viewProgress(c);
  if (c.status === "pruefung") return viewReview(c);
  if (c.status === "fertig") return viewResult(c);
  if (c.status === "fehler") return viewError(c);
  return viewNew();
}

function viewProgress(c) {
  const bar = h("div", { class: "progress" }, h("div"));
  const label = h("p", { class: "muted" }, "");
  const isAnalyse = c.status === "analyse";
  render(
    steps(isAnalyse ? 0 : 2),
    h("div", { class: "panel" },
      h("h1", {}, isAnalyse ? "Dokumente werden analysiert …" : "Dokumente werden anonymisiert …"),
      h("p", {}, isAnalyse
        ? "Der Text wird gelesen (bei Scans per Texterkennung) und nach persönlichen Daten durchsucht."
        : "Die Daten werden entfernt, die neuen PDFs erzeugt und in vier Stufen automatisch geprüft."),
      bar, label,
      h("p", { class: "muted small" }, "Sie können dieses Browserfenster schließen und später wieder öffnen – " +
        "die Verarbeitung läuft weiter. Bitte nur das schwarze Programmfenster geöffnet lassen.")),
  );
  async function poll() {
    try {
      const cur = await api("GET", `/api/cases/${c.id}`);
      if (cur.status !== c.status) return route();
      const p = cur.progress || {};
      const pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
      bar.firstChild.style.width = `${pct}%`;
      label.textContent = `${p.total > 1 ? `${p.done} von ${p.total} – ` : ""}${p.step || "Wartet …"}`;
    } catch (err) { label.textContent = "Verbindung unterbrochen – läuft das Programm noch?"; }
    pollTimer = setTimeout(poll, 1000);
  }
  poll();
}

function viewError(c) {
  render(h("div", { class: "panel" },
    h("h1", {}, "Es ist ein Fehler aufgetreten"),
    h("div", { class: "notice bad" }, c.error || "Unbekannter Fehler"),
    h("div", { class: "row" },
      h("a", { class: "btn", href: "#/" }, "Zur Übersicht"),
      h("button", {
        class: "btn primary", onclick: async () => {
          try { await api("POST", `/api/cases/${c.id}/analyse`); route(); } catch (err) { showError(err); }
        },
      }, "Analyse erneut starten"))));
}

// ---------------------------------------------------------------- review

async function viewReview(c) {
  let entities = await api("GET", `/api/cases/${c.id}/entities`);
  const settings = await api("GET", "/api/settings");
  let attention = await api("GET", `/api/cases/${c.id}/attention`);
  const state = { doc: 0, page: 0, selected: null, filter: "" };

  // ---- "Bitte entscheiden": uncertain findings and poorly readable pages ----
  const decideBox = h("div");
  function showAt(doc, page, id, area) {
    state.selected = id || null; state.area = area ?? null; state.scroll = true;
    state.doc = doc; state.page = page; drawList(); drawPage();
  }
  async function decide(e, redact) {
    try {
      await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { enabled: redact, decided: true });
      e.enabled = redact; e.decided = true;
      drawDecisions(); drawList(); drawPage();
    } catch (err) { showError(err); }
  }
  function drawDecisions() {
    const open = entities.filter((e) => e.uncertain && !e.decided);
    const pages = attention.pages.filter((p) => !p.reviewed);
    if (!open.length && !pages.length) {
      decideBox.replaceChildren(h("div", { class: "notice ok small" },
        "✔ Keine offenen Entscheidungen. Bitte trotzdem kurz durch die Seiten blättern."));
      return;
    }
    const items = open.slice(0, 200).map((e) => h("div", { class: "entity" },
      h("span", { class: "count", title: e.uncertain }, "?"),
      h("span", {},
        h("span", { class: "orig" }, e.original), " ",
        h("span", { class: "muted small" }, `${e.category_label} · ${e.count}× · ${e.uncertain}`)),
      h("span", {}),
      h("div", { class: "repl" },
        h("button", { class: "btn small", onclick: () => decide(e, true), title: "Ist personenbezogen – schwärzen" }, "■ schwärzen"),
        h("button", { class: "btn small", onclick: () => decide(e, false), title: "Kein Personenbezug – lesbar lassen" }, "□ nicht schwärzen"),
        h("button", { class: "btn link", onclick: () => showAt(e.first.doc, e.first.page, e.id) }, "zeigen"))));
    const pageItems = pages.map((p) => h("div", { class: "entity" },
      h("span", { class: "count" }, "!"),
      h("span", {}, h("strong", {}, `Dokument ${p.doc + 1}, Seite ${p.page + 1}`), " ",
        h("span", { class: "muted small" }, `schlecht lesbar (${p.quality} %) – bitte ansehen und fehlende Namen mit der Maus einrahmen`)),
      h("span", {}),
      h("div", { class: "repl" },
        h("button", { class: "btn link", onclick: () => showAt(p.doc, p.page) }, "Seite ansehen"),
        h("button", {
          class: "btn small", onclick: async () => {
            try {
              await api("POST", `/api/cases/${c.id}/pages/reviewed`, { doc: p.doc, page: p.page });
              p.reviewed = true; drawDecisions();
            } catch (err) { showError(err); }
          },
        }, "✓ geprüft"))));
    const acceptAll = h("button", {
      class: "btn small", title: "Alle offenen Vorschläge so übernehmen, wie sie angezeigt werden (im Zweifel wird geschwärzt)",
      onclick: async () => {
        if (!confirm(`${open.length} Vorschläge so übernehmen, wie sie angezeigt werden` +
          (pages.length ? ` und ${pages.length} Seite(n) als geprüft markieren` : "") + "?")) return;
        try {
          await api("POST", `/api/cases/${c.id}/accept-all`, { pages: true });
          entities.forEach((e) => { if (e.uncertain) e.decided = true; });
          attention.pages.forEach((p) => { p.reviewed = true; });
          drawDecisions(); drawList();
        } catch (err) { showError(err); }
      },
    }, "✓ Alle Vorschläge übernehmen");
    decideBox.replaceChildren(h("div", { class: "notice warn" },
      h("div", { class: "row", style: "justify-content:space-between" },
        h("strong", {}, `Bitte entscheiden: ${open.length + pages.length} offene Punkte`), acceptAll),
      h("div", { class: "small muted" }, "Das Programm ist sich hier nicht sicher. Ihre Entscheidungen werden gespeichert " +
        "und verbessern die Erkennung in künftigen Akten."),
      items, open.length > 200 ? h("p", { class: "small muted" }, `… und ${open.length - 200} weitere`) : null, pageItems));
  }

  // ---- automatically found areas: signatures, handwriting, stamps, images ----
  const AREA_NAMES = { unterschrift: "Unterschrift", handschrift: "Handschrift", stempel: "Stempel", bild: "Bild/Logo" };
  const areasBox = h("div");
  async function removeArea(index) {
    try {
      await api("DELETE", `/api/cases/${c.id}/manual/${index}`);
      // Indices shift after a deletion – reload the list.
      attention = await api("GET", `/api/cases/${c.id}/attention`);
      drawAreas(); drawPage();
    } catch (err) { showError(err); }
  }
  function drawAreas() {
    const areas = attention.areas || [];
    if (!areas.length) { areasBox.replaceChildren(); return; }
    const counts = {};
    areas.forEach((a) => { counts[a.kind] = (counts[a.kind] || 0) + 1; });
    const summaryText = Object.entries(counts).map(([k, n]) => `${n}× ${AREA_NAMES[k] || k}`).join(", ");
    const rows = areas.slice(0, 100).map((a) => h("div", { class: "entity" },
      h("span", { class: "count" }, "✎"),
      h("span", {}, h("strong", {}, AREA_NAMES[a.kind] || a.kind), " ",
        h("span", { class: "muted small" }, `Dokument ${a.doc + 1}, Seite ${a.page + 1}`)),
      h("span", {}),
      h("div", { class: "repl" },
        h("button", { class: "btn link", onclick: () => showAt(a.doc, a.page, null, a.index) }, "zeigen"),
        h("button", {
          class: "btn small", title: "Ist keine Unterschrift/kein personenbezogenes Bild – sichtbar lassen",
          onclick: () => removeArea(a.index),
        }, "□ nicht schwärzen"))));
    areasBox.replaceChildren(h("details", { class: "notice small" },
      h("summary", {}, h("strong", {}, "Automatisch geschwärzte Bereiche: "), summaryText),
      h("div", { class: "small muted" }, "Unterschriften, Handschrift, Stempel und kleine Bilder werden vollständig " +
        "geschwärzt. Falls ein Bereich keine persönlichen Daten enthält, können Sie ihn sichtbar lassen – " +
        "das Programm lernt daraus."),
      rows, areas.length > 100 ? h("p", { class: "small muted" }, `… und ${areas.length - 100} weitere`) : null));
  }

  // ---- entity list (left) ----
  const listBox = h("div");
  const filter = h("input", { type: "search", placeholder: "Suchen …", oninput: () => { state.filter = filter.value.toLowerCase(); drawList(); } });
  const summary = h("span", { class: "muted" });

  function drawList() {
    const groups = {};
    for (const e of entities) {
      if (state.filter && !(`${e.original} ${e.replacement}`.toLowerCase().includes(state.filter))) continue;
      (groups[e.category_label] ||= []).push(e);
    }
    const nodes = [];
    for (const [label, items] of Object.entries(groups).sort()) {
      nodes.push(h("div", { class: "group-head" }, h("span", {}, label), h("span", { class: "muted small" }, `${items.length}`)));
      for (const e of items) nodes.push(entityRow(e));
    }
    if (!nodes.length) nodes.push(h("p", { class: "muted" }, "Keine Einträge."));
    listBox.replaceChildren(...nodes);
    const on = entities.filter((e) => e.enabled);
    summary.textContent = `${on.length} Einträge an ${on.reduce((s, e) => s + e.count, 0)} Stellen werden ersetzt.`;
  }

  function entityRow(e) {
    const cb = h("input", {
      type: "checkbox", title: "Haken = wird geschwärzt/ersetzt", checked: e.enabled,
      onchange: async () => {
        try {
          await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { enabled: cb.checked, decided: true });
          e.enabled = cb.checked; e.decided = true; drawDecisions(); drawList(); drawPage();
        } catch (err) { showError(err); }
      },
    });
    const repl = h("input", {
      type: "text", value: e.replacement, title: "Ersetzung – kann geändert werden",
      onchange: async () => {
        try {
          await api("PATCH", `/api/cases/${c.id}/entities/${e.id}`, { replacement: repl.value });
          e.replacement = repl.value; toast("Ersetzung gespeichert"); drawPage();
        } catch (err) { showError(err); }
      },
    });
    const row = h("div", { class: `entity ${e.enabled ? "" : "off"} ${state.selected === e.id ? "selected" : ""}`, "data-id": e.id },
      cb,
      h("span", { class: "orig", title: e.variants.join(" | ") }, e.original,
        e.uncertain && !e.decided ? h("span", { class: "count", title: e.uncertain, style: "margin-left:.4rem" }, "unsicher") : null),
      h("span", { class: "meta" }, h("span", { class: "count", title: "Anzahl Fundstellen" }, `${e.count}×`)),
      h("div", { class: "repl" }, "→", repl,
        h("button", {
          class: "btn link", title: "Erste Fundstelle anzeigen",
          onclick: () => { state.selected = e.id; state.doc = e.first.doc; state.page = e.first.page; drawList(); drawPage(); },
        }, "zeigen"),
        h("button", {
          class: "btn link", title: "Diesen Begriff auch in Zukunft nie schwärzen",
          onclick: async () => {
            if (!confirm(`„${e.original}“ nicht schwärzen und auch in künftigen Akten nicht mehr vorschlagen?`)) return;
            try {
              await api("POST", `/api/cases/${c.id}/entities/${e.id}/never`);
              e.enabled = false; drawList(); drawPage();
            } catch (err) { showError(err); }
          },
        }, "nie")));
    return row;
  }

  // ---- add term form ----
  const termInput = h("input", { type: "text", placeholder: "Weiteren Namen oder Begriff schwärzen …" });
  const termCat = h("select", {},
    Object.entries(settings.category_labels).map(([v, l]) => h("option", { value: v }, l)),
    h("option", { value: "MANUAL" }, "Sonstiges"));
  termCat.value = "PERSON";
  const termAlways = h("input", { type: "checkbox" });
  const addForm = h("form", {
    class: "panel", style: "padding:.9rem 1rem",
    onsubmit: async (ev) => {
      ev.preventDefault();
      try {
        const res = await api("POST", `/api/cases/${c.id}/entities`, { text: termInput.value, category: termCat.value, always: termAlways.checked });
        toast(res.count ? `${res.count} Fundstelle(n) hinzugefügt` : "Begriff wurde im Text nicht gefunden");
        termInput.value = "";
        entities = await api("GET", `/api/cases/${c.id}/entities`);
        drawDecisions(); drawList(); drawPage();
      } catch (err) { showError(err); }
    },
  }, h("strong", {}, "Begriff hinzufügen"),
    h("div", { class: "row", style: "margin-top:.4rem;flex-wrap:nowrap" }, termInput, termCat, h("button", { class: "btn", type: "submit" }, "Hinzufügen")),
    h("label", { class: "check small muted" }, termAlways, "auch in künftigen Akten immer schwärzen"));

  // ---- page viewer (right) ----
  const img = h("img", { alt: "Seitenvorschau", draggable: "false" });
  const overlay = h("div", { class: "overlay" });
  const wrap = h("div", { class: "page-wrap" }, img, overlay);
  const pageInput = h("input", { type: "number", min: "1", value: "1" });
  const pageCount = h("span", { class: "muted" });
  const kindInfo = h("span", { class: "muted small" });
  const docSelect = h("select", { style: "width:auto" }, c.documents.map((d, i) => h("option", { value: i }, `${i + 1}: ${d.name}`)));
  docSelect.addEventListener("change", () => { state.doc = +docSelect.value; state.page = 0; drawPage(); });
  pageInput.addEventListener("change", () => { state.page = Math.max(0, (+pageInput.value || 1) - 1); drawPage(); });
  let pageData = null;

  async function drawPage() {
    try {
      pageData = await api("GET", `/api/cases/${c.id}/docs/${state.doc}/pages/${state.page}/boxes`);
    } catch (err) { return showError(err); }
    state.page = Math.min(state.page, pageData.pages - 1);
    docSelect.value = state.doc;
    pageInput.value = state.page + 1;
    pageInput.max = pageData.pages;
    pageCount.textContent = `von ${pageData.pages}`;
    kindInfo.textContent = { scan: "Scan – Text per Texterkennung gelesen", mixed: "Text + Bilder (Bilder per Texterkennung gelesen)", digital: "" }[pageData.kind] || "";
    img.src = `/api/cases/${c.id}/docs/${state.doc}/pages/${state.page}.png?zoom=1.6`;
    const W = pageData.width, H = pageData.height;
    const pct = (r) => `left:${(100 * r[0]) / W}%;top:${(100 * r[1]) / H}%;width:${(100 * (r[2] - r[0])) / W}%;height:${(100 * (r[3] - r[1])) / H}%`;
    const boxes = pageData.boxes.map((b) => {
      const ent = entities.find((e) => e.id === b.entity);
      const enabled = ent ? ent.enabled : b.enabled;
      return h("div", {
        class: `hl ${CATEGORY_CLASS(b.category)} ${enabled ? "" : "off"} ${state.selected === b.entity ? "sel" : ""}`,
        style: pct(b.rect), title: enabled ? `wird ersetzt durch: ${b.label}` : "wird NICHT geschwärzt",
        onmousedown: (ev) => ev.stopPropagation(),
        onclick: () => {
          state.selected = b.entity; drawList(); drawPage();
          const row = listBox.querySelector(`[data-id="${b.entity}"]`);
          if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
        },
      });
    });
    const manual = pageData.manual.map((m) => {
      const auto = m.kind && m.kind !== "manuell";
      return h("div", {
        class: `hl manual ${auto ? "auto" : ""} ${state.area === m.index ? "sel" : ""}`, style: pct(m.rect),
        title: auto ? `Automatisch erkannt: ${m.label} – wird geschwärzt` : "Manuell markierter Bereich",
        onmousedown: (ev) => ev.stopPropagation(),
      }, auto ? h("span", { class: "tag" }, m.label) : null, h("span", {
        class: "x", title: auto ? "Nicht schwärzen (Bereich bleibt sichtbar)" : "Markierung entfernen",
        onclick: () => removeArea(m.index),
      }, "×"));
    });
    overlay.replaceChildren(...boxes, ...manual);
    if (state.scroll) {  // "zeigen": bring the marked spot into view
      state.scroll = false;
      const target = overlay.querySelector(".hl.sel");
      const go = () => target && target.scrollIntoView({ block: "center", behavior: "smooth" });
      // Positions are only right once the page image has its size.
      if (img.complete) go(); else img.addEventListener("load", go, { once: true });
    }
  }

  // Draw additional redaction areas with the mouse.
  let start = null, rubber = null;
  overlay.addEventListener("mousedown", (ev) => {
    const r = overlay.getBoundingClientRect();
    start = { x: ev.clientX - r.left, y: ev.clientY - r.top, r };
    rubber = h("div", { class: "drawing" });
    overlay.append(rubber);
  });
  window.addEventListener("mousemove", (ev) => {
    if (!start) return;
    const x = ev.clientX - start.r.left, y = ev.clientY - start.r.top;
    Object.assign(rubber.style, {
      left: `${Math.min(x, start.x)}px`, top: `${Math.min(y, start.y)}px`,
      width: `${Math.abs(x - start.x)}px`, height: `${Math.abs(y - start.y)}px`,
    });
  });
  window.addEventListener("mouseup", async (ev) => {
    if (!start || !pageData) return;
    const s = start; start = null;
    const x = ev.clientX - s.r.left, y = ev.clientY - s.r.top;
    rubber.remove();
    if (Math.abs(x - s.x) < 6 || Math.abs(y - s.y) < 6) return;
    const sx = pageData.width / s.r.width, sy = pageData.height / s.r.height;
    const rect = [Math.min(x, s.x) * sx, Math.min(y, s.y) * sy, Math.max(x, s.x) * sx, Math.max(y, s.y) * sy];
    try {
      await api("POST", `/api/cases/${c.id}/manual`, { doc: state.doc, page: state.page, rect });
      toast("Bereich wird geschwärzt");
      drawPage();
    } catch (err) { showError(err); }
  });

  const go = (delta) => { state.page = Math.max(0, Math.min(pageData.pages - 1, state.page + delta)); drawPage(); };
  document.onkeydown = (ev) => {
    if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
    if (ev.key === "ArrowRight" || ev.key === "PageDown") go(1);
    if (ev.key === "ArrowLeft" || ev.key === "PageUp") go(-1);
  };

  const redactBtn = h("button", {
    class: "btn primary big",
    onclick: async () => {
      const openItems = entities.filter((e) => e.uncertain && !e.decided).length;
      const openPages = attention.pages.filter((p) => !p.reviewed).length;
      if (openItems + openPages > 0 && !confirm(
        `Es sind noch ${openItems} unsichere Fundstelle(n) und ${openPages} schlecht lesbare Seite(n) ungeprüft.\n\n` +
        "Offene Vorschläge werden wie angezeigt übernommen (im Zweifel wird geschwärzt). Trotzdem fortfahren?")) return;
      redactBtn.disabled = true;
      try { await api("POST", `/api/cases/${c.id}/redact`); route(); }
      catch (err) { showError(err); redactBtn.disabled = false; }
    },
  }, "Jetzt endgültig anonymisieren →");

  const warnings = (c.warnings || []).map((w) => h("div", { class: "notice warn small" }, w));
  // Poorly readable pages are listed under "Bitte entscheiden"; other notes
  // are shown here (at most three, otherwise the page would become endless).
  const allNotes = c.documents.flatMap((d) => d.notes || []).filter((n) => !n.includes("schlecht lesbar"));
  const notes = allNotes.slice(0, 3).map((n) => h("div", { class: "notice warn small" }, n));
  if (allNotes.length > 3) notes.push(h("div", { class: "notice warn small" }, `… und ${allNotes.length - 3} weitere Hinweise zu einzelnen Seiten.`));

  render(
    steps(1),
    ...warnings, ...notes,
    h("div", { class: "review" },
      h("div", {},
        decideBox, areasBox,
        h("div", { class: "panel entity-panel" },
          h("h2", { style: "margin-top:0" }, "Gefundene Daten"),
          h("p", { class: "muted small" },
            "Haken = wird ersetzt. Ersetzungen können Sie direkt ändern. Fehlt etwas, fügen Sie es unten hinzu " +
            "oder markieren Sie den Bereich rechts auf der Seite mit gedrückter Maustaste."),
          filter, listBox),
        addForm),
      h("div", { class: "panel" },
        h("div", { class: "viewer-bar" },
          c.documents.length > 1 ? [
            h("button", { class: "btn", title: "Vorheriges Dokument", onclick: () => { state.doc = Math.max(0, state.doc - 1); state.page = 0; drawPage(); } }, "⏮"),
            docSelect,
            h("button", { class: "btn", title: "Nächstes Dokument", onclick: () => { state.doc = Math.min(c.documents.length - 1, state.doc + 1); state.page = 0; drawPage(); } }, "⏭")] : null,
          h("button", { class: "btn", onclick: () => go(-1), title: "Vorherige Seite (←)" }, "◀"),
          h("span", {}, "Seite"), pageInput, pageCount,
          h("button", { class: "btn", onclick: () => go(1), title: "Nächste Seite (→)" }, "▶"),
          kindInfo),
        h("div", { class: "legend" },
          h("span", { class: "c-PERSON" }, "Person"), h("span", { class: "c-ORG" }, "Firma"),
          h("span", { class: "c-ADDRESS" }, "Adresse/Ort"), h("span", { class: "c-BIRTH_DATE" }, "Geburtsdatum"),
          h("span", { class: "c-other" }, "Nummern, Kontakt …")),
        h("p", { class: "muted small" }, "Tipp: Mit gedrückter Maustaste einen Rahmen ziehen, um z. B. Unterschriften, " +
          "Stempel oder Fotos zusätzlich zu schwärzen. Gestrichelte Rahmen werden nicht geschwärzt."),
        wrap,
        h("div", { class: "actionbar" }, summary, redactBtn))),
  );
  drawDecisions(); drawAreas();
  drawList();
  drawPage();
}

// ---------------------------------------------------------------- result

function viewResult(c) {
  const docs = c.documents.map((d, i) => ({ d, i, v: d.verification || {} }));
  const failed = docs.filter((x) => !x.v.passed);
  const hints = docs.filter((x) => (x.v.suspects || []).length);
  // Problems first, then documents with hints, then the rest.
  docs.sort((a, b) => (a.v.passed - b.v.passed) || ((b.v.suspects || []).length - (a.v.suspects || []).length) || a.i - b.i);

  const rows = docs.map(({ d, i, v }) => h("tr", {},
    h("td", {}, v.passed ? h("span", { class: "status fertig" }, "✔ bestanden") : h("span", { class: "status failed" }, "✘ nicht bestanden")),
    h("td", {}, d.name),
    h("td", { class: "muted" }, `${d.pages} S.`),
    h("td", { class: "muted small" }, (v.suspects || []).length ? `${v.suspects.length} Hinweis(e)` : ""),
    h("td", { style: "text-align:right;white-space:nowrap" },
      h("a", { class: "btn small", href: `/api/cases/${c.id}/docs/${i}/download/pdf`, title: "Anonymisiertes PDF herunterladen" }, "PDF"), " ",
      h("a", { class: "btn small", href: `/api/cases/${c.id}/docs/${i}/download/txt`, title: "Textfassung für KI-Werkzeuge" }, "Text"), " ",
      h("button", { class: "btn small", onclick: () => openDocDetails(c, d, i) }, "Details"))));

  render(
    steps(2),
    h("div", { class: "panel" },
      h("h1", {}, failed.length ? "Fertig – bitte die markierten Dokumente prüfen" : "Fertig ✔"),
      failed.length
        ? h("div", { class: "notice bad" }, h("strong", {}, `${failed.length} von ${docs.length} Dokument(en) haben die Prüfung nicht bestanden. `),
          "Diese bitte nicht weitergeben. Unter „Details“ steht, was gefunden wurde; mit „Zurück zur Prüfung“ können Sie nachbessern.")
        : h("div", { class: "notice ok" }, h("strong", {}, `Alle ${docs.length} Dokument(e) haben alle Prüfstufen bestanden. `),
          "Es konnten keine Originaldaten wiederhergestellt werden."),
      hints.length ? h("p", { class: "muted small" }, `${hints.length} Dokument(e) enthalten Hinweise der Nachkontrolle (Stufe 4) – meist Fehlalarme, ein kurzer Blick lohnt sich.`) : null,
      h("p", {}, "Gespeichert in: ", h("code", {}, c.output_dir || "–")),
      h("div", { class: "row" },
        h("button", {
          class: "btn primary big", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/open-folder`); } catch (err) { showError(err); }
          },
        }, "📂 Ergebnisordner öffnen"),
        h("a", { class: "btn big", href: `/api/cases/${c.id}/zip` }, "⬇ Alle als ZIP"),
        h("button", {
          class: "btn", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/review`); route(); } catch (err) { showError(err); }
          },
        }, "✎ Zurück zur Prüfung"),
        c.mapping ? h("a", { class: "btn", href: `/api/cases/${c.id}/mapping`, target: "_blank" }, "🔐 Zuordnungstabelle (vertraulich)") : null,
        h("a", { class: "btn", href: "#/" }, "Neue Akte"))),
    h("div", { class: "panel" },
      h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Ergebnis"), h("th", {}, "Dokument"), h("th", {}, "Seiten"), h("th", {}, "Hinweise"), h("th", {})),
        rows)),
  );
}

function openDocDetails(c, d, i) {
  const close = () => document.getElementById("modal-root").replaceChildren();
  document.getElementById("modal-root").replaceChildren(h("div", {
    class: "modal-back", onclick: (e) => { if (e.target === e.currentTarget) close(); },
  }, h("div", { class: "modal wide" },
    h("div", { class: "row", style: "justify-content:space-between" },
      h("h2", { style: "margin:0" }, `📄 ${d.name}`), h("button", { class: "btn", onclick: close }, "Schließen")),
    resultCard(c, d, i))));
}

function resultCard(c, d, i) {
  const v = d.verification || {};
  const passed = v.passed;
  const base = `/api/cases/${c.id}/docs/${i}`;
  const previewImg = h("img", { alt: "Vorschau des anonymisierten Dokuments" });
  const pageInput = h("input", { type: "number", min: "1", max: d.pages, value: "1", style: "width:4.5rem" });
  const showPage = (p) => {
    const n = Math.max(1, Math.min(d.pages, p));
    pageInput.value = n;
    previewImg.src = `${base}/output/pages/${n - 1}.png?zoom=1.4`;
  };
  pageInput.addEventListener("change", () => showPage(+pageInput.value));
  showPage(1);

  const redoBtn = h("button", {
    class: "btn primary", style: "display:none",
    onclick: async () => {
      try { await api("POST", `/api/cases/${c.id}/redact`); document.getElementById("modal-root").replaceChildren(); route(); }
      catch (err) { showError(err); }
    },
  }, "↻ Korrekturen übernehmen und neu anonymisieren");
  const suspects = (v.suspects || []).map((s) => {
    const li = h("li", {},
      h("button", { class: "btn link", onclick: () => showPage(s.page) }, `Seite ${s.page}`), ": ",
      h("strong", {}, s.text), h("span", { class: "muted small" }, ` (${s.category}) `));
    const decideSuspect = async (redact) => {
      try {
        await api("POST", `/api/cases/${c.id}/suspects`, { text: s.text, category: s.category, redact });
        li.replaceChildren(h("span", { class: "muted" }, `${s.text}: ${redact ? "wird geschwärzt" : "bleibt lesbar (gemerkt)"}`));
        if (redact) redoBtn.style.display = "";
      } catch (err) { showError(err); }
    };
    li.append(h("button", { class: "btn small", onclick: () => decideSuspect(true) }, "schwärzen"), " ",
      h("button", { class: "btn small", onclick: () => decideSuspect(false) }, "kein Personenbezug"));
    return li;
  });
  const leaks = (v.leaks || []).map((l) => h("li", {}, `Seite ${l.page || "–"} (${l.where}): `, h("strong", {}, l.term)));

  return h("div", {},
    passed
      ? h("div", { class: "notice ok" }, h("strong", {}, "✔ Prüfung bestanden. "),
        `Keine der ${v.terms_checked} bekannten Originalangaben ist im Ergebnis enthalten oder wiederherstellbar (${v.pages_checked} Seiten geprüft).`)
      : h("div", { class: "notice bad" }, h("strong", {}, "✘ Prüfung nicht bestanden – bitte NICHT weitergeben. "),
        "Gehen Sie zurück zur Prüfung und ergänzen bzw. korrigieren Sie die Schwärzungen."),
    h("div", { class: "row" },
      h("a", { class: "btn primary", href: `${base}/download/pdf` }, "⬇ Anonymisiertes PDF"),
      h("a", { class: "btn", href: `${base}/download/txt`, title: "Reiner Text in Lesereihenfolge – ideal zum Einfügen in KI-Werkzeuge" }, "⬇ Textfassung (.txt)"),
      h("a", { class: "btn", href: `${base}/download/report?inline=1`, target: "_blank" }, "Prüfbericht öffnen")),
    h("h3", {}, "Automatische Prüfungen"),
    h("ul", { class: "checks" }, (v.checks || []).map((ch) =>
      h("li", { class: ch.ok ? "ok" : "bad" }, ch.name, ch.detail ? h("span", { class: "muted small" }, ` – ${ch.detail}`) : null))),
    leaks.length ? [h("h3", {}, "Gefundene Originaldaten"), h("ul", {}, leaks)] : null,
    suspects.length ? [
      h("h3", {}, "Prüfstufe 4: Bitte kurz entscheiden"),
      h("p", { class: "muted small" }, "Die Nachkontrolle hat Stellen gefunden, die personenbezogen aussehen könnten. " +
        "Oft sind das Fehlalarme (z. B. Fachbegriffe). Ihre Entscheidung wird für künftige Akten gemerkt."),
      h("ul", {}, suspects), redoBtn] : null,
    h("h3", {}, "Vorschau"),
    h("div", { class: "viewer-bar" },
      h("button", { class: "btn", onclick: () => showPage(+pageInput.value - 1) }, "◀"),
      h("span", {}, "Seite"), pageInput, h("span", { class: "muted" }, `von ${d.pages}`),
      h("button", { class: "btn", onclick: () => showPage(+pageInput.value + 1) }, "▶")),
    h("div", { class: "preview" }, previewImg));
}

// -------------------------------------------------------------- settings

async function openSettings() {
  const s = await api("GET", "/api/settings");
  const cats = Object.entries(s.category_labels).map(([value, label]) => {
    const cb = h("input", { type: "checkbox", value, checked: s.categories.includes(value) });
    return { cb, el: h("label", { class: "check" }, cb, label) };
  });
  const mode = h("select", {},
    h("option", { value: "standard" }, "Standard – Text bleibt als Text erhalten (empfohlen)"),
    h("option", { value: "maximum" }, "Maximale Sicherheit – alle Seiten werden als Bild neu aufgebaut"));
  mode.value = s.security_mode;
  const gliner = h("input", { type: "checkbox", checked: s.use_gliner });
  const deep = h("input", { type: "checkbox", checked: s.deep_verification });
  const mapping = h("input", { type: "checkbox", checked: s.write_mapping });
  const learn = h("input", { type: "checkbox", checked: s.use_learning });
  const images = h("input", { type: "checkbox", checked: s.remove_small_images });
  const signatures = h("input", { type: "checkbox", checked: s.detect_signatures });
  const forensic = h("input", { type: "checkbox", checked: s.forensic_check });
  const autoTrain = h("input", { type: "checkbox", checked: s.auto_train });
  const stats = await api("GET", "/api/learning");
  const outDir = h("input", { type: "text", value: s.output_dir });
  const allow = h("textarea", {}, s.allow_terms.join("\n"));
  const deny = h("textarea", {}, s.deny_terms.map((t) => t.text).join("\n"));

  const close = () => document.getElementById("modal-root").replaceChildren();
  const save = async () => {
    try {
      const res = await api("POST", "/api/settings", {
        categories: cats.filter((c) => c.cb.checked).map((c) => c.cb.value),
        security_mode: mode.value, use_gliner: gliner.checked, deep_verification: deep.checked,
        write_mapping: mapping.checked, output_dir: outDir.value, use_learning: learn.checked,
        remove_small_images: images.checked, forensic_check: forensic.checked, auto_train: autoTrain.checked,
        detect_signatures: signatures.checked, allow_terms: allow.value.split("\n"),
        deny_terms: deny.value.split("\n").map((t) => t.trim()).filter(Boolean)
          .map((text) => (s.deny_terms.find((d) => d.text === text) || { text, category: "MANUAL" })),
      });
      if (res.warning) alert(res.warning);
      toast("Einstellungen gespeichert – gelten ab der nächsten Analyse");
      close();
    } catch (err) { showError(err); }
  };

  document.getElementById("modal-root").replaceChildren(h("div", { class: "modal-back", onclick: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal" },
      h("h1", {}, "Einstellungen"),
      h("h3", {}, "Welche Daten sollen entfernt werden?"),
      h("div", { class: "grid2" }, cats.map((c) => c.el)),
      h("h3", {}, "Sicherheitsstufe"), mode,
      h("p", { class: "muted small" }, "Eingescannte Seiten werden immer als Bild neu aufgebaut. „Maximale Sicherheit“ macht das " +
        "auch für digitale Seiten (größere Dateien, Text wird per Texterkennung neu hinterlegt)."),
      h("h3", {}, "Ergebnisordner"), outDir,
      h("label", { class: "check" }, learn, "Aus meinen Entscheidungen lernen (nur lokal gespeichert)"),
      h("details", { class: "advanced" }, h("summary", {}, "Erweiterte Einstellungen"),
      h("h3", {}, "Erkennung und Prüfung"),
      h("label", { class: "check" }, images, "Kleine Bilder auf Textseiten entfernen (Logos, Unterschriften, Stempel, Fotos) – empfohlen"),
      h("label", { class: "check" }, signatures, "Handschriftliche Unterschriften, Handschrift und Stempel auf Scans erkennen und schwärzen – empfohlen"),
      h("label", { class: "check" }, gliner, "KI-Zusatzmodell GLiNER verwenden, falls installiert (gründlicher, langsamer)"),
      h("label", { class: "check" }, deep, "Prüfstufe 2: alle fertigen Seiten als Bild per Texterkennung kontrollieren (empfohlen)"),
      h("label", { class: "check" }, forensic, "Prüfstufe 3: Wiederherstellungsversuche mit unabhängigen Werkzeugen (empfohlen)"),
      h("label", { class: "check" }, mapping, "Vertrauliche Zuordnungstabelle (Original → Ersetzung) anlegen"),
      h("label", { class: "check" }, autoTrain, "Sprachmodell automatisch nachtrainieren, wenn genug neue Beispiele vorliegen"),
      h("p", { class: "muted small" },
        `Bisher ${stats.entscheidungen} Entscheidungen gespeichert: ${stats.immer_schwaerzen} Begriffe werden künftig immer geschwärzt, ` +
        `${stats.nicht_mehr_vorschlagen} nicht mehr vorgeschlagen, ${stats.trainingsbeispiele} Trainingsbeispiele für das Sprachmodell. `,
        h("button", {
          class: "btn link", onclick: async () => {
            if (!confirm("Alle gelernten Entscheidungen löschen?")) return;
            try { await api("POST", "/api/learning/clear"); toast("Lerndaten gelöscht"); } catch (err) { showError(err); }
          },
        }, "Lerndaten löschen"), " · ",
        h("button", {
          class: "btn link", title: "Passt das lokale Sprachmodell an Ihre Entscheidungen an (dauert einige Minuten)",
          onclick: async () => {
            try { await api("POST", "/api/learning/train"); toast("Training gestartet – das Ergebnis erscheint hier"); }
            catch (err) { showError(err); }
          },
        }, "Sprachmodell jetzt nachtrainieren"),
        stats.training && stats.training.message ? h("div", {}, `Training: ${stats.training.message}`) : null,
        stats.custom_model ? h("div", {}, "✔ Ein an Ihre Entscheidungen angepasstes Sprachmodell ist aktiv.") : null),
      h("div", { class: "grid2" },
        h("div", {}, h("h3", {}, "Nie schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile (z. B. Namen von Gerichten oder Ihrer Kanzlei, falls gewünscht)."), allow),
        h("div", {}, h("h3", {}, "Immer schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile – wird in jeder Akte gesucht."), deny))),
      h("div", { class: "row", style: "justify-content:flex-end;margin-top:1.2rem" },
        h("button", { class: "btn", onclick: close }, "Abbrechen"),
        h("button", { class: "btn primary", onclick: save }, "Speichern")))));
}

function openHelp() {
  const close = () => document.getElementById("modal-root").replaceChildren();
  document.getElementById("modal-root").replaceChildren(h("div", { class: "modal-back", onclick: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal" },
      h("h1", {}, "So funktioniert es"),
      h("ol", {},
        h("li", {}, h("strong", {}, "PDF-Dateien oder einen ganzen Ordner"), " auf die Startseite ziehen (oder „Dateien auswählen“). Die Analyse startet sofort."),
        h("li", {}, h("strong", {}, "Prüfen: "), "Oben stehen nur die Punkte, bei denen sich das Programm unsicher ist – jeweils „schwärzen“ oder „nicht schwärzen“ " +
          "wählen oder mit „Alle Vorschläge übernehmen“ abschließen. Fehlendes mit der Maus auf der Seite einrahmen."),
        h("li", {}, h("strong", {}, "Anonymisieren: "), "Ein Klick. Das Programm entfernt die Daten unwiderruflich und prüft jedes Ergebnis in vier Stufen – " +
          "einschließlich echter Versuche, die Daten wiederherzustellen."),
        h("li", {}, h("strong", {}, "Ergebnis: "), "Grün = bestanden, kann weitergegeben werden. Rot = bitte „Details“ ansehen und nachbessern. " +
          "„Alle als ZIP“ lädt alle Ergebnisse auf einmal herunter.")),
      h("p", { class: "muted small" }, "Selbst nachprüfen: anonymisiertes PDF öffnen, Strg+F drücken, einen Namen suchen – er darf nicht gefunden werden."),
      h("h3", {}, "Wichtig"),
      h("ul", {},
        h("li", {}, "Alle Daten bleiben auf diesem Computer. Es wird keine Internetverbindung benötigt."),
        h("li", {}, "Die „Zuordnungstabelle“ hebt die Anonymisierung auf – niemals weitergeben oder hochladen."),
        h("li", {}, "Logos, Fotos, handschriftliche Unterschriften, Handschrift und Stempel werden automatisch erkannt und geschwärzt. " +
          "Sie stehen in der Prüfansicht unter „Automatisch geschwärzte Bereiche“. Fehlt etwas, bitte mit der Maus einrahmen.")),
      h("div", { class: "row", style: "justify-content:flex-end" }, h("button", { class: "btn primary", onclick: close }, "Schließen")))));
}

// ------------------------------------------------------------ program state
// The installed Windows program has no window of its own: the server ends
// itself when no page has been open for a few minutes. This page sends a
// sign of life, offers "Beenden" and reconnects after a restart.
const lifeline = { failures: 0, banner: null, ended: false };

async function heartbeat() {
  if (lifeline.ended) return;
  try {
    await api("POST", "/api/heartbeat");
    lifeline.failures = 0;
    if (lifeline.banner) location.reload();  // the program runs again
  } catch (err) {
    lifeline.failures += 1;
    if (lifeline.failures >= 2 && !lifeline.banner) {
      lifeline.banner = h("div", { class: "modal-back" }, h("div", { class: "modal" },
        h("h1", {}, "Das Programm läuft nicht mehr"),
        h("p", {}, "Es wurde beendet – zum Beispiel automatisch nach einer längeren Pause. Ihre Akten sind gespeichert."),
        h("p", {}, h("strong", {}, "Zum Weiterarbeiten: "),
          "auf dem Desktop doppelt auf „Dokumenten-Anonymisierer“ klicken. Diese Seite verbindet sich dann von selbst wieder.")));
      document.body.append(lifeline.banner);
    }
  }
}

async function quitProgram() {
  if (!confirm("Programm beenden?\n\nIhre Akten und Ergebnisse bleiben gespeichert.")) return;
  const post = (body) => fetch("/api/shutdown", {
    method: "POST", headers: { "X-Anonymizer": "1", "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  try {
    let resp = await post();
    if (resp.status === 409) {
      if (!confirm("Es läuft gerade noch eine Verarbeitung. Trotzdem beenden?\n\n" +
        "Die laufende Verarbeitung muss dann später neu gestartet werden.")) return;
      resp = await post({ trotzdem: true });
    }
    if (!resp.ok) throw new Error(`Fehler ${resp.status}`);
    lifeline.ended = true;
    render(h("div", { class: "panel" }, h("h1", {}, "Das Programm wurde beendet"),
      h("p", {}, "Sie können dieses Browserfenster jetzt schließen. Zum erneuten Start auf dem Desktop doppelt auf „Dokumenten-Anonymisierer“ klicken.")));
  } catch (err) { showError(err); }
}

setInterval(heartbeat, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) heartbeat(); });
api("GET", "/api/status").then((s) => {
  const quit = document.getElementById("btn-quit");
  if (s && s.managed) { quit.hidden = false; quit.addEventListener("click", quitProgram); }
}).catch(() => {});

document.getElementById("btn-settings").addEventListener("click", () => openSettings().catch(showError));
document.getElementById("btn-help").addEventListener("click", openHelp);
route();
