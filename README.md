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
* **Was man sieht, ist was man bekommt:** Ins Ergebnis kommt nur, was auf
  den Seiten sichtbar ist – versteckter Text (unter Balken, weiß,
  unsichtbar, außerhalb der Seite), Kameradaten von Fotos (EXIF/GPS) und
  abgeschnittene Bildteile werden nicht übernommen.
* Es werden **keine Daten ins Internet** gesendet – auch nicht an KI-Dienste.

---

## 1. Installation (Windows)

Zwei Wege, die dasselbe Setup einrichten (Anleitung für Nutzer:
`ANLEITUNG.txt`).

**Weg A: eine Zeile in PowerShell** – ohne die Windows-Warnung „Der Computer
wurde durch Windows geschützt“. Startmenü → „PowerShell“ öffnen, Zeile
einfügen, Eingabetaste:

```powershell
irm https://github.com/pmanlukas/doc-anomyzer/releases/latest/download/install.ps1 | iex
```

Die Zeile führt `installer/install.ps1` aus: Die Setup.exe des neuesten
Release wird ohne Browser geladen, ihre SHA-256-Prüfsumme mit der
veröffentlichten verglichen, das Setup ohne Administratorrechte eingerichtet
und das Programm gestartet. Dieselbe Zeile aktualisiert eine vorhandene
Installation.

* **Warum erscheint keine Warnung?** Windows zeigt den SmartScreen-Hinweis
  für Dateien, die ein Browser als „aus dem Internet“ markiert hat. Ein
  Download über die Kommandozeile trägt diese Markierung nicht. Diese Prüfung
  entfällt also bewusst: Wer die Zeile ausführt, vertraut diesem Repository.
  Die Prüfsumme belegt nur, dass die Datei vollständig und unverändert
  angekommen ist – nicht, von wem sie stammt.
* **Voraussetzung:** ein öffentlich abrufbares Release (siehe
  „Veröffentlichen“ unten). Bei einem privaten Repository findet die Zeile
  nichts.
* **Hilft nicht** auf Rechnern, die nur signierte Programme zulassen (Smart
  App Control unter Windows 11, AppLocker/WDAC in Firmennetzen) – das Skript
  meldet dann, dass Windows den Start verhindert hat.
* Für Administratoren (Umgebungsvariablen): `DOC_ANONYMIZER_AUTOMATISCH=1`
  richtet ohne Fenster und ohne Programmstart ein und beendet PowerShell bei
  einem Fehler mit Exitcode 1; `DOC_ANONYMIZER_QUELLE=<Adresse>` nennt einen
  eigenen Download-Ort mit Setup.exe, `.sha256` und `install.ps1`.

**Weg B: `Dokumenten-Anonymisierer-Setup.exe` per Doppelklick** – wie ein
normales Windows-Programm, auch ohne Internet:

1. Doppelklick auf die Setup.exe. Warnt Windows („Der Computer wurde durch
   Windows geschützt“): „Weitere Informationen“ → „Trotzdem ausführen“
   (das Setup ist nicht code-signiert).
2. „Installieren“ → „Fertigstellen“ (Haken „Programm jetzt starten“).
3. Künftig: Symbol „Dokumenten-Anonymisierer“ auf dem Desktop/im Startmenü.

Eigenschaften des Setups:

* **komplett offline** – Python 3.12, alle Pakete, das deutsche Sprachmodell
  `de_core_news_lg`, Tesseract und die OCR-Sprachdateien (deu/eng/osd) sind
  enthalten; bei der Installation wird nichts heruntergeladen,
* **ohne Administratorrechte** – Programmordner
  `%LOCALAPPDATA%\Programs\Dokumenten-Anonymisierer`, keine UAC-Abfrage,
* **eigenes Programmfenster, kein Konsolenfenster** – das Programm öffnet
  sich wie eine normale Anwendung (Microsoft Edge im App-Modus, ohne
  Adressleiste und Tabs, mit einem eigenen, abgeschotteten Profil). Ohne
  Edge öffnet es sich als Tab im Standardbrowser. Ein zweiter Klick auf das
  Symbol holt nur das Fenster wieder hervor,
* **Beenden = Fenster schließen** – das Programm beendet sich kurz danach
  selbst, nie während einer laufenden Verarbeitung (alternativ: „Beenden“
  oben rechts),
