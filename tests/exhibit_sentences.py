"""Sentences from pleadings with exhibit designations in the notations in use.

Collected from published court decisions, court leaflets on electronic
filing and practice guides (K/B, ASt/AG, BK/BB, Bf/BG, WK, Vk, SH/SV/NI,
NK/NB/D in patent cases, initials of law firms such as "HL", "rop", "B&B";
Austria "Beilage ./A", Switzerland "act. 3/5", arbitration "C-12").
All names are invented. Per sentence: what must stay readable, what must go.
"""

from __future__ import annotations

#: (sentence, designations and codes that stay, personal data that is removed)
SENTENCES: list[tuple[str, list[str], list[str]]] = [
    ("Beweis: Mietvertrag vom 01.03.2019, Anlage K 1.", ["Anlage K 1"], []),
    ("Der Kläger Markus Schneider legt den Kaufvertrag vom 12.05.2021 als Anlage K1 vor.",
     ["Anlage K1"], ["Schneider"]),
    ("Die Beklagte Erika Hoffmann verweist auf ihr Schreiben vom 06.05.2020 "
     "(Anlage B 1, Bl. 152 f. d. A.).", ["Anlage B 1", "Bl. 152 f. d. A."], ["Hoffmann"]),
    ("Wegen der Einzelheiten wird auf die Anlagen K 1 und K 2 sowie das Anlagenkonvolut K 3 "
     "Bezug genommen.", ["Anlagen K 1 und K 2", "Anlagenkonvolut K 3"], []),
    ("Die Rechnungen sind als Anlagen K 14 - K 24 vorgelegt, die Mahnungen als Anlagen K 25 "
     "bis K 27.", ["Anlagen K 14 - K 24", "Anlagen K 25 bis K 27"], []),
    ("Die E-Mail des Zeugen Thomas Wagner vom 03.02.2022 (Anl. K 5, S. 3) belegt dies.",
     ["Anl. K 5, S. 3"], ["Wagner"]),
    ("Die Liste der Werke ist in den Anlagen K 1a, K 1b und K 1c enthalten.",
     ["Anlagen K 1a, K 1b und K 1c"], []),
    ("Der Darlehensvertrag vom 14.11.2013 (Anlage K 1.1) wurde von Herrn Dr. Peter Becker "
     "unterzeichnet.", ["Anlage K 1.1"], ["Becker"]),
    ("Die Abtretungsvereinbarung vom 27.03.2022 liegt als Anlage K01 vor, die E-Mail an Frau "
     "Sabine Schulz als Anlage K001.", ["Anlage K01", "Anlage K001"], ["Schulz"]),
    ("Vgl. hierzu Bl. 172 f. GA sowie Anlage K116, K117, K118.",
     ["Bl. 172 f. GA", "Anlage K116, K117, K118"], []),
    ("Glaubhaftmachung: Eidesstattliche Versicherung des Geschäftsführers Hans Koch, als "
     "Anlage Ast. 1.", ["Anlage Ast. 1"], ["Koch"]),
    ("Der als Anlage ASt 2 vorgelegte Newsletter und die Screenshots ASt 4 bis ASt 8 zeigen "
     "die Werbung.", ["Anlage ASt 2", "ASt 4 bis ASt 8"], []),
    ("Die Antragstellerin verweist auf den Screenshot in Anlage AST 1, die Antragsgegnerin auf "
     "das Abmahnschreiben vom 01.02.2023 (Anlage AG 01).", ["Anlage AST 1", "Anlage AG 01"], []),
    ("Der Arbeitgeber informierte den Betriebsrat mit Schreiben vom 18.10.2017 "
     "(Anlage AGG 1 = Bl. 53 d. A.).", ["Anlage AGG 1 = Bl. 53 d. A."], []),
    ("Wegen des Anhörungsschreibens wird auf die Anlage A 1 zur Antragsschrift Bezug genommen.",
     ["Anlage A 1"], []),
    ("Die Berufungsklägerin stützt sich auf das Gutachten von Prof. Dr. Karl Richter vom "
     "24.05.2012 (Anlage BK 1).", ["Anlage BK 1"], ["Richter"]),
    ("Der Berufungsbeklagte Jürgen Wolf hat die ärztlichen Stellungnahmen als Anlage BB 1 "
     "eingereicht (vgl. BB S. 8 und LGU S. 12).", ["Anlage BB 1", "BB S. 8", "LGU S. 12"],
     ["Jürgen Wolf"]),
    ("Die Beschwerdeführerin hat den angefochtenen Beschluss als Anlage Bf 1 vorgelegt, die "
     "Beigeladene eine Übersicht als Anlage BG 1.", ["Anlage Bf 1", "Anlage BG 1"], []),
    ("Mit der Widerklage legt der Beklagte Stefan Schröder die Aufhebungsvereinbarung vom "
     "26.01.2016 als Anlage WK 1 vor.", ["Anlage WK 1"], ["Schröder"]),
    ("Der Lageplan ist als Anlage Vk 1 beigefügt; Verfügungsklägerin ist Frau Monika Neumann.",
     ["Anlage Vk 1"], ["Neumann"]),
    ("Die Streithelferin hat das Schreiben vom 29.03.2012 als Anlage SH 1 vorgelegt, weitere "
     "Unterlagen als Anlagen SV1 und SV2.", ["Anlage SH 1", "Anlagen SV1 und SV2"], []),
    ("Über die Löschungsanträge der Nebenintervenientin (Anlagenkonvolut NI 11) und der "
     "Antragsgegnerin (Anlagen rop 10a und rop 10b) ist noch nicht entschieden.",
     ["Anlagenkonvolut NI 11", "Anlagen rop 10a und rop 10b"], []),
    ("Das Streitpatent ist als Anlage NK 1 vorgelegt, die Entgegenhaltungen als NK 2, NK 3 "
     "und NK 11.", ["Anlage NK 1", "NK 2, NK 3 und NK 11"], []),
    ("Die Klägerin stützt sich auf die Druckschriften D1 und D2 (Übersetzung: D1.1), die "
     "Beklagte auf die Dokumente NB1 und HE 4.", ["D1 und D2", "D1.1", "NB1 und HE 4"], []),
    ("Das Klagepatent (Anlagen HL 5/5a) wurde durch Urteil vom 19.11.2021 (Anlage HL 1, "
     "nachfolgend: LGU) bestätigt.", ["Anlagen HL 5/5a", "Anlage HL 1"], []),
    ("Das Privatgutachten von Prof. Dr. Heinrich Braun ist als Anlage B&B 1 vorgelegt, die "
     "Patentschrift als Anlage B & B 10.", ["Anlage B&B 1", "Anlage B & B 10"], ["Braun"]),
    ("Der Registerauszug liegt als Anlage FBD 1, das Klagepatent als Anlage TW 1 und der "
     "Standard als Anlage WKS1 vor.", ["Anlage FBD 1", "Anlage TW 1", "Anlage WKS1"], []),
    ("Soweit sich die Beklagte zu 1) auf das Schreiben des Bauleiters Frank Zimmermann "
     "(Anlage B2-1) beruft, ist dies unerheblich; vgl. auch Anlagen B1 Nr. 1 bis Nr. 3.",
     ["Anlage B2-1", "Anlagen B1 Nr. 1 bis Nr. 3"], ["Zimmermann"]),
    ("Wegen der Versorgungsordnung wird auf das Anlagenkonvolut AK 6 zur Klageschrift "
     "(Bl. 50 ff. d. A.) verwiesen.", ["Anlagenkonvolut AK 6", "Bl. 50 ff. d. A."], []),
    ("Die Schiedsklägerin legt den Liefervertrag als Exhibit C-12 und das Rechtsgutachten als "
     "CL-3 vor; die Schiedsbeklagte verweist auf R-1 und RL-2.",
     ["Exhibit C-12", "CL-3", "R-1 und RL-2"], []),
    ("Der Kaufvertrag (Beilage ./A) und das Anbot des Herrn Josef Krüger (Beilage ./1) sind "
     "unstrittig.", ["Beilage ./A", "Beilage ./1"], ["Krüger"]),
    ("Die Klage vom 04.03.2024 (act. 1) nennt als Klagebeilage 5 (act. 3/5) die Rechnung; vgl. "
     "ferner KB 37-60 und KAB 17.", ["act. 1", "Klagebeilage 5 (act. 3/5)", "KB 37-60", "KAB 17"],
     []),
    ("Wie sich aus K 1 ergibt, schuldet die Beklagte den Betrag; entgegen B 3 wurde nicht "
     "gezahlt, vgl. auch K 4 bis K 6 und (B 12).", ["aus K 1 ergibt", "entgegen B 3",
                                                    "K 4 bis K 6", "(B 12)"], []),
    ("Beweis: Zeugnis der Frau Anna Hartmann, zu laden über die Klägerin; Zeugnis N.N. "
     "(Sachbearbeiter der Beklagten); Sachverständigengutachten.", ["Zeugnis N.N."],
     ["Hartmann"]),
    # Codes of the same shape that are no exhibits - and no personal data either.
    ("Das Fahrzeug Audi A4 mit dem Motor EA 189 ist im Kaufvertrag (Anlage K 2) bezeichnet; "
     "die Rechnung wurde im Format DIN A4 übersandt.", ["A4", "EA 189", "Anlage K 2", "DIN A4"],
     []),
    ("Die Klägerin Maria Lange ist in die Entgeltgruppe E 13 TV-L eingruppiert, ihr Ehemann "
     "wird nach A 13 besoldet; Nachweis: Anlage K 7.", ["E 13", "A 13", "Anlage K 7"],
     ["Maria Lange"]),
    ("Laut Attest von Dr. med. Bernd Werner (Anlage B 3) leidet der Beklagte an F32.1 und "
     "M54.5; anerkannt ist die BK 2108, er verfügt über das Sprachniveau B1.",
     ["Anlage B 3", "F32.1", "M54.5", "BK 2108", "Sprachniveau B1"], ["Werner"]),
]

#: Personal data in the same sentence as a designation is still removed.
PLATE_SENTENCE = ("Der Kläger befuhr mit seinem Pkw (amtliches Kennzeichen K-AB 1234) die "
                  "Landstraße in Richtung Norden; Lichtbilder: Anlage K 4.")
