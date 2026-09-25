# Skaldir: Implementierungsplan für MiMo Code

Stand: 24. September 2026. Dokumenttyp: Arbeitsauftrag mit Architekturvorschlägen und Abnahmekriterien.

## Auftrag

**Aktualisierte Nutzerprioritäten:** Zuerst [11-SKALDIR-HANDOFF.md](11-SKALDIR-HANDOFF.md) lesen. Dieser Handoff präzisiert das vollständige Zielbild und priorisiert Computer Use, schnelle Subtools, allgemeinen/Docker-Tool-Bau und The Voice mit JEV. Standard-Agent-Funktionen, Geschwindigkeit und UI-Polish bleiben zentrale Produktanforderungen.

Vergleiche diesen Plan mit der **momentanen Implementierung von Skaldir**. Ermittle anhand von Code, Konfiguration und ausführbaren Tests, was bereits funktioniert, nur teilweise vorhanden ist oder fehlt. Ergänze die sinnvollen fehlenden Teile, integriere sie in die vorhandene Architektur und teste die tatsächlichen Nutzerabläufe. Übernimm keine vorgeschlagene Architektur ungeprüft. Unpassende, redundante oder nachweislich schlechtere Vorschläge darfst und sollst du begründet verwerfen.

Das Ziel ist ein zuverlässigerer, ressourcenschonender Agent mit bedarfsgeladenem Wissen, überprüfbaren Fähigkeiten und kontrollierter Verbesserung aus Erfahrung. „Bester Agent“ ist eine Entwicklungsrichtung, kein ungeprüfter Produktclaim.

**Zentrale Nutzeridee:** Informationen sollen ähnlich wie Tools genutzt werden: finden, gezielt abrufen, anwenden. Wissen liegt in überschaubaren Markdown-Dateien; der Agent kann konkrete Zeilenbereiche lesen. Die gesamte Wissensbasis wird nicht bei jedem Modellaufruf in den Prompt geladen.

## Was über den Ist-Zustand bekannt ist

Aus bisherigen Projektgesprächen stammen die Namen Hermes, EXPAND, ACT, Voice, JEV, Treasury, persistente Goals, Subagents, Recovery und Memory. Diese Namen beschreiben das bisherige Konzept. Sie sind **kein Nachweis**, dass entsprechende Komponenten im aktuellen Repository existieren oder produktionsreif sind. Insbesondere darf „Hermes“ nicht ohne Repository-Beleg mit dem Nous-Research-Projekt gleichgesetzt werden.

Dieser Plan entstand ohne Zugriff auf Skaldirs aktuellen Quellcode. Alle Schnittstellen, Pfade, Budgets und Modulnamen sind Vorschläge, bis der Codeabgleich ihre Eignung bestätigt. Bestehende gleichwertige Mechanismen sollen erhalten und erweitert werden.

## So arbeitest du mit diesem Paket

1. Lies diese Datei vollständig, `01-IST-ABGLEICH.md` und `09-CODING-AGENT-GRUNDLAGEN.md`.
2. Ermittle Repository-Regeln, Einstiegspunkte, Stack, Runtime, Tests und vorhandene Änderungen.
3. Führe den Ist-Abgleich aus. Dokumentiere pro Anforderung den Codebeleg und den bisherigen Testnachweis.
4. Wähle den kleinsten konsistenten Umbau. Lies die zugehörigen Spezifikationsdateien erst dann, wenn du an dem Bereich arbeitest.
5. Prüfe und vervollständige zuerst Stufe 0 (Coding-Grundlagen), danach die anwendbaren Anforderungen der Stufen A bis C in kleinen, integrierten Änderungen.
6. Teste die veränderten Verträge sowie echte Abläufe einschließlich Fehlern, Wiederaufnahme und Kontextwechseln.
7. Führe Stufe D nur aus, wenn ihre Daten- und Evaluationsvoraussetzungen vorhanden sind. Eine ungetestete Selbstmodifikationsfunktion ist kein Ersatz für diese Voraussetzungen.
8. Liefere einen überprüfbaren Abschluss mit Änderungen, Testbefehlen, Resultaten, verworfenen Vorschlägen und verbleibenden Blockern.