* Startprobleme erscheinen als verständliche Windows-Meldung; Details stehen
  in `%LOCALAPPDATA%\DocAnonymizer\logs\programm.log` (ohne Inhalte und
  ohne Dateinamen der Dokumente),
* **Update** = neue Setup.exe ausführen oder die PowerShell-Zeile
  wiederholen (ein laufendes Programm wird vorher beendet).
  **Deinstallation** über „Apps & Features“.

Nutzerdaten liegen **getrennt** vom Programm und bleiben bei einem Update
erhalten: Akten (Arbeitskopien der Originale), Einstellungen, Lernstand und
das nachtrainierte Sprachmodell in `%LOCALAPPDATA%\DocAnonymizer`, die
Ergebnisse in `%USERPROFILE%\Anonymisiert` (änderbar). Beim Deinstallieren
fragt das Programm, ob die Arbeitsdaten mit gelöscht werden sollen; die
Ergebnisse bleiben in jedem Fall erhalten.

Die Setup.exe wird von GitHub Actions gebaut und auf einem frischen Windows
getestet (`.github/workflows/windows-setup.yml`, `installer/`): stille
Installation, Selbsttest, Anonymisierung von Test-PDFs über die Kommandozeile
und über das laufende Programm (inkl. Prüfung, dass ohne Zugangsschlüssel
nichts erreichbar ist), Zweitstart, Beenden, selbstständiges Beenden, das
echte Programmfenster in Edge, Update bei laufendem Programm, Deinstallation
und die Einrichtung über die PowerShell-Zeile (Windows PowerShell 5.1 mit der
Standardrichtlinie „Restricted“, gegen einen lokalen Download-Ort – auch mit
einer Datei, die nicht zur Prüfsumme passt und deshalb nicht gestartet wird).
Alle Bausteine, die in das Setup wandern, werden beim Bau gegen feste
SHA-256-Prüfsummen bzw. die Windows-Signatur des Herstellers geprüft; die
enthaltenen Paketversionen stehen in `enthaltene_pakete.txt` im
Programmordner. Download: Actions → Lauf „Windows-Setup“ → Artefakt
„Dokumenten-Anonymisierer-Setup“ (enthält auch die SHA-256-Prüfsumme der
Setup.exe zum Vergleichen: `Get-FileHash Dokumenten-Anonymisierer-Setup.exe`).

**Veröffentlichen:** Ein Release entsteht nur absichtlich – durch ein
Versions-Tag (`git tag v0.2.0 && git push origin v0.2.0`) oder über Actions →
„Windows-Setup“ → „Run workflow“ mit dem Haken „Als Release veröffentlichen“.
Der Job `release` hängt die getestete Setup.exe, ihre Prüfsumme, `install.ps1`
und `ANLEITUNG.txt` an das Release `v<Version>`. Eine veröffentlichte Version
wird nie überschrieben: Eine geänderte Setup.exe braucht eine neue
Versionsnummer (`doc_anonymizer/__init__.py`). Danach führt der Job
`release-test` die veröffentlichte Zeile auf einem frischen Windows aus
(Download vom Release ohne Anmeldung, Prüfsumme, Einrichtung, Selbsttest) und
prüft, dass sie die neue Version einrichtet. Die PowerShell-Zeile lädt von
`…/releases/latest/download/`; das funktioniert ohne Anmeldung nur bei einem
öffentlichen Repository.

**Alternative: ZIP + `Installieren.bat`** (lädt Python, Tesseract und das
Sprachmodell bei der Installation aus dem Internet; ZIP erzeugen mit
`python tools/build_zip.py`):

1. ZIP entpacken, im entpackten Ordner doppelt auf `Installieren.bat`
   klicken (SmartScreen-Hinweis wie oben).
2. Start mit `Start.bat` bzw. der Desktop-Verknüpfung (ein Konsolenfenster
   bleibt geöffnet, solange das Programm läuft).
3. Optional: `Zusatzmodell_installieren.bat` installiert das KI-Modell
   GLiNER (ca. 2 GB, nur in dieser Variante).

## 2. Bedienung

