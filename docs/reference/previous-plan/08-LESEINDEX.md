# Leseindex und Revisionen

Stand: 25. September 2026. Index für diese lokale Paketkopie. Physische Zeilen, 1-basiert und inklusive; nach Änderungen erneut erzeugen. Index selbst ausgeschlossen.

## 00-MIMO-START.md

[00-MIMO-START.md](00-MIMO-START.md) · 173 Zeilen · `sha256:529bc29a8ef8b9343d0bc84b0432fb2f2f61f15f85099e20e5d59bd769e5a199`

| Abschnitt | Zeilen |
|---|---|
| Skaldir: Implementierungsplan für MiMo Code | 1–173 |
| Auftrag | 5–14 |
| Was über den Ist-Zustand bekannt ist | 15–20 |
| So arbeitest du mit diesem Paket | 21–33 |
| Dateien und Lesereihenfolge | 34–60 |
| Verbindliche Qualitätsprinzipien | 61–73 |
| Umfang und Prioritäten | 74–131 |
| Stufe 0: Coding-Grundlagen zuerst | 76–83 |
| Stufe A: Verlässlicher Zustand und bedarfsgeladene Informationen | 84–98 |
| Stufe B: Effiziente Tool- und Kontextnutzung | 99–111 |
| Stufe C: Linux-Ausführung und geprüfte Fähigkeiten | 112–123 |
| Stufe D: Experimentelle Selbstverbesserung | 124–131 |
| Was ausdrücklich angepasst oder verworfen werden darf | 132–144 |
| Erwartete Arbeitsartefakte im Skaldir-Repository | 145–156 |
| Abschlussbericht von MiMo Code | 157–170 |
| CLI-Erweiterung | 171–173 |

## 01-IST-ABGLEICH.md

[01-IST-ABGLEICH.md](01-IST-ABGLEICH.md) · 119 Zeilen · `sha256:579f47ec2b3af38143eed0f36a330a73892b319e824246fb39df691225e7c43a`

| Abschnitt | Zeilen |
|---|---|
| Ist-Abgleich, Architekturentscheidungen und Migration | 1–119 |
| 1. Repository orientieren | 5–16 |
| 2. Komponenten nach Funktion zuordnen | 17–35 |
| 3. Mindestabläufe vor Änderungen nachvollziehen | 36–47 |
| 4. Gap-Matrix | 48–65 |
| 5. Entscheidungsverfahren | 66–90 |
| 6. Ausgangsmessung | 91–104 |
| 7. Migration bestehender Daten | 105–116 |
| 8. Grenze des Codeabgleichs | 117–119 |

## 02-INFORMATION-UND-MEMORY.md

[02-INFORMATION-UND-MEMORY.md](02-INFORMATION-UND-MEMORY.md) · 268 Zeilen · `sha256:b91c10f8edf2d09365ecec196091e2594c37ededc9477ef9db647fbea6192200`

| Abschnitt | Zeilen |
|---|---|
| Informationen als abrufbare Fähigkeiten und dauerhaftes Memory | 1–268 |
| 1. Ziel und Kernentscheidung | 5–12 |
| 2. Datenarten und Zuständigkeiten | 13–25 |
| 3. Vorschlag für die Wissensablage | 26–51 |
| 4. Minimale Metadaten | 52–81 |
| 5. INFO-SEARCH: begrenzte Entdeckung | 82–133 |
| 6. INFO-READ: genaue Zeilen aus einer bestimmten Version | 134–190 |
| Verbindliche Semantik | 167–180 |
| Warum Dateiversionen notwendig sind | 181–190 |
| 7. Freshness: Verfügbarkeit und Aktualität unterscheiden | 191–207 |
| 8. Memory schreiben und konsolidieren | 208–225 |
| 9. Failure Memory | 226–242 |
| 10. Kontextwechsel und lange Sessions | 243–256 |
| 11. Abnahmekriterien | 257–268 |

## 03-TOOLS-UND-KONTEXT.md

[03-TOOLS-UND-KONTEXT.md](03-TOOLS-UND-KONTEXT.md) · 211 Zeilen · `sha256:953ad58fc8e377a9b0d85be57a5636b80d296fc1402278631af43bd61a1694d9`

