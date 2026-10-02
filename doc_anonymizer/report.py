"""HTML reports.

* **Prüfbericht** (verification report) – may be passed on together with
  the anonymised PDF. It contains *no* original data: only the checks, the
  number of redactions per category and the replacement labels.
* **Zuordnungstabelle** (mapping table) – CONFIDENTIAL. Lists original ->
  replacement so the lawyer can translate results back. It is written to a
  separate folder named "VERTRAULICH_nicht_weitergeben".
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING

from .models import Category

if TYPE_CHECKING:  # pragma: no cover - imports for type hints only
    from .jobs import Case, DocumentInfo
    from .redact import RedactionResult
    from .registry import CaseRegistry
    from .verify import VerificationResult

_STYLE = """
body{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:2em auto;padding:0 1em;
color:#1d2433;line-height:1.45}
h1{font-size:1.6em;margin-bottom:.2em}h2{font-size:1.2em;margin-top:1.8em;border-bottom:1px solid #ddd}
table{border-collapse:collapse;width:100%;margin:.5em 0}td,th{border:1px solid #d5d9e0;
padding:.35em .6em;text-align:left;vertical-align:top}th{background:#f3f5f8}
.ok{color:#0b7a32;font-weight:600}.bad{color:#b3261e;font-weight:600}
.banner{padding:1em 1.2em;border-radius:8px;font-size:1.15em;margin:1em 0}
.banner.ok{background:#e6f4ea;border:1px solid #9ad3ae}.banner.bad{background:#fdecea;border:1px solid #f1aaa4}
.warn{background:#fff7e0;border:1px solid #f0d58c;padding:.8em 1em;border-radius:8px}
small,.muted{color:#5f6b7a}
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='de'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>")


def verification_report(case: "Case", doc: "DocumentInfo", verification: "VerificationResult",
                        result: "RedactionResult", registry: "CaseRegistry") -> str:
    """Shareable verification report (contains no original personal data)."""
    from pathlib import Path

    passed = verification.passed
    counts: Counter[str] = Counter()
    labels: dict[str, set[str]] = {}
    doc_index = next(i for i, d in enumerate(case.documents) if d is doc)
    areas = Counter(b.kind for b in registry.manual_boxes if b.doc == doc_index)
    area_text = ", ".join(f"{n} × {name}" for kind, name in (
        ("unterschrift", "Unterschrift"), ("handschrift", "Handschrift"), ("stempel", "Stempel"),
        ("bild", "Bild/Logo"), ("manuell", "manuell markierter Bereich"))
        if (n := areas.get(kind)))
    for m in registry.mentions:
        entity = registry.entities[m.entity_id]
        if m.doc == doc_index and entity.enabled:
            counts[entity.category.label_de] += 1
            labels.setdefault(entity.category.label_de, set()).add(entity.replacement)
    manual = sum(1 for b in registry.manual_boxes if b.doc == doc_index)
    if manual:
        counts[Category.MANUAL.label_de] += manual

    rows_checks = "".join(
        f"<tr><td class='{'ok' if c.ok else 'bad'}'>{'✔' if c.ok else '✘'}</td>"
        f"<td>{_e(c.name)}</td><td class='muted'>{_e(c.detail)}</td></tr>"
        for c in verification.checks)
    rows_counts = "".join(
        f"<tr><td>{_e(cat)}</td><td>{n}</td><td class='muted'>"
        f"{_e(', '.join(sorted(labels.get(cat, set()))[:30]))}</td></tr>"
        for cat, n in sorted(counts.items()))
    leak_pages = sorted({(leak.page, leak.where) for leak in verification.leaks})
    leaks = ""
    if leak_pages:
        leaks = ("<h2>Gefundene Restdaten</h2><p class='bad'>An folgenden Stellen wurden "
                 "Originaldaten gefunden. Bitte das Dokument NICHT weitergeben und die Akte "
                 "im Programm erneut prüfen.</p><ul>" +
                 "".join(f"<li>Seite {p or '–'} ({_e(w)})</li>" for p, w in leak_pages) +
                 "</ul>")
    suspects = ""
    if verification.suspects:
        pages = sorted({s.page for s in verification.suspects})
        suspects = ("<h2>Hinweise zur Sichtprüfung</h2><div class='warn'>Die Nachkontrolle hat "
                    f"auf den Seiten {_e(', '.join(map(str, pages)))} Stellen gefunden, die "
                    "personenbezogen aussehen könnten (z.&nbsp;B. Eigennamen, die keinem "
                    "bekannten Muster entsprechen). Häufig sind das Fehlalarme. Die Liste "
                    "mit den konkreten Stellen ist im Programm unter „Ergebnis“ sichtbar."
                    "</div>")
    banner = ("<div class='banner ok'>✔ Prüfung bestanden: Im Ergebnis wurden keine der "
              f"{verification.terms_checked} bekannten Originaldaten gefunden.</div>" if passed
              else "<div class='banner bad'>✘ Prüfung NICHT bestanden – bitte nicht "
              "weitergeben.</div>")
    mode = ("Alle Seiten als Bild neu aufgebaut (maximale Sicherheit)"
            if result.raster_pages == result.pages else
            f"{result.raster_pages} von {result.pages} Seiten als Bild neu aufgebaut "
            "(Scans bzw. Seiten mit verstecktem Inhalt); übrige Seiten mit echter "
            "Text-Schwärzung")
    body = f"""
<h1>Prüfbericht Anonymisierung</h1>
<p class="muted">Erstellt am {datetime.now():%d.%m.%Y um %H:%M} Uhr · Datei:
{_e(Path(doc.output).name)} · {result.pages} Seiten</p>
{banner}
<h2>Automatische Prüfungen</h2>
<table><tr><th></th><th>Prüfung</th><th>Details</th></tr>{rows_checks}</table>
{leaks}
<h2>Ersetzungen</h2>
<table><tr><th>Kategorie</th><th>Anzahl Stellen</th><th>Verwendete Platzhalter</th></tr>
{rows_counts or "<tr><td colspan=3>Keine</td></tr>"}</table>
{suspects}
<h2>Verfahren</h2>
<ul>
<li>Verarbeitung ausschließlich lokal auf diesem Computer – keine Übertragung an Dritte
oder Cloud-Dienste.</li>
<li>Erkennung: Regeln für deutsche Rechtstexte, lokales Sprachmodell (spaCy)
{"und KI-Modell GLiNER" if any(s == "ki:gliner" for e in registry.entities.values()
                               for s in e.sources) else ""}, manuelle Prüfung durch den Nutzer.</li>
<li>{_e(mode)}.</li>
<li>Die Originalzeichen wurden aus der Datei entfernt (nicht nur überdeckt). Metadaten,
Kommentare, Anhänge, Lesezeichen, Formulare und frühere Dateiversionen wurden entfernt.</li>
<li>Prüfstufe 1 (Text): vollständige Suche nach allen Originalbegriffen im fertigen PDF
inkl. unsichtbarem Text, Dateiname und Dateistruktur.</li>
<li>Prüfstufe 2 (Bild): {verification.ocr_pages_checked} Seiten wurden als Bild gelesen
(Texterkennung) und ebenfalls nach allen Originalbegriffen durchsucht.</li>
<li>Prüfstufe 3 (Wiederherstellungsversuche): {_e("; ".join(verification.forensic_methods)
or "nicht durchgeführt")}.</li>
<li>Prüfstufe 4 (Nachkontrolle): erneute automatische Erkennung auf dem Ergebnis;
Auffälligkeiten wurden dem Nutzer zur Entscheidung vorgelegt.</li>
<li>Eingebettete Schriften wurden auf die verbliebenen Zeichen reduziert; versteckte
Alternativtexte (ActualText/Alt) entfernt; kleine Bilder (Logos, Unterschriften, Fotos)
entfernt, sofern der Nutzer sie nicht ausdrücklich behalten hat.</li>
<li>Geschwärzte Flächen: {_e(area_text or "keine")}. Handschriftliche Unterschriften,
Handschrift und Stempel auf Scans werden automatisch erkannt.</li>
</ul>
<p class="muted">Hinweis: Eine automatische Anonymisierung kann eine abschließende
Sichtkontrolle nicht vollständig ersetzen. Bitte insbesondere Unterschriften, Stempel,
Fotos und handschriftliche Vermerke prüfen.</p>
"""
    return _page("Prüfbericht Anonymisierung", body)


def mapping_report(case: "Case", registry: "CaseRegistry") -> str:
    """CONFIDENTIAL mapping table original -> replacement."""
    rows = []
    for entity in sorted(registry.entities.values(),
                         key=lambda e: (e.category.label_de, e.replacement)):
        variants = ", ".join(v for v in entity.variants if v != entity.original)
        rows.append(
            f"<tr><td>{_e(entity.category.label_de)}</td><td>{_e(entity.replacement)}</td>"
            f"<td>{_e(entity.original)}<br><small>{_e(variants)}</small></td>"
            f"<td>{entity.count}</td><td>{'ja' if entity.enabled else 'NEIN'}</td></tr>")
    docs = "".join(f"<li>{_e(d.name)} → {_e(d.output)}</li>" for d in case.documents)
    body = f"""
<div class="banner bad">VERTRAULICH – diese Tabelle hebt die Anonymisierung auf.
Niemals zusammen mit den anonymisierten Dokumenten weitergeben oder hochladen!</div>
<h1>Zuordnungstabelle: {_e(case.title)}</h1>
<p class="muted">Erstellt am {datetime.now():%d.%m.%Y um %H:%M} Uhr</p>
<h2>Dokumente</h2><ul>{docs}</ul>
<h2>Ersetzungen</h2>
<table><tr><th>Kategorie</th><th>Ersetzt durch</th><th>Original (Varianten)</th>
<th>Stellen</th><th>Geschwärzt</th></tr>{''.join(rows)}</table>
"""
    return _page("VERTRAULICH – Zuordnungstabelle", body)