Ein Audit allein erfüllt den Auftrag nicht. Nach dem Audit weiter implementieren und testen. Bei einer fehlenden externen Voraussetzung alle davon unabhängigen Arbeiten fortsetzen. Fehlende GPU, Credentials oder Desktop-Sitzung niemals durch erfundene Erfolgsberichte ersetzen.

## Dateien und Lesereihenfolge

| Datei | Inhalt | Wann lesen? |
|---|---|---|
| [11-SKALDIR-HANDOFF.md](11-SKALDIR-HANDOFF.md) | Aktuelles Gesamtziel, Nutzerprioritäten, berichteter Ist-Stand und 12 Produktabnahmen | Zuerst; danach nach Bedarf die Detaildateien |
| [01-IST-ABGLEICH.md](01-IST-ABGLEICH.md) | Inventur, Gap-Matrix, Entscheidungen, Migration | Vor Änderungen |
| [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md) | 24 Basisfähigkeiten, Tool-Verträge und 16 Audit-Szenarien | Direkt nach Datei 01; vor Advanced Tools und RSI |
| [02-INFORMATION-UND-MEMORY.md](02-INFORMATION-UND-MEMORY.md) | Markdown-Wissen, Suche, Zeilenabruf, Versionen, Herkunft, Memory | Bei Informations- und Kontextarbeit |
| [03-TOOLS-UND-KONTEXT.md](03-TOOLS-UND-KONTEXT.md) | Tool Discovery, programmatische Ausführung, Budgets, Routing, Voice | Beim Umbau der Agentenschleife |
| [04-LINUX-COMPUTER-USE.md](04-LINUX-COMPUTER-USE.md) | Linux-Backends, Beobachtungen, Aktionen, Verifikation, Recovery | Bei ACT und GUI-Skills |
| [05-LERNEN-UND-RSI.md](05-LERNEN-UND-RSI.md) | Skill-Lebenszyklus, kontrollierte Optimierung, Rollback | Nach Evidence- und Testgrundlagen |
| [06-TESTS-UND-ABNAHME.md](06-TESTS-UND-ABNAHME.md) | Testfälle, Messverfahren, Ablation, Definition of Done | Früh planen, während jeder Stufe anwenden |
| [07-QUELLEN-UND-ENTSCHEIDUNGEN.md](07-QUELLEN-UND-ENTSCHEIDUNGEN.md) | Recherchequellen, Übernahmegrenzen, bewusst zurückgestellte Ideen | Bei Architekturentscheidungen |
| [10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md) | Skaldir-Stile, Animationen, strukturierte Tool-Ausgabe, Interaktion und UI-Tests | Beim CLI-Umbau; Statusverträge zuerst |
| [08-LESEINDEX.md](08-LESEINDEX.md) | Exakte Abschnittsbereiche und Inhalts-Hashes dieser Planversion | Für gezielten Zeilenabruf |

Für dieses Paket kann ein Terminal-Agent gezielt Zeilen lesen:

```bash
rg -n 'INFO-READ|expected_revision|Freshness' 02-INFORMATION-UND-MEMORY.md
nl -ba 02-INFORMATION-UND-MEMORY.md | sed -n '60,130p'
```

Die Zahlen im Beispiel illustrieren das Verfahren und sind keine dauerhaft gültigen Anker. Erst suchen, dann den relevanten Bereich lesen. Bei Änderungen die aktuelle Dateiversion berücksichtigen.

`08-LESEINDEX.md` enthält zusätzlich die beim Verpacken ermittelten Zeilenbereiche und SHA-256-Hashes. Diese gelten für die gelieferten Dateien; nach Änderungen den Index regenerieren oder erneut nach Überschriften suchen. Der Index enthält keinen Hash von sich selbst.

## Verbindliche Qualitätsprinzipien

- Nutzerziel, Berechtigungen und Abnahmekriterien bleiben maßgeblich.
- Code- und Laufzeitbelege haben Vorrang vor alten Konzeptbeschreibungen.
- Für denselben Zustand genau eine maßgebliche Zuständigkeit: kein zweites Goal-, Memory- oder Tool-System neben dem vorhandenen aufbauen.
- Rohbeobachtungen, daraus abgeleitetes Wissen und normative Anweisungen unterscheiden.
- Informationen nur im benötigten Umfang in den aktiven Kontext laden; Originalbelege gezielt wiederfinden können.
- Einen Tool-Erfolg nicht automatisch mit fachlichem Erfolg gleichsetzen.
- Bei unbekanntem Ausgang einer schreibenden Aktion zuerst den Zustand abgleichen.
- Optimierungen anhand vollständig erledigter Aufgaben, Kosten und Fehlern bewerten.
- Keine automatischen Änderungen an Nutzerzielen, Berechtigungen, Secret-Broker oder maßgeblichem Erfolgstest durch den Lernpfad.
- Kein pauschaler Architekturwechsel, keine neue Datenbank oder zusätzliche Agentenebene ohne nachgewiesenen Bedarf.