| Abschnitt | Zeilen |
|---|---|
| Effiziente Tools, Agentenkontext und Modellaufrufe | 1–211 |
| 1. Laufzeitbeobachtbarkeit zuerst | 5–23 |
| 2. TOOL-DISCOVERY: kleine Grundausstattung | 24–48 |
| Zu vermeiden | 39–48 |
| 3. TOOL-RESULTS: Ausgabe kontrollieren | 49–71 |
| 4. PROGRAMMATIC-TOOLS: wiederholte Inferenz vermeiden | 72–101 |
| Anforderungen an den Executor | 86–101 |
| 5. CONTEXT-ASSEMBLY: aktiver Kontext als berechnete Ansicht | 102–124 |
| 6. PROVIDER-CONTEXT: keine universelle Cache-Annahme | 125–147 |
| 7. READ-DEDUP: identische lesende Arbeit vermeiden | 148–161 |
| 8. MODEL-ROUTING: austauschbar und messbar | 162–177 |
| 9. VOICE: gezielte Supervision | 178–192 |
| 10. Subagents | 193–200 |
| 11. Abnahme | 201–211 |

## 04-LINUX-COMPUTER-USE.md

[04-LINUX-COMPUTER-USE.md](04-LINUX-COMPUTER-USE.md) · 187 Zeilen · `sha256:b4a096bfee89d472736dfe39271e54437abaf0c68b3a473820c6083d71381771`

| Abschnitt | Zeilen |
|---|---|
| Linux Computer Use, überprüfte Aktionen und Recovery | 1–187 |
| 1. Integration in die vorhandene Runtime | 5–21 |
| 2. Linux-Fähigkeiten tatsächlich erkennen | 22–48 |
| Wayland | 37–40 |
| X11 | 41–44 |
| Accessibility | 45–48 |
| 3. Beobachtungsvertrag | 49–70 |
| 4. Aktionsvertrag | 71–96 |
| 5. Kurze Aktionssequenzen statt blindem Makro | 97–117 |
| 6. Exklusive Steuerung einer Desktop-Sitzung | 118–129 |
| 7. Ergebnisprüfung über JEV | 130–145 |
| 8. Recovery bei unbekanntem Ergebnis | 146–166 |
| 9. Geeignete Linux-Abnahmeszenarien | 167–178 |
| 10. Nicht automatisch hinzufügen | 179–187 |

## 05-LERNEN-UND-RSI.md

[05-LERNEN-UND-RSI.md](05-LERNEN-UND-RSI.md) · 193 Zeilen · `sha256:0800c0351038d344e98b29c47e3beacaba6e88cc77adaa6c3ff648b461a2117d`

| Abschnitt | Zeilen |
|---|---|
| Geprüfte Skills und kontrollierte Selbstverbesserung | 1–193 |
| 1. Drei getrennte Veränderungsebenen | 5–16 |
| 2. SKILL-CONTRACT | 17–40 |
| 3. Lebenszyklus | 41–52 |
| 4. Aus Erfahrung einen Skill-Kandidaten erzeugen | 53–80 |
| 5. Wiederverwendung | 81–93 |
| 6. RSI: begrenzter Optimierungslauf | 94–119 |
| 7. Komponentenweise Änderungen | 120–140 |
| 8. Auswahl und Generalisierung | 141–156 |
| 9. Was unverändert maßgeblich bleibt | 157–168 |
| 10. Rollback und Auslieferung | 169–176 |
| 11. Optional und erst später | 177–184 |
| 12. Abnahme | 185–193 |

## 06-TESTS-UND-ABNAHME.md

[06-TESTS-UND-ABNAHME.md](06-TESTS-UND-ABNAHME.md) · 251 Zeilen · `sha256:10f6d3daafb6c7b66017c1787daf32eb5e12f911b73fe58bae78ae3dbffd73fe`

| Abschnitt | Zeilen |
|---|---|
| Tests, Vergleichsmessung und Definition of Done | 1–251 |
| 1. Teststrategie | 5–16 |
| 1a. Coding-Grundlagen vor Optimierung | 17–22 |
| 2. Informationsabruf und Markdown | 23–43 |
| 3. Memory, Kontextwechsel und Konsistenz | 44–60 |
| 4. Tools, Executor und Kosten | 61–82 |
| 5. Linux, Evidence und Recovery | 83–101 |
| 6. Skills und Lernen | 102–120 |
| 7. Eigene Aufgaben-Suite | 121–145 |
| 8. Vergleichsprotokoll | 146–157 |
| 9. Metriken | 158–182 |
| 10. Ablation | 183–195 |
| 11. Abnahme und Abschlussstatus | 196–220 |
| Erforderlich für die implementierten Stufen | 198–211 |
| Nicht als vollständig erledigt ausgeben | 212–220 |
| 12. Vorlage für den Validierungsbericht | 221–244 |
| 13. CLI-UI und UX | 245–248 |
| 14. Gesamtprodukt nach aktualisierten Nutzerprioritäten | 249–251 |

