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
function render(...nodes) { app().replaceChildren(...nodes); window.scrollTo(0, 0); }

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

async function viewHome() {
  const [cases, status] = await Promise.all([api("GET", "/api/cases"), api("GET", "/api/status")]);
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

  render(
    problems.length ? h("div", { class: "notice bad" }, h("strong", {}, "Installation unvollständig: "),
      problems.join(" ")) : null,
    h("div", { class: "panel" },
      h("h1", {}, "PDF-Dokumente anonymisieren"),
      h("p", { class: "muted" },
        "Personenbezogene Daten werden erkannt, Namen durch Initialen ersetzt (z. B. „M. M.“) und alle " +
        "übrigen Daten durch Platzhalter (z. B. „[Adresse 1]“). Die Daten werden dabei unwiderruflich " +
        "aus der Datei entfernt. Alles geschieht auf diesem Computer – nichts wird ins Internet übertragen."),
      h("a", { class: "btn primary big", href: "#/neu" }, "＋ Neue Akte anonymisieren")),
    h("div", { class: "panel" },
      h("h2", {}, "Bisherige Akten"),
      cases.length ? h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Bezeichnung"), h("th", {}, "Angelegt"), h("th", {}, "Umfang"),
          h("th", {}, "Status"), h("th", {})),
        rows) : h("p", { class: "muted" }, "Noch keine Akten vorhanden.")),
  );
}

// ---------------------------------------------------------------- new case