1. **Start:** Doppelklick auf „Dokumenten-Anonymisierer“ (Desktop oder
   Startmenü). Das Programmfenster öffnet sich. Es dient nur als Anzeige –
   das Programm ist ausschließlich auf diesem Computer erreichbar
   (`127.0.0.1`) und nur für das Fenster, das es selbst geöffnet hat.
2. **Dokumente ablegen:** PDF-Dateien **oder einen ganzen Ordner** in das
   Fenster ziehen (oder „Dateien auswählen“ / „Ordner auswählen“). Das Lesen
   startet sofort und lässt sich jederzeit **abbrechen**. Alles, was
   gemeinsam abgelegt wird, ist eine „Akte“: Dieselbe Person erhält in allen
   Dokumenten dieselben Initialen. Auch hunderte Dokumente sind kein Problem
   (siehe Abschnitt 8). Ein defektes oder passwortgeschütztes PDF hält die
   übrigen nicht auf – es wird am Ende mit dem Grund aufgeführt.
3. **Prüfen:**
   * **Offene Fragen:** nur die Stellen, bei denen sich das Programm **nicht
     sicher** ist – mit Textzusammenhang und einem Vorschlag. Jeweils
     „Schwärzen“ / „Lesbar lassen“ wählen oder alle Vorschläge übernehmen.
   * **Alle Funde:** alle gefundenen Daten mit ihrer Ersetzung. Schalter aus
     = nicht schwärzen. Ersetzungen lassen sich direkt ändern.
   * **Bereiche:** automatisch erkannte Unterschriften, Stempel, Logos.
   * Fehlt etwas: „Begriff hinzufügen“ oder auf der Seitenansicht **mit der
     Maus einen Rahmen ziehen**.
4. **Anonymisieren und speichern:** ein Klick (der Speicherort steht daneben
   und lässt sich dort ändern). Ergebnis je Dokument:
   * `…_anonymisiert.pdf` – das anonymisierte Dokument,
   * `…_anonymisiert.txt` – Textfassung in Lesereihenfolge (ideal zum
     Einfügen in KI-Werkzeuge),
   * `…_Pruefbericht.html` – Prüfbericht (enthält keine Originaldaten).

   **Wo liegt was?** (`<Speicherort>` ist anfangs `C:\Users\<Name>\Anonymisiert`)

   ```
   <Speicherort>\<Datum Akte>\            nur geprüfte, bestandene Ergebnisse –
                                          der Ordner kann als Ganzes weitergegeben werden
   <Speicherort>\VERTRAULICH_nicht_weitergeben\<Datum Akte>\
       Zuordnungstabelle.html             Original → Ersetzung (hebt die Anonymisierung auf!)
       NICHT BESTANDEN - nicht weitergeben\   Ergebnisse, die eine Prüfung nicht bestanden haben
   ```

   Ein Dokument wird zunächst im privaten Arbeitsordner erzeugt und erst
   nach bestandener Prüfung in den Ergebnisordner gelegt. Dateinamen werden
   mit anonymisiert (`Klage Mustermann.pdf` → `Klage M_anonymisiert.pdf`);
   lässt sich ein Name im Dateinamen nicht sicher entfernen, heißt die Datei
   neutral `Dokument_003_anonymisiert.pdf`.

   Die Ergebnisseite zeigt alle Dokumente (Problemfälle zuerst), „Ordner
   öffnen“ führt direkt zu den Dateien, „Alle als ZIP“ lädt alle bestandenen
   Ergebnisse auf einmal herunter.

   Der Standard-Speicherort ist bewusst nicht „Dokumente“ oder „Desktop“,
   weil diese oft per OneDrive in die Cloud synchronisiert werden. Das
   Programm warnt, wenn ein Cloud-Ordner gewählt wird.