## 07-QUELLEN-UND-ENTSCHEIDUNGEN.md

[07-QUELLEN-UND-ENTSCHEIDUNGEN.md](07-QUELLEN-UND-ENTSCHEIDUNGEN.md) · 81 Zeilen · `sha256:75fb3a4358e4f0edc1ddd31847a3759c1ae90d7168858eb8d2a3a3efea7dc79d`

| Abschnitt | Zeilen |
|---|---|
| Recherchegrundlage und Übernahmeentscheidungen | 1–81 |
| 1. Priorisierte Architekturquellen | 5–19 |
| 2. Tool- und Kontextquellen | 20–29 |
| 3. Selbstverbesserung | 30–43 |
| 4. Computer Use und Linux | 44–53 |
| 5. Modellkandidaten aus der Recherche | 54–59 |
| 6. Bewusst verworfene oder bedingte Ideen | 60–76 |
| 7. Ergänzende Recherche: Coding-Grundlagen | 77–81 |

## 09-CODING-AGENT-GRUNDLAGEN.md

[09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md) · 201 Zeilen · `sha256:b57cfb315fa32010be33b9ade15ad6affce71acfefaba276bd4403f08f507d3c`

| Abschnitt | Zeilen |
|---|---|
| Coding-Agent-Grundlagen: verbindlicher MiMo-Audit | 1–201 |
| 1. Auftrag an MiMo | 5–12 |
| 2. Was die Recherche tatsächlich zeigt | 13–27 |
| 3. Prüfmatrix: 24 Fähigkeiten | 28–60 |
| 4. So sollen grundlegende Tool Calls funktionieren | 61–123 |
| 4.1 Gemeinsamer Ausführungspfad | 63–74 |
| 4.2 Minimaler logischer Vertrag | 75–109 |
| 4.3 Logische Fähigkeiten statt Tool-Wildwuchs | 110–123 |
| 5. Eigene Dateien, Zeilen und Kontext | 124–131 |
| 6. Ausführbare Audit-Szenarien | 132–156 |
| 7. Ergebnisformat und Umsetzung | 157–186 |
| 8. Bewusst keine allgemeinen Pflichtfeatures | 187–201 |

## 10-CLI-UI-UX-UPGRADE.md

[10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md) · 261 Zeilen · `sha256:5d5cccb8f8921b25a3e954a24f7752eede177e0a50ba94856f95f1f85d28273e`

| Abschnitt | Zeilen |
|---|---|
| Skaldir CLI: UI- und UX-Upgrade für MiMo | 1–261 |
| 1. Auftrag und Zielbild | 5–12 |
| 2. Visuelle Sprache | 13–24 |
| 3. Hauptansichten | 25–116 |
| 3.1 Start: nützlicher Kontext statt interner Platzhalter | 27–44 |
| 3.2 Während eines Computer-Use-Auftrags | 45–62 |
| 3.3 Fehler verständlich und handlungsfähig darstellen | 63–78 |
| 3.4 Tool-Ergebnisse kurz und bei Bedarf vollständig | 79–94 |
| 3.5 Abschluss | 95–116 |
| 4. Animationen mit Persönlichkeit | 117–151 |
| 4.1 Drei auswählbare Stile | 119–128 |
| 4.2 Animationsmotive | 129–143 |
| 4.3 Bewegungsbudget | 144–151 |
| 5. Strukturierter Output und Interaktion | 152–183 |
| 5.1 Drei Informationsebenen | 154–163 |
| 5.2 Eingabe und Tastatur | 164–183 |
| 6. Datenvertrag: UI darf keine Zustände erfinden | 184–202 |
| 7. Terminaltechnik und Robustheit | 203–214 |
| 8. Umsetzung in vier Schritten | 215–232 |
| Schritt A: Wahrheit und Lesbarkeit | 217–220 |
| Schritt B: Layout und Eingabe | 221–224 |
| Schritt C: Skaldir-Stil | 225–228 |
| Schritt D: Betriebsfestigkeit | 229–232 |
| 9. Anforderungen und Abnahmetests | 233–256 |
| 10. Definition of Done und Bericht an den Nutzer | 257–261 |

