# Dokumenten-Anonymisierer

Lokales Programm für Kanzleien, das PDF-Dokumente – auch mit mehreren hundert
Seiten und eingescannte Akten – **vollständig auf dem eigenen Computer**
anonymisiert. Ziel: Die Dokumente können anschließend gefahrlos weitergegeben
oder z. B. von einem KI-Sprachmodell analysiert werden.

* Namen werden durch **Initialen** ersetzt (`Max Mustermann` → `M. M.`,
  `Herr Mustermann` → `Herr M.`), sodass der Sachverhalt nachvollziehbar bleibt.
* Alle übrigen personenbezogenen Daten werden durch **nummerierte Platzhalter**
  ersetzt (`[Adresse 1]`, `[IBAN 1]`, `[Geburtsdatum 1]` …). Dieselbe Angabe
  erhält in allen Dokumenten einer Akte denselben Platzhalter.
* Die Daten werden **unwiderruflich aus der Datei entfernt** (keine
  überdeckenden Kästen, die man wegklicken könnte).
* Jedes Ergebnis wird in **vier Prüfstufen** automatisch kontrolliert (inkl.
  echter Wiederherstellungsversuche); ein
  **Prüfbericht** dokumentiert das Ergebnis.
* Unsichere Stellen legt das Programm dem Nutzer zur Entscheidung vor und
  **lernt aus diesen Entscheidungen**.
* Es werden **keine Daten ins Internet** gesendet – auch nicht an KI-Dienste.

---

## 1. Installation (Windows, einmalig)

1. Ordner an einen beliebigen Ort kopieren (z. B. `C:\Programme\Anonymisierer`
   oder in die eigenen Dokumente).
2. **Doppelklick auf `Installieren.bat`.** Das Skript installiert bei Bedarf
   Python 3.12 und die Texterkennung Tesseract (über `winget`), richtet eine
   eigene Programmumgebung ein, lädt das deutsche Sprachmodell
   (ca. 550 MB) und legt eine Verknüpfung auf dem Desktop an.
   Für diesen Schritt wird einmalig Internet benötigt (nur Downloads).
3. Optional: **`Zusatzmodell_installieren.bat`** installiert das KI-Modell
   GLiNER (ca. 2 GB). Es verbessert die Erkennung, macht die Analyse aber
   langsamer.

Danach läuft das Programm vollständig offline.

## 2. Bedienung

1. **Start:** Doppelklick auf „Dokumenten-Anonymisierer“ (Desktop) bzw.
   `Start.bat`. Der Browser öffnet sich mit der Oberfläche. Der Browser dient
   nur als Anzeige – das Programm ist ausschließlich auf diesem Computer
   erreichbar (`127.0.0.1`).
2. **Dokumente ablegen:** PDF-Dateien **oder einen ganzen Ordner** auf die
   Startseite ziehen (oder „Dateien auswählen“ / „Ordner auswählen“). Die
   Analyse startet sofort. Alles, was gemeinsam abgelegt wird, ist eine
   „Akte“: Dieselbe Person erhält in allen Dokumenten dieselben Initialen.
   Auch hunderte Dokumente sind kein Problem (siehe Abschnitt 8).
3. **Prüfen:**
   * Oben stehen nur die Punkte, bei denen sich das Programm **nicht sicher**
     ist (z. B. nur vom Sprachmodell erkannte Wörter, schlecht lesbare
     Scan-Seiten). Jeweils „schwärzen“ / „nicht schwärzen“ wählen – oder mit
     **„Alle Vorschläge übernehmen“** in einem Schritt abschließen.
   * Darunter alle gefundenen Daten mit ihrer Ersetzung. Haken entfernen =
     nicht schwärzen. Ersetzungen lassen sich direkt ändern.
   * Fehlt etwas: „Begriff hinzufügen“ oder auf der Seitenansicht **mit der
     Maus einen Rahmen ziehen**.
4. **Anonymisieren:** „Jetzt endgültig anonymisieren“. Ergebnis je Dokument:
   * `…_anonymisiert.pdf` – das anonymisierte Dokument,
   * `…_anonymisiert.txt` – Textfassung in Lesereihenfolge (ideal zum
     Einfügen in KI-Werkzeuge),
   * `…_Pruefbericht.html` – Prüfbericht (enthält keine Originaldaten),
   * `VERTRAULICH_nicht_weitergeben\Zuordnungstabelle.html` – Original →
     Ersetzung. **Hebt die Anonymisierung auf, niemals weitergeben.**

   Die Ergebnisseite zeigt eine Tabelle aller Dokumente (Problemfälle
   zuerst), „Alle als ZIP“ lädt alle Ergebnisse auf einmal herunter (ohne die
   vertrauliche Zuordnungstabelle).

   Standard-Ergebnisordner ist `C:\Users\<Name>\Anonymisiert` – bewusst nicht
   „Dokumente“ oder „Desktop“, weil diese oft per OneDrive in die Cloud
   synchronisiert werden. Das Programm warnt, wenn ein Cloud-Ordner gewählt wird.