5. **Nachbessern:** Meldet die Nachkontrolle noch Auffälligkeiten oder fällt
   Ihnen selbst etwas auf, führt „Nachbessern“ zurück in die Prüfung. Beim
   erneuten Anonymisieren werden die bisherigen Ergebnisdateien der Akte
   **ersetzt** – es bleibt kein veralteter Stand liegen.

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
| Unterschriften, Handschrift, Stempel | handschriftlich auf Scans, gezeichnet mit Unterschrift-Pads/PDF-Werkzeugen | `[Unterschrift]`, `[Handschrift]`, `[Stempel]` |
| Logos, Fotos | kleine Bilder auf Textseiten | `[Bild]` |

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
6. **Unterschriften-Erkennung** (OpenCV, `doc_anonymizer/signatures.py`):
   Auf Scans wird die Seite in Tinte und Papier zerlegt; sicher gelesener
   Drucktext und lange gerade Linien (Tabellen) werden herausgerechnet. Die
   übrigen Striche werden zu „Tinteninseln“ zusammengefasst und nach
   nachvollziehbaren Merkmalen bewertet: farbige Tinte (Kugelschreiber), kein
   lesbarer Text, Grußformel/„gez.“/Rolle („Rechtsanwältin“) in der Nähe,
   Größe, Strichdichte, Strich-Komplexität und Höhe des längsten
   Einzelstrichs (Druckbuchstaben sind auch verwaschen höchstens ca. 3,5 mm
   hoch). Schwungstriche unter der Unterschrift werden mit erfasst. Auf
   digitalen Seiten werden gezeichnete Unterschriften als Häufung von
   Kurvenpfaden erkannt und als Vektorgrafik entfernt.

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
* **Nur Sichtbares wird übernommen.** Jede Seite wird zweimal gerendert –
  mit und ohne Text. Wo das Entfernen eines Wortes keinen Bildpunkt ändert,
  war das Wort nicht sichtbar: unter einem schwarzen Balken (die klassische
  „Schwärzung“ per Rechteck), in weißer oder unsichtbarer Schrift,
  weggeschnitten oder außerhalb der Seite. Solche Wörter fließen in die
  Erkennung ein, gelangen aber nie in das Ergebnis (weder in die Textebene
  noch in die `.txt`); die Seite wird als Bild neu aufgebaut. Eine fremde,
  unsichtbare Textebene kann auch nicht verdecken, was das Seitenbild zeigt –
  es zählt, was die eigene Texterkennung sieht.
* **Keine Original-Bilddaten.** Jede Seite, die nach dem Schwärzen noch ein
  Bild enthält, wird aus ihrer Darstellung neu aufgebaut. Kameradaten in
  Fotos (EXIF: Aufnahmeort, Gerät, Besitzer, Zeitpunkt, Vorschaubild),
  XMP-Daten an Bildern und abgeschnittene oder außerhalb der Seite liegende
  Bildteile sind danach nicht mehr vorhanden.
* **Gedrehter Text** (senkrechte Randzeilen von Briefbögen und
  Gehaltsabrechnungen) wird über seine ganze Länge entfernt.

## 6. Die vier Prüfstufen

| Stufe | Was wird geprüft | Wirkung bei Fund |
|---|---|---|
| 1 – Textprüfung | Alle Originalbegriffe (inkl. jedes einzelnen Namensteils, Zahlen OCR-tolerant: „DEO2“ = „DE02“) im gesamten Text der fertigen Datei inkl. unsichtbarem Text, im Dateinamen und in der Textfassung; **Reste von IBANs**, auch wenn die Erkennung sie nicht kannte (z. B. „DE02 1203“ neben einer Schwärzung); Dateistruktur (Metadaten, Anhänge …) | automatische Nachschwärzung, erneute Prüfung |
| 2 – Bildprüfung | Jede fertige Seite wird als Bild gerendert und per OCR gelesen – geprüft wird, was ein Mensch oder eine KI tatsächlich *sieht* | automatische Nachschwärzung genau dieser Stellen, erneute Prüfung |
| 3 – Wiederherstellungsversuche | Ein „Angreifer“ versucht die Daten zurückzuholen: Suche in allen PDF-Rohobjekten und entpackten Datenströmen (auch UTF-16/Hex), Textauslesen mit unabhängigen Programmen (pypdf, pdfminer, pdftotext), Suche nach verdecktem/unsichtbarem Text und nach Original-Bilddaten im Ergebnis, Abgleich der Dokumentkennung | betroffene Seite wird komplett als Bild neu aufgebaut, erneute Prüfung |
| 4 – Nachkontrolle | Erkennung läuft erneut über das Ergebnis | Hinweise zur Entscheidung durch den Nutzer |

Erst wenn Stufe 1 bis 3 nichts mehr finden, gilt die Prüfung als bestanden.
Eine Prüfstufe, die nicht ausgeführt werden konnte (abgeschaltet, technisches
Problem), wird als **„nicht ausgeführt“** ausgewiesen – nie als bestanden.