## 11-SKALDIR-HANDOFF.md

[11-SKALDIR-HANDOFF.md](11-SKALDIR-HANDOFF.md) · 583 Zeilen · `sha256:3c28a0f0b90e839d89ac7222341974bd3cb754c15b1537939c32c839fd4f5a7b`

| Abschnitt | Zeilen |
|---|---|
| Skaldir — vollständiger Produkt- und Implementierungshandoff | 1–583 |
| 0. Verbindlicher Auftrag und Beleggrenze | 5–14 |
| 1. Was der Nutzer tatsächlich möchte | 15–43 |
| 1.1 Produktversprechen | 17–28 |
| 1.2 Nicht verhandelbare Nutzerpräferenzen | 29–43 |
| 2. Prioritäten und Arbeitsreihenfolge | 44–61 |
| 3. Gemeldeter Ist-Zustand: erhalten, aber erneut prüfen | 62–117 |
| 3.1 Repository und historische Evidenz | 64–78 |
| 3.2 Architektur laut Handoff | 79–102 |
| 3.3 Berichtete Änderungen dieser MiMo-Runde | 103–117 |
| 4. Standard-Agent-Funktionen: vollständige Grundlage | 118–135 |
| 5. HIGH PRIORITY: Computer Use vollständig und störungsarm | 136–188 |
| 5.1 Drei ausdrücklich unterscheidbare Ausführungsmodi | 138–147 |
| 5.2 Vollständiger Funktionsumfang | 148–162 |
| 5.3 Browserlebenszyklus: Fensterflut dauerhaft verhindern | 163–179 |
| 5.4 Computer-Use-Abnahme | 180–188 |
| 6. HIGH PRIORITY: schnelle Subtools und COMPOSE | 189–224 |
| 6.1 Begriffe sauber trennen | 191–199 |
| 6.2 Ausführbare Subtools statt bloßer Promptvorlagen | 200–212 |
| 6.3 Schneller Pfad mit kontrolliertem Fallback | 213–224 |
| 7. HIGH PRIORITY: allgemeiner Tool Builder | 225–243 |
| 7.1 Erwarteter Lebenszyklus | 227–232 |
| 7.2 Konkrete Integrationslücken aus dem Verlauf | 233–239 |
| 7.3 Abnahme | 240–243 |
| 8. HIGH PRIORITY: Docker Tool Builder | 244–259 |
| 9. HIGH PRIORITY: The Voice mit JEV | 260–298 |
| 9.1 Gewünschte Rolle | 262–267 |
| 9.2 Zuständigkeiten | 268–278 |
| 9.3 Trigger und Rückkopplung | 279–290 |
| 9.4 Evidence-Verträge | 291–298 |
| 10. Geschwindigkeit und Token-Effizienz | 299–320 |
| 10.1 Messung statt „ultrafast“ als Behauptung | 301–306 |
| 10.2 Optimierungsreihenfolge | 307–320 |
| 11. UI-Polish: Skaldir erkennbar und verständlich | 321–338 |
| 12. Memory, Knowledge und Kontext | 339–365 |
| 12.1 Informationen wie Tools nutzen | 341–351 |
| 12.2 Gedächtnisarten und Lebenszyklus | 352–359 |
| 12.3 Kontinuität | 360–365 |
| 13. Lernen, Skills und kontrollierte RSI | 366–373 |
| 14. Weitere gemeinsam besprochene Fähigkeiten | 374–384 |
| 15. Offene Fehler: präzise Diagnose statt Schuldzuweisung ans Modell | 385–401 |
| 16. Arbeits- und Testverfahren für MiMo | 402–416 |
| 17. Verbindliche Produktabnahme | 417–435 |
| 18. Konkreter Start der nächsten Sitzung | 436–445 |
| 19. Dokumente dieses Pakets | 446–463 |
| 20. Anhang: die `/`-Befehle im Chat (Slash-Vokabular) | 464–583 |
| Nicht gebaute Befehle (ehrlich gemeldet, nie ausgeführt) | 574–583 |