## Umfang und Prioritäten

### Stufe 0: Coding-Grundlagen zuerst

Anforderungen: `BASE-01` bis `BASE-24`, einzeln spezifiziert in [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md).

MiMo muss jede Fähigkeit am aktiven Codepfad prüfen, Code- und Laufzeitnachweise in der Gap-Matrix festhalten und sinnvolle Lücken schließen. Kernfunktionen: Auftrag und Repository verstehen, suchen, lesen, gezielt editieren, Befehle ausführen, Tool-Ergebnisse verarbeiten, Fehler reproduzieren und Tests ausführen. Betriebsfunktionen umfassen unter anderem Änderungsschutz, Abbruch, Fehlerbehandlung, Kontext, Recovery und belegten Abschluss. Aufgabenabhängige Funktionen separat begründen.

Abnahme: `B01` bis `B16` in Datei 09; vorhandene Tests wiederverwenden. Stufe 0 ist mit A–C integrierbar, wird aber zuerst bewertet. Gleiche Mechanismen nicht doppelt implementieren. Grundlegende Datenverlust- und Ausführungsfehler vor Optimierung oder RSI beheben.

### Stufe A: Verlässlicher Zustand und bedarfsgeladene Informationen

Implementiere fehlende oder mangelhafte Teile dieser Stufe zuerst:

- `AUD-01`: evidenzbasierter Ist-Abgleich und eindeutige Zuständigkeiten.
- `OBS-01`: Run-, Tool-, Modell-, Kosten- und Evidenzereignisse.
- `INFO-01`: kleine, auffindbare Markdown-Wissenseinheiten mit Metadaten.
- `INFO-02`: begrenzte Informationssuche.
- `INFO-03`: versionsgebundener Abruf konkreter Zeilenbereiche.
- `INFO-04`: Aktualitätsprüfung und sichere Quellenreferenzen.
- `MEM-01`: Trennung von Arbeitszustand, Episoden, Fakten, Skills und Evidenz.
- `MEM-02`: expliziter Speicher für bereits versuchte, gescheiterte Ansätze.

Abnahme: Skaldir findet und liest relevantes Wissen gezielt, erkennt veraltete Referenzen und verliert beim Kontextwechsel weder Goal noch wesentliche offene Punkte.

### Stufe B: Effiziente Tool- und Kontextnutzung

- `TOOL-01`: Discovery statt vollständigem Tool-Katalog im Prompt.
- `TOOL-02`: große Ergebnisse als abrufbare Artefakte.
- `TOOL-03`: mehrstufige Tool-Aufrufe programmatisch bündeln, falls die vorhandene Runtime das sinnvoll unterstützt.
- `CTX-01`: begrenzter, strukturierter Arbeitskontext mit Originalreferenzen.
- `CTX-02`: providerspezifische Kontext- und Cache-Verträge.
- `CTX-03`: gültigkeitsabhängige Deduplizierung lesender Aufrufe.
- `ROUTE-01`: beobachtbare Modellwahl und Eskalation mit gemeinsamem Budget.
- `VOICE-01`: ereignisbezogene Überwachung ohne dauernde Vollkopie des Runs.

Abnahme: gleiche oder bessere Aufgabenerfüllung bei geringerem Aufwand auf passenden Vergleichsaufgaben. Ein kleinerer Prompt allein genügt nicht.

### Stufe C: Linux-Ausführung und geprüfte Fähigkeiten

- `ACT-01`: Capability-Erkennung und explizite Backend-Auswahl.
- `ACT-02`: aktuelle Beobachtungen, Fokusprüfung und exklusive Desktop-Ausführung.
- `ACT-03`: fachliche Postconditions und Evidence-Bindung.
- `ACT-04`: Reconciliation nach Timeout/Crash.
- `SKILL-01`: Skills mit Preconditions, Parametern, Ergebnisprüfung und Versionen.
- `SKILL-02`: geprüfte Wiederverwendung und Rückfall auf freie Planung.
- `LEARN-01`: belegbasierte Kandidatenerzeugung aus Erfahrung.