**Getestete Angriffe** (`tests/test_recovery.py`): Name versteckt als
ActualText in getaggten PDFs (Word), in abgeschnittenen Bildteilen, als weißer,
winziger, unsichtbarer oder unter einem Bild liegender Text, außerhalb der
Seite, mit weichem Trennstrich im Wort, in Formular-XObjects, Kommentaren,
Formularfeldern, Links, Metadaten, XMP, Lesezeichen, Dateianhängen,
Seitenbeschriftungen, ausgeblendeten Ebenen, JavaScript und in alten
Dateiversionen. Bei keinem Fall lässt sich der Name aus dem Ergebnis
wiederherstellen.

**Versteckte Inhalte, die keine Erkennung kennen kann**
(`tests/test_hidden_content.py`, mit einem frei erfundenen Wort): Text unter
einem schwarzen Balken, in weißer und in unsichtbarer Schrift, außerhalb der
Seite, Kameradaten (EXIF) in einem großen und einem kleinen Foto, der
abgeschnittene Teil eines Bildschirmfotos, eine irreführende unsichtbare
Textebene über einem Bild mit Namen, senkrecht geschriebener Name. Nichts
davon ist im Ergebnis auffindbar – weder in der Datei noch in der Textfassung.

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
* **Unterschriften:** Lässt der Nutzer eine erkannte Fläche sichtbar
  („nicht schwärzen“), ist das ein Gegenbeispiel; Flächen, die beim
  Anonymisieren bestehen bleiben, gelten als bestätigt. Ab 20 Entscheidungen
  ersetzt ein daraus gelerntes Modell (logistische Regression über dieselben
  Merkmale) die festen Gewichte. Gespeichert werden nur Zahlen, keine Bilder.
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

Messwerte (Version 0.2.0, ohne GLiNER, 4 CPU-Kerne, genaue OCR-Sprachdateien
`tessdata_best` wie im Setup; ohne jede Nutzerentscheidung – unsichere
Vorschläge bleiben dabei unbeantwortet):

| Testlauf | Eingefügte Angaben | Entfernt | Prüfung |
|---|---|---|---|
| 24 gemischte Akten (⅓ Scans guter Qualität) | 292 | **100 %** | bestanden |
| 36 gemischte Akten, zweiter Datensatz | 438 | **100 %** | bestanden |
| 12 Scans mittlerer Qualität | 146 | **100 %** | bestanden |
| 12 Scans schlechter Qualität (150 dpi, verwaschen, schief) | 146 | **100 %** | bestanden |
| 12 Scans sehr schlechter Qualität (Fax-Niveau, 120 dpi) | 146 | 96,6–98,6 % (je nach Stichprobe) | Seiten als „schlecht lesbar“ zur Sichtprüfung markiert |
| 1 Akte, 300 Seiten digital | 3 128 | **100 %** | bestanden, 3,7 min, alle Seiten bleiben Text |
| 1 Akte, 100 Seiten Scan schlechter Qualität | 1 042 | 99,8 % (2 von der Texterkennung verstümmelte Kleinschreibungen) | **als „nicht bestanden“ gemeldet** – Sichtprüfung nötig; 9,4 min |

**Durchsatz** (4 CPU-Kerne, inklusive aller vier Prüfstufen):

* 90 kurze Dokumente (105 Seiten, davon ⅓ Scans): **3,2 Minuten** ≈ 29 Dokumente
  pro Minute; 90 von 90 bestanden.
* 300 kurze Dokumente in *einer* Akte (350 Seiten, davon ⅓ Scans):
  **ca. 12,5 Minuten** ≈ 24 Dokumente pro Minute bzw. ca. 1 400 pro Stunde;
  300 von 300 bestanden. (Je größer die Akte, desto mehr Suchbegriffe prüft
  jede Prüfstufe – hier 4 673 je Dokument.)