function viewNew() {
  const files = [];
  const list = h("ul", { class: "filelist" });
  const startBtn = h("button", { class: "btn primary big", disabled: true }, "Analyse starten →");
  const title = h("input", { type: "text", placeholder: "z. B. „Akte 2024-117 Bauschaden“ (nur intern sichtbar)" });
  const input = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, style: "display:none" });

  function refresh() {
    list.replaceChildren(...files.map((f, i) => h("li", {},
      h("span", {}, `📄 ${f.name}`),
      h("span", {}, h("span", { class: "muted small" }, `${(f.size / 1024 / 1024).toFixed(1)} MB `),
        h("button", { class: "btn link", onclick: () => { files.splice(i, 1); refresh(); } }, "entfernen")))));
    startBtn.disabled = files.length === 0;
  }
  function addFiles(fileList) {
    for (const f of fileList) {
      if (!f.name.toLowerCase().endsWith(".pdf")) { toast(`„${f.name}“ ist keine PDF-Datei`); continue; }
      if (!files.some((x) => x.name === f.name && x.size === f.size)) files.push(f);
    }
    refresh();
  }
  input.addEventListener("change", () => addFiles(input.files));

  const drop = h("div", {
    class: "dropzone", tabindex: "0",
    onclick: () => input.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") input.click(); },
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
    ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles(e.dataTransfer.files); },
  }, h("strong", {}, "PDF-Dateien hierher ziehen"), h("br"),
    h("span", { class: "muted" }, "oder klicken, um Dateien auszuwählen. Mehrere Dateien einer Akte werden gemeinsam " +
      "verarbeitet – dieselbe Person erhält dann in allen Dokumenten dieselben Initialen."));

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    startBtn.textContent = "Wird hochgeladen …";
    try {
      const c = await api("POST", "/api/cases", { title: title.value || files[0].name.replace(/\.pdf$/i, "") });
      const form = new FormData();
      files.forEach((f) => form.append("files", f, f.name));
      await api("POST", `/api/cases/${c.id}/documents`, form);
      await api("POST", `/api/cases/${c.id}/analyse`);
      location.hash = `#/akte/${c.id}`;
    } catch (err) {
      showError(err);
      startBtn.disabled = false;
      startBtn.textContent = "Analyse starten →";
    }
  });

  render(
    steps(0),
    h("div", { class: "panel" },
      h("h1", {}, "Neue Akte"),
      h("label", {}, h("strong", {}, "Bezeichnung der Akte"), h("br"), title),
      h("p", { class: "muted small" }, "Die Bezeichnung erscheint nicht im anonymisierten Dokument. " +
        "Sie dient nur dazu, die Akte in der Übersicht wiederzufinden."),
      h("h3", {}, "PDF-Dateien"),
      drop, input, list,
      h("div", { class: "row", style: "justify-content:flex-end;margin-top:1rem" },
        h("a", { class: "btn", href: "#/" }, "Abbrechen"), startBtn)),
  );
}

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
        ? "Der Text wird gelesen (bei Scans per Texterkennung) und nach personenbezogenen Daten durchsucht. " +
          "Bei mehreren hundert Seiten kann das einige Minuten dauern. Sie können das Fenster offen lassen."
        : "Die Daten werden entfernt, das neue PDF wird erzeugt und anschließend automatisch geprüft."),
      bar, label),
  );
  async function poll() {
    try {
      const cur = await api("GET", `/api/cases/${c.id}`);
      if (cur.status !== c.status) return route();
      const p = cur.progress || {};
      const pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
      bar.firstChild.style.width = `${pct}%`;
      label.textContent = `${p.step || "Wartet …"}${p.total > 1 ? ` – ${p.done} von ${p.total}` : ""}`;
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
  function showAt(doc, page, id) {
    state.selected = id || null; state.doc = doc; state.page = page; drawList(); drawPage();
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
    decideBox.replaceChildren(h("div", { class: "notice warn" },
      h("strong", {}, `Bitte entscheiden: ${open.length + pages.length} offene Punkte`),
      h("div", { class: "small muted" }, "Das Programm ist sich hier nicht sicher. Ihre Entscheidungen werden gespeichert " +
        "und verbessern die Erkennung in künftigen Akten."),
      items, pageItems));
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
    const manual = pageData.manual.map((m) => h("div", {
      class: "hl manual", style: pct(m.rect), title: "Manuell markierter Bereich",
      onmousedown: (ev) => ev.stopPropagation(),
    }, h("span", {
      class: "x", title: "Markierung entfernen",
      onclick: async () => {
        try { await api("DELETE", `/api/cases/${c.id}/manual/${m.index}`); drawPage(); } catch (err) { showError(err); }
      },
    }, "×")));
    overlay.replaceChildren(...boxes, ...manual);
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
  const notes = c.documents.flatMap((d) => d.notes || []).map((n) => h("div", { class: "notice warn small" }, n));

  render(
    steps(1),
    ...warnings, ...notes,
    h("div", { class: "review" },
      h("div", {},
        decideBox,
        h("div", { class: "panel entity-panel" },
          h("h2", { style: "margin-top:0" }, "Gefundene Daten"),
          h("p", { class: "muted small" },
            "Haken = wird ersetzt. Ersetzungen können Sie direkt ändern. Fehlt etwas, fügen Sie es unten hinzu " +
            "oder markieren Sie den Bereich rechts auf der Seite mit gedrückter Maustaste."),
          filter, listBox),
        addForm),
      h("div", { class: "panel" },
        h("div", { class: "viewer-bar" },
          c.documents.length > 1 ? docSelect : null,
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
  drawDecisions();
  drawList();
  drawPage();
}

// ---------------------------------------------------------------- result

function viewResult(c) {
  const cards = c.documents.map((d, i) => resultCard(c, d, i));
  render(
    steps(2),
    h("div", { class: "panel" },
      h("h1", {}, "Fertig"),
      h("p", {}, "Die anonymisierten Dateien wurden gespeichert in:"),
      h("p", {}, h("code", {}, c.output_dir || "–")),
      h("div", { class: "row" },
        h("button", {
          class: "btn primary", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/open-folder`); } catch (err) { showError(err); }
          },
        }, "📂 Ergebnisordner öffnen"),
        h("button", {
          class: "btn", onclick: async () => {
            try { await api("POST", `/api/cases/${c.id}/review`); route(); } catch (err) { showError(err); }
          },
        }, "✎ Zurück zur Prüfung (Korrekturen)"),
        c.mapping ? h("a", { class: "btn", href: `/api/cases/${c.id}/mapping`, target: "_blank" }, "🔐 Zuordnungstabelle (VERTRAULICH)") : null,
        h("a", { class: "btn", href: "#/" }, "Zur Übersicht"))),
    ...cards,
  );
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
      try { await api("POST", `/api/cases/${c.id}/redact`); route(); } catch (err) { showError(err); }
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

  return h("div", { class: "panel" },
    h("h2", { style: "margin-top:0" }, `📄 ${d.name}`),
    passed
      ? h("div", { class: "notice ok" }, h("strong", {}, "✔ Prüfung bestanden. "),
        `Keine der ${v.terms_checked} bekannten Originalangaben ist im Ergebnis enthalten (${v.pages_checked} Seiten geprüft).`)
      : h("div", { class: "notice bad" }, h("strong", {}, "✘ Prüfung nicht bestanden – bitte NICHT weitergeben. "),
        "Gehen Sie zurück zur Prüfung und ergänzen Sie die Schwärzungen."),
    h("div", { class: "row" },
      h("a", { class: "btn primary", href: `${base}/download/pdf` }, "⬇ Anonymisiertes PDF"),
      h("a", { class: "btn", href: `${base}/download/txt`, title: "Reiner Text in Lesereihenfolge – ideal zum Einfügen in KI-Werkzeuge" }, "⬇ Textfassung (.txt)"),
      h("a", { class: "btn", href: `${base}/download/report?inline=1`, target: "_blank" }, "Prüfbericht öffnen")),
    h("h3", {}, "Automatische Prüfungen"),
    h("ul", { class: "checks" }, (v.checks || []).map((ch) =>
      h("li", { class: ch.ok ? "ok" : "bad" }, ch.name, ch.detail ? h("span", { class: "muted small" }, ` – ${ch.detail}`) : null))),
    leaks.length ? [h("h3", {}, "Gefundene Originaldaten"), h("ul", {}, leaks)] : null,
    suspects.length ? [
      h("h3", {}, "Prüfstufe 3: Bitte kurz entscheiden"),
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
        allow_terms: allow.value.split("\n"),
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
      h("h3", {}, "Erkennung und Prüfung"),
      h("label", { class: "check" }, gliner, "KI-Zusatzmodell GLiNER verwenden, falls installiert (gründlicher, langsamer)"),
      h("label", { class: "check" }, deep, "Prüfstufe 2: alle fertigen Seiten als Bild per Texterkennung kontrollieren (empfohlen)"),
      h("label", { class: "check" }, mapping, "Vertrauliche Zuordnungstabelle (Original → Ersetzung) anlegen"),
      h("label", { class: "check" }, learn, "Aus meinen Entscheidungen lernen (nur lokal gespeichert)"),
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
      h("h3", {}, "Ergebnisordner"), outDir,
      h("div", { class: "grid2" },
        h("div", {}, h("h3", {}, "Nie schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile (z. B. Namen von Gerichten oder Ihrer Kanzlei, falls gewünscht)."), allow),
        h("div", {}, h("h3", {}, "Immer schwärzen"), h("p", { class: "muted small" }, "Ein Begriff pro Zeile – wird in jeder Akte gesucht."), deny)),
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
        h("li", {}, h("strong", {}, "Neue Akte anlegen"), " und die PDF-Dateien hineinziehen. Zusammengehörige Dokumente gemeinsam verarbeiten."),
        h("li", {}, h("strong", {}, "Prüfen: "), "Links stehen alle gefundenen Daten, rechts sehen Sie die Seite mit farbigen Markierungen. " +
          "Haken entfernen, wenn etwas nicht geschwärzt werden soll. Fehlendes über „Begriff hinzufügen“ ergänzen oder auf der Seite mit der Maus einrahmen."),
        h("li", {}, h("strong", {}, "Anonymisieren: "), "Das Programm entfernt die Daten unwiderruflich, erstellt ein neues PDF, eine Textfassung und einen Prüfbericht."),
        h("li", {}, h("strong", {}, "Ergebnis kontrollieren: "), "Der Prüfbericht zeigt, dass keine Originaldaten mehr enthalten sind. " +
          "Sie können das zusätzlich selbst prüfen: PDF öffnen, Strg+F drücken und nach einem Namen suchen – er darf nicht gefunden werden.")),
      h("h3", {}, "Wichtig"),
      h("ul", {},
        h("li", {}, "Alle Daten bleiben auf diesem Computer. Es wird keine Internetverbindung benötigt."),
        h("li", {}, "Die „Zuordnungstabelle“ hebt die Anonymisierung auf – niemals weitergeben oder hochladen."),
        h("li", {}, "Unterschriften, Stempel, Fotos und Handschrift bitte selbst mit der Maus einrahmen.")),
      h("div", { class: "row", style: "justify-content:flex-end" }, h("button", { class: "btn primary", onclick: close }, "Schließen")))));
}

document.getElementById("btn-settings").addEventListener("click", () => openSettings().catch(showError));
document.getElementById("btn-help").addEventListener("click", openHelp);
route();