5. **Nachkontrolle:** Meldet Prüfstufe 4 noch Auffälligkeiten, kann man sie
   unter „Details“ direkt „schwärzen“ oder als „kein Personenbezug“ markieren
   und neu anonymisieren.

**Selbst nachprüfen:** Anonymisiertes PDF öffnen, `Strg+F`, einen der Namen
suchen – er darf nicht gefunden werden.

## 3. Was wird erkannt?

| Kategorie | Beispiele | Ersetzung |
|---|---|---|
| Personennamen (alle natürlichen Personen, auch Richter/Anwälte) | Max Mustermann, Herr Dr. Müller, Mustermann, Erika, geb. Schmidt, gez. … | `M. M.`, `Herr M.` |
| Adressen | Lindenstraße 12, 80331 München, Am Markt 3, Postfach 10 20 30 | `[Adresse n]` |
| Orte (außer Gerichts-/Behördennamen) | Geburtsort, Wohnort | `[Ort n]` |
| Firmen/Organisationen | Müller Bau GmbH, Kanzlei X & Partner | `[Firma n]` |
| Kontakt | Telefon/Fax (alle gängigen Schreibweisen), E-Mail, Webadressen | `[Telefon n]` … |
| Bank | IBAN (mit Prüfziffer), BIC, Kontonummer, Kreditkarte (Luhn) | `[IBAN n]` … |
| Kennnummern | Steuer-ID (Prüfziffer), Steuernummer, SV-/KV-Nummer, Ausweis-/Pass-/Führerscheinnummer, Kfz-Kennzeichen, Kunden-/Vertrags-/Personalnummern, eigene Aktenzeichen, HRB, USt-IdNr., IP-Adressen | `[Nummer n]` … |
| Geburtsdaten | geb. am 03.04.1978 | `[Geburtsdatum n]` |
| Sonstige Eigennamen | vom Sprachmodell als Eigenname markiert | `[Name n]` (zur Entscheidung) |

**Bewusst lesbar bleiben:** Gerichte und Behörden („Landgericht München I“),
Paragraphen und Gesetze, Aktenzeichen *veröffentlichter* Entscheidungen
(„BGH, Urteil vom … – VI ZR 123/19“), sonstige Datumsangaben, Beträge.
Alles ist in den Einstellungen anpassbar (Kategorien, „nie schwärzen“,
„immer schwärzen“).

## 4. Wie funktioniert die Erkennung?

Mehrere voneinander unabhängige Verfahren werden kombiniert:

1. **Regeln für deutsche Rechtstexte** – Anreden, Titel, Rollen („Rechtsanwältin“,
   „Zeuge“, „Richter am Landgericht“), Unterschriftszeilen, Geburtsnamen,
   Schreibweise „Nachname, Vorname“, über 8 000 Vornamen; Muster mit
   Prüfziffern für IBAN, Steuer-ID, Kreditkarten.
2. **Lokales Sprachmodell spaCy** `de_core_news_lg` (Open Source, MIT).
3. **Optional GLiNER** `urchade/gliner_multi_pii-v1` (Open Source, Apache 2.0),
   ein speziell auf personenbezogene Daten trainiertes Modell.
4. **Weitergabe:** Ist ein Name einmal erkannt, wird *jedes* weitere
   Vorkommen gefunden – auch in Großbuchstaben, im Genitiv („Mustermanns“),
   über Zeilenumbrüche und bei Scans mit kleinen Lesefehlern.
5. **Texterkennung (OCR) für Scans** mit Tesseract in zwei Durchgängen
   (normale Seitenanalyse + „verstreuter Text“ für Kleingedrucktes,
   Absenderzeilen, Stempel).

## 5. Warum kann die Anonymisierung nicht rückgängig gemacht werden?

* **Echtes Schwärzen:** Die Zeichen werden aus dem Seiteninhalt *gelöscht*,
  Bildpunkte unter geschwärzten Stellen überschrieben.
* **Neue Datei:** Das Ergebnis ist ein neu aufgebautes PDF, in das nur die
  bereinigten Seiten kopiert werden. Metadaten (Autor …), XMP, Kommentare,
  Formularfelder, Lesezeichen, Anhänge, JavaScript, Links, ausgeblendete
  Ebenen und frühere Dateiversionen werden nicht übernommen.
* **Scans** (und auf Wunsch *alle* Seiten, Einstellung „Maximale Sicherheit“)
  werden nach dem Schwärzen als neues Bild gerendert; die durchsuchbare
  Textebene wird ausschließlich aus dem bereits anonymisierten Text erzeugt.