Die Zeiten hängen stark vom Rechner und von der Texterkennung ab; die im
Setup enthaltenen genauen OCR-Sprachdateien sind etwa halb so schnell wie
die einfachen. Auf demselben Rechner ist diese Version durch die
zusätzlichen Prüfungen (Sichtbarkeit, Bilddaten) rund 6 % langsamer als
die Vorversion (189 s statt 178 s für die 90 Dokumente). Die Dokumente
werden auf mehrere Prozesse verteilt; mit mehr Kernen steigt der Durchsatz
entsprechend (Arbeitsspeicher: ca. 2 GB + 0,5 GB je zusätzlichem Kern).

**Unterschriften** (`tests/signature_docs.py`: erfundene Briefe mit
synthetischen Unterschriften in blauer/schwarzer Tinte, nach Grußformeln,
„i. V.“ und auf Unterschriftslinien, teils quer über dem getippten Namen).
Gemessen auf 36 Dokumenten, die bei der Entwicklung **nicht** verwendet
wurden; „gefunden“ heißt: mindestens 80 % der Tintenfläche liegen in der
Schwärzung.

| Scanqualität | Farbe | Schwarz-weiß | Fehlalarme |
|---|---|---|---|
| gut | 41/42 | 41/42 | 0 |
| mittel | 41/42 | 41/42 | 0 |
| schlecht | 38/42 | 40/42 | 0 |
| **gesamt** | | **242/252 = 96 %** | **0 auf 252 Seiten** |

Auf echten Dokumenten (ein Scan mit Unterschrift und Gerichtssiegel, ein
Word-PDF mit Logo) wurden Unterschrift und Siegel gefunden, ohne Fehlalarm.

Wichtig: Wo die automatische Prüfung noch etwas findet, meldet das Programm
„nicht bestanden“, legt das Ergebnis getrennt ab und fordert zur Entscheidung
auf. Was aber keine Texterkennung lesen kann (sehr schlechte Scans), kann
auch die Prüfung nicht finden – solche Seiten werden deshalb als „schlecht
lesbar“ markiert und müssen selbst angesehen werden.

## 9. Grenzen – bitte beachten

* **Sehr schlechte Scans** (Fax, < 150 dpi, stark verwaschen): Keine
  Texterkennung kann solche Seiten zuverlässig lesen. Das Programm erkennt
  diese Seiten, markiert sie als „schlecht lesbar“ und fordert zur
  Sichtprüfung auf. Bitte dort Namen mit der Maus einrahmen.
* **Logos, Unterschriften, Stempel und Fotos auf Textseiten** werden
  automatisch entfernt (abschaltbar). **Unterschriften auf Scans** werden in
  den Tests zu 96 % gefunden (siehe Abschnitt 8) – sehr kleine Kürzel
  (Paraphen) oder blasse Bleistift-Unterschriften können fehlen. Die erkannten
  Flächen stehen in der Prüfansicht oben („Automatisch geschwärzte
  Bereiche“); bitte Unterschriften kurz kontrollieren und Fehlendes mit der
  Maus einrahmen. Handschriftliche Randnotizen ohne Grußformel werden nur
  bei farbiger Tinte zuverlässig erkannt.
* **Seiten mit Bildern werden zu Bildseiten:** Enthält eine Seite nach dem
  Schwärzen noch ein Bild (Foto, Briefbogen als Hintergrundbild, behaltenes
  Logo), wird sie als Bild neu aufgebaut (200 dpi, mit durchsuchbarer
  Textebene). Das ist der Preis dafür, dass keine Original-Bilddaten in das
  Ergebnis gelangen; reine Textseiten bleiben gestochen scharfer Text.
* **Sehr kurze Namen** (zwei Buchstaben, z. B. „Li“) werden als Teil des
  vollen Namens geschwärzt, von der Prüfung aber nicht einzeln gesucht (zu
  viele Fehltreffer) – bei solchen Namen bitte selbst kurz hinsehen.
* **Indirekte Identifizierbarkeit** (z. B. „der einzige Kinderarzt im Ort“,
  seltene Berufe, Diagnosen) kann ein Programm nicht sicher beurteilen.
* Eine automatische Anonymisierung ersetzt nicht die abschließende
  Verantwortung des Anwenders.

## 10. Schutz des Programms und der Daten auf dem Rechner

Die Oberfläche läuft technisch als lokaler Webserver – deshalb ist sie
besonders abgesichert:

* **Nur dieser Computer:** Der Server lauscht ausschließlich auf `127.0.0.1`
  (exklusiv gebundener Port), andere Rechner im Netz erreichen ihn nicht.