Abnahme: repräsentative Linux-Aufgaben laufen Ende zu Ende. Ein geprüfter Skill funktioniert mit neuen Eingaben und erkennt unpassende Umgebungen.

### Stufe D: Experimentelle Selbstverbesserung

- `RSI-01`: begrenzte Änderungen einzelner Komponenten, isolierte Evaluation, Versionshistorie und Rollback.
- `RSI-02`: Komplexitäts- und Kostenbewertung, getrennte Evaluationsaufgaben, nachvollziehbare Auswahl.
- Optional: adaptive Retrieval-Konfiguration, replaygestützte Auswahl von Experimenten, weitere Forschung.

Voraussetzung: belastbare Run-Daten, ausführbare Bewertung, stabile Ausgangsversion und klare Änderungsgrenzen. Fehlen diese Voraussetzungen, liefere die nötigen Datenverträge und den genauen offenen Schritt; aktiviere keine selbstverändernde Produktionsschleife.

## Was ausdrücklich angepasst oder verworfen werden darf

- Neue `info_*`-Tools, wenn bestehende Suche und Datei-Lesetools den gleichen Vertrag erfüllen.
- Ein eigener Vektorindex, wenn Volltextsuche und strukturierte Filter ausreichend gut sind.
- Graphiti, Mem0 oder Letta als vollständige zusätzliche Laufzeit, wenn das die vorhandene Architektur dupliziert.
- Ein persistenter Python-Kernel, wenn ein bestehender JavaScript-/Tool-Executor besser passt.
- Multiagent-Ausführung, wenn sie auf den tatsächlichen Aufgaben mehr kostet als nützt.
- Automatische Skills für seltene, instabile oder schlecht prüfbare Abläufe.
- Kontextkompression, deren Informationsverlust mehr Wiederholungen verursacht als sie Kosten spart.
- RSI-Verfahren ohne ausreichende unabhängige Tests.

Verwerfen bedeutet: Grund, betroffene Anforderung, vorhandene Alternative und Überprüfung dokumentieren. „Zu aufwendig“ ohne Bezug zum Nutzen ist keine vollständige Entscheidung. Eine wichtige funktionale Anforderung darf mit einer besseren vorhandenen Lösung erfüllt werden.

## Erwartete Arbeitsartefakte im Skaldir-Repository

Nutze vorhandene Dokumentationskonventionen. Falls keine existieren, sind folgende Namen Vorschläge:

```text
docs/skaldir-upgrade/implementation-audit.md
docs/skaldir-upgrade/decisions.md
docs/skaldir-upgrade/validation-report.md
```

Die Arbeitsartefakte enthalten aktuelle Codepfade, Änderungsstände und Testresultate. Sie ersetzen keine Implementierung. Keine Platzhalter als erfüllte Features kennzeichnen.

## Abschlussbericht von MiMo Code

Am Ende mindestens angeben:

1. Was bereits vorhanden war und wiederverwendet wurde.
2. Welche Lücken implementiert wurden und welche Nutzerabläufe dadurch funktionieren.
3. Welche Vorschläge verworfen oder angepasst wurden und warum.
4. Welche Tests tatsächlich ausgeführt wurden, mit Ergebnis und relevanten Belegen.
5. Welche Vergleiche Qualität, Tokens, Kosten und Laufzeit zeigen.
6. Welche Teile mangels externer Voraussetzungen ungetestet oder unvollständig bleiben.
7. Wie neue Funktionen aktiviert, beobachtet und zurückgerollt werden können.

Keine Behauptung „vollständig getestet“, wenn nur Unit-Tests oder simulierte Adapter gelaufen sind. Die Einhaltung eines Plans ist weniger wichtig als eine kohärente und belegbar verbesserte Implementierung.

## CLI-Erweiterung

Zusätzlicher Nutzerauftrag: [10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md), Anforderungen `CLI-01` bis `CLI-12`. Mit der bestehenden UI vergleichen, sinnvoll integrieren und testen. Zuerst ehrliche Statusanzeige und klare Tool-Ergebnisse, danach Layout und Animationen. Diese Erweiterung ersetzt keine Backend-Reparatur.