* Versteckter Text (weiß, unsichtbar, außerhalb der Seite) wird erkannt und
  entfernt bzw. die Seite als Bild neu aufgebaut.

## 6. Die vier Prüfstufen

| Stufe | Was wird geprüft | Wirkung bei Fund |
|---|---|---|
| 1 – Textprüfung | Alle Originalbegriffe (inkl. jedes einzelnen Namensteils) im gesamten Text der fertigen Datei inkl. unsichtbarem Text, im Dateinamen und in der Textfassung; Dateistruktur (Metadaten, Anhänge …) | automatische Nachschwärzung, erneute Prüfung |
| 2 – Bildprüfung | Jede fertige Seite wird als Bild gerendert und per OCR gelesen – geprüft wird, was ein Mensch oder eine KI tatsächlich *sieht* | automatische Nachschwärzung genau dieser Stellen, erneute Prüfung |
| 3 – Wiederherstellungsversuche | Ein „Angreifer“ versucht die Daten zurückzuholen: Suche in allen PDF-Rohobjekten und entpackten Datenströmen (auch UTF-16/Hex), Textauslesen mit unabhängigen Programmen (pypdf, pdfminer, pdftotext), OCR aller eingebetteten Bilder in Originalauflösung (auch abgeschnittene/unsichtbare Bildteile), Abgleich der Dokumentkennung | betroffene Seite wird komplett als Bild neu aufgebaut, erneute Prüfung |
| 4 – Nachkontrolle | Erkennung läuft erneut über das Ergebnis | Hinweise zur Entscheidung durch den Nutzer |

Erst wenn Stufe 1 bis 3 nichts mehr finden, gilt die Prüfung als bestanden.

**Getestete Angriffe** (`tests/test_recovery.py`): Name versteckt als
ActualText in getaggten PDFs (Word), in abgeschnittenen Bildteilen, als weißer,
winziger, unsichtbarer oder unter einem Bild liegender Text, außerhalb der
Seite, mit weichem Trennstrich im Wort, in Formular-XObjects, Kommentaren,
Formularfeldern, Links, Metadaten, XMP, Lesezeichen, Dateianhängen,
Seitenbeschriftungen, ausgeblendeten Ebenen, JavaScript und in alten
Dateiversionen. Bei keinem Fall lässt sich der Name aus dem Ergebnis
wiederherstellen.

**Bekannte Restinformation:** Die *Breite* einer Schwärzung entspricht der
Länge des Originals (wie bei jeder Schwärzung auf Papier). Aus „M. M.“ und
dem Zusammenhang kann ein Mensch mit Vorwissen ggf. Rückschlüsse ziehen –
deshalb sollten auch Orte, Firmen und Daten konsequent ersetzt bleiben.

## 7. Selbstlernen

Das Programm verbessert sich mit jeder Akte – alles bleibt auf dem Rechner
(`%LOCALAPPDATA%\DocAnonymizer`):

* **Aus Ihren Entscheidungen:** Bestätigte bzw. hinzugefügte Begriffe werden
  künftig automatisch geschwärzt; zweimal abgelehnte Vorschläge des
  Sprachmodells werden nicht mehr vorgeschlagen; Vornamen aus bestätigten
  Personennamen erweitern die Namensliste.
* **Aus eigenen Fehlern:** Findet eine Prüfstufe etwas, das die Erkennung
  übersehen hatte, wird die Stelle automatisch nachgeschwärzt *und* als
  Trainingsbeispiel gespeichert („Selbstkorrektur“).
* **Nachtrainieren des Sprachmodells:** Sobald genug neue Beispiele vorliegen,
  trainiert das Programm das lokale spaCy-Modell automatisch im Hintergrund
  nach (zwischen zwei Akten, nie während einer laufenden). Ein Teil der
  Beispiele wird zurückgehalten; das neue Modell wird **nur übernommen, wenn
  es darauf nachweislich besser ist** als das bisherige. Manuell:
  Einstellungen → „Sprachmodell jetzt nachtrainieren“ oder
  `Modell_trainieren.bat`.
* Die Startseite zeigt den Lernfortschritt.

Die Lerndaten enthalten Textausschnitte aus den Dokumenten, bleiben auf dem
Rechner und können in den Einstellungen gelöscht werden.

## 8. Qualität – gemessen

Mit dem Test-Generator (`tests/corpus.py`) werden realistische, **frei
erfundene** Akten erzeugt (Klageschrift, Arztbrief, Mietvertrag,
Unfallbericht, E-Mail-Verlauf, Kündigung; deutsche und internationale
Namen), teils als Scan in vier Qualitätsstufen (Schiefstellung, Unschärfe,
Rauschen, starke JPEG-Kompression, blasser Druck). Da bekannt ist, welche
Daten eingefügt wurden, lässt sich exakt messen, ob etwas übrig bleibt:

```
python -m tests.evaluate --anzahl 24 --scan                      # digital + Scans
python -m tests.evaluate --anzahl 12 --nur-scans --qualitaet schlecht
```

Messwerte (Stand dieser Version, ohne GLiNER, 4 CPU-Kerne):

| Testlauf | Eingefügte Angaben | Entfernt | Prüfung |
|---|---|---|---|
| 24 gemischte Akten (⅓ Scans guter Qualität) | 292 | **100 %** | bestanden |
| 12 Scans mittlerer Qualität | 146 | **100 %** | bestanden |
| 12 Scans schlechter Qualität (150 dpi, verwaschen, schief) | 146 | **100 %** | bestanden |
| 12 Scans sehr schlechter Qualität (Fax-Niveau, 120 dpi) | 146 | 98,6 % | Seiten als „schlecht lesbar“ zur Sichtprüfung markiert |
| 1 Akte, 300 Seiten digital | 3 128 | **100 %** | bestanden, 4,7 min |
| 1 Akte, 100 Seiten Scan schlechter Qualität | 1 042 | **100 %** | 2 OCR-Fehltreffer zur Nutzerentscheidung, 6,5 min |

**Durchsatz** (Stapel aus 300 kurzen Dokumenten, 350 Seiten, davon ⅓ Scans;
4 CPU-Kerne): **7,1 Minuten** = ca. 42 Dokumente pro Minute bzw. ca. 2 500
pro Stunde, inklusive aller vier Prüfstufen. Die Dokumente werden auf mehrere
Prozesse verteilt; mit mehr Kernen steigt der Durchsatz entsprechend
(Arbeitsspeicher: ca. 2 GB + 0,5 GB je zusätzlichem Kern).

Wichtig: Wo die automatische Prüfung noch etwas findet, meldet das Programm
„nicht bestanden“ und fordert zur Entscheidung auf – es gibt kein stilles
Durchrutschen.

## 9. Grenzen – bitte beachten

* **Sehr schlechte Scans** (Fax, < 150 dpi, stark verwaschen): Keine
  Texterkennung kann solche Seiten zuverlässig lesen. Das Programm erkennt
  diese Seiten, markiert sie als „schlecht lesbar“ und fordert zur
  Sichtprüfung auf. Bitte dort Namen mit der Maus einrahmen.
* **Logos, Unterschriften, Stempel und Fotos auf Textseiten** werden
  automatisch entfernt (abschaltbar). **Handschrift und Unterschriften auf
  eingescannten Seiten** werden nicht automatisch erkannt – bitte mit der
  Maus einrahmen.
* **Indirekte Identifizierbarkeit** (z. B. „der einzige Kinderarzt im Ort“,
  seltene Berufe, Diagnosen) kann ein Programm nicht sicher beurteilen.
* Eine automatische Anonymisierung ersetzt nicht die abschließende
  Verantwortung des Anwenders.

## 10. Für Entwickler

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m spacy download de_core_news_lg
# Tesseract mit deutschem Sprachpaket installieren (Linux: apt install tesseract-ocr-deu)

python -m doc_anonymizer                 # Oberfläche starten
python -m doc_anonymizer check           # Installation prüfen
python -m doc_anonymizer anonymisieren a.pdf b.pdf --ausgabe out/   # ohne Oberfläche
python -m pytest                         # Tests
```

Aufbau des Pakets `doc_anonymizer/`:

| Modul | Aufgabe |
|---|---|
| `extract.py`, `ocr.py` | Text und Wortpositionen lesen, Seitenklassifikation, OCR |
| `detect/patterns.py` | Regeln für strukturierte Daten (IBAN, Telefon, Adressen …) |
| `detect/persons.py` | Regeln für Personennamen in deutschen Rechtstexten |
| `detect/ner.py`, `detect/pipeline.py` | spaCy/GLiNER und Zusammenführung |
| `registry.py` | Gruppierung, Weitergabe, Initialen/Platzhalter, Unsicherheiten |
| `redact.py` | echtes Schwärzen, neues PDF, Textfassung |
| `verify.py`, `forensics.py`, `process.py` | vier Prüfstufen, Wiederherstellungsversuche, automatische Nachbesserung |
| `learning.py`, `train.py` | Lernen aus Entscheidungen, Nachtrainieren |
| `jobs.py`, `parallel.py`, `web.py`, `static/` | Akten, parallele Hintergrundverarbeitung, Oberfläche |

Lizenzhinweis: PyMuPDF steht unter AGPL-3.0 (bzw. kommerzieller Lizenz von
Artifex). Für die interne Nutzung in einer Kanzlei ist das unproblematisch; bei
Weitergabe/Vertrieb des Programms sind die AGPL-Bedingungen zu beachten.