* **Nur dieser Benutzer, nur dieses Fenster:** Bei jedem Start entsteht ein
  zufälliger Zugangsschlüssel. Ohne ihn ist nichts erreichbar – auch nicht
  für andere Benutzerkonten auf demselben Rechner (Terminalserver) oder für
  andere Programme. Das Fenster wird mit einem **Einmal-Ticket** eingelassen;
  der Schlüssel selbst steht nie in einer Adresse, einer Befehlszeile oder
  im Browserverlauf. Ein zweiter Start erkennt die eigene Instanz per
  Challenge/Response – ein fremdes Programm auf demselben Port bekommt den
  Schlüssel nicht.
* **Abgeschottetes Fenster:** Das Programmfenster nutzt ein eigenes
  Edge-Profil im Datenordner des Programms: keine Browser-Erweiterungen
  (Übersetzer, „KI-Assistenten“ …), keine Synchronisation, und ein
  Cookie-Speicher, den keine andere Webseite je sieht.
* **Keine fremden Webseiten:** Anfragen anderer Seiten werden abgewiesen
  (Prüfung von `Host`, `Origin`, `Sec-Fetch-Site`, eigener Anfrage-Header),
  die Seiten lassen sich nicht einbetten, und eine strenge
  Content-Security-Policy erlaubt weder Inline-Skripte noch externe Inhalte.
* **Feindliche Dateien:** Übergroße Seiten und Bilder werden begrenzt,
  Dokumente mit mehr als 5 000 Seiten abgelehnt, die Texterkennung hat ein
  Zeitlimit. Dokumente werden in getrennten Arbeitsprozessen verarbeitet –
  stürzt einer ab, betrifft das nur sein Dokument. Hochgeladene Dateien
  werden unter neutralen Namen gespeichert.
* **Ordnung auf der Platte:** Arbeitskopien der Originale liegen nur im
  privaten Datenordner und werden nach einstellbarer Frist (Standard: 30 Tage
  nach der letzten Bearbeitung) automatisch gelöscht. Temporäre Dateien
  (Seitenbilder für die Texterkennung) liegen in einem eigenen privaten
  Ordner, der bei jedem Start und Ende geleert wird. Die Lerndaten (Begriff
  und wenige Wörter Zusammenhang) bleiben bis „Gelerntes löschen“.
* **Während der Verarbeitung** ist die Prüfung gesperrt: Anonymisiert wird
  genau der Stand, den man beim Klick gesehen hat.

**Bekannte Grenzen:** Wird das Programm nicht im eigenen Fenster, sondern als
Tab im Standardbrowser benutzt (Edge fehlt oder die Einstellung wurde
geändert), teilt es sich dessen Cookie-Speicher und Erweiterungen – auf
gemeinsam genutzten Rechnern bitte das eigene Fenster verwenden. Vor einem
Benutzer mit Administratorrechten oder Schadsoftware im eigenen
Benutzerkonto kann kein lokales Programm schützen. Die Setup.exe ist nicht
code-signiert: Beim Doppelklick erscheint der SmartScreen-Hinweis, und
Rechner, die nur signierte Programme zulassen, starten sie gar nicht (auch
nicht über die PowerShell-Zeile aus Abschnitt 1).

## 11. Für Entwickler


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
| `launcher.py` | Start des installierten Programms (ohne Konsole, Einzelinstanz, Selbstbeenden) |
| `installer/` | Windows-Setup (Inno Setup), Build- und Testskripte für GitHub Actions |

Lizenzhinweis: Der eigene Quelltext steht unter der MIT-Lizenz (`LICENSE`).
Das Setup enthält außerdem PyMuPDF unter AGPL-3.0 (bzw. kommerzieller Lizenz
von Artifex). Für die interne Nutzung in einer Kanzlei ist das unproblematisch;
wer das Setup weitergibt oder veröffentlicht, muss die AGPL-Bedingungen
einhalten – insbesondere den vollständigen Quelltext zugänglich machen. Die
Releases verweisen deshalb auf den Quelltext der jeweiligen Version in diesem
Repository; die Lizenztexte der enthaltenen Pakete liegen im Programmordner
(`python\Lib\site-packages\*.dist-info`).
