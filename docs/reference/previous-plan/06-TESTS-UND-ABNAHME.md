# Tests, Vergleichsmessung und Definition of Done

Diese Datei konkretisiert die Abnahme aller Anforderungen. Testnamen und Ablage an den vorhandenen Stack anpassen.

## 1. Teststrategie

Teste beobachtbares Verhalten und wichtige Fehlergrenzen. Keine Tests, die nur die interne Implementierung nachbauen oder unveränderte Boilerplate absichern.

Nutze drei Ebenen:

1. **Deterministische Vertragsprüfungen:** Zeilen, Revisionen, Scopes, Statusübergänge, Budget und Idempotenz.
2. **Integration:** Echter Store, Registry, Dispatcher, Provider-Adapter und Recovery arbeiten zusammen. Externe Dienste dürfen gezielt durch kontrollierte Fixtures ersetzt werden.
3. **Ende-zu-Ende:** Ein realer Agent erledigt repräsentative Aufgaben in der unterstützten Umgebung. Linux-Computer-Use benötigt mindestens einen tatsächlichen Desktop-Ablauf.

Für geänderte Verhaltensverträge Tests implementieren oder vorhandene erweitern. Nach bestandenen passenden Tests nur dann verbreitern, wenn Änderungen, Fehler oder offene Risiken es rechtfertigen. Projektseitig verpflichtende Checks vollständig ausführen.

## 1a. Coding-Grundlagen vor Optimierung

Die verbindlichen Basisfälle `B01` bis `B16` stehen vollständig in [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md). Sie prüfen `BASE-01` bis `BASE-24`: reale Fehlerbehebung, mehrere Dateien, begrenztes Lesen, Änderungsschutz, Prozesse, Tool-Verträge, Prüfqualität, Setup, Recovery, Nutzersteuerung, Kontext, Rechte und Abschlussbelege.

Diese Tests gehören zur Gesamtabnahme. Pro BASE-ID Codepfad und Nachweisgrad erfassen; ohne Laufzeitbeleg keine bestandene Basisabnahme melden. Vorhandene I-/T-/A-Tests können dieselben Verträge abdecken und werden referenziert statt dupliziert. Bedingte Fälle am unterstützten Produktumfang entscheiden. Für den Kern zusätzlich mindestens einen echten Bugfix und eine Änderung über mehrere Dateien mit dem konfigurierten Laufzeitmodell prüfen; rein gemockte Harness-Tests genügen dafür nicht.

## 2. Informationsabruf und Markdown

| Test-ID | Fall | Erwartetes Verhalten |
|---|---|---|
| I01 | Kleine Datei, Abruf erster/mittlerer/letzter Zeilen | Exakter Inhalt, 1-basierte inklusive Grenzen |
| I02 | Leerzeilen, Frontmatter, Unicode, LF und CRLF | Dokumentierte Zeilensemantik, keine Verschiebung durch Anzeigeumbruch |
| I03 | Leere Datei, kein oder vorhandener finaler Zeilentrenner | Konsistente Gesamtzeilenzahl |
| I04 | Null/negative/verkehrte Grenzen, Bereich hinter EOF | Strukturierter und dokumentierter Fehler oder explizite Kürzung |
| I05 | Sehr großer Bereich bei kleinem Ausgabebudget | Begrenzte Ausgabe, korrekte tatsächlich gelieferte Grenzen, Fortsetzung möglich |
| I06 | Einzelne Zeile größer als Budget | Kein stiller Datenverlust; expliziter Fehler oder Fragmentvertrag |
| I07 | Datei zwischen Suche und Lesen geändert | `STALE_REFERENCE`, keine falsche Übereinstimmung |
| I08 | Datei während des Abrufs geändert | Hash und Inhalt gehören zum selben Snapshot |
| I09 | Historische Version gelesen | Historischer Status klar, keine automatische aktuelle Gültigkeit |
| I10 | Datei umbenannt oder gelöscht | ID-/Indexzustand konsistent, nachvollziehbares Nichtgefunden-Ergebnis |
| I11 | Pfad außerhalb Scope, Traversal oder Symlink-Ausbruch | Kein unberechtigter Inhalt in Suche, Snippets oder Lesen |
| I12 | Gleiche Suchbegriffe in zwei Projekten | Richtiger Scope; keine versehentliche Wissensvermischung |
| I13 | Notiz enthält eingebettete Aufforderung zur Regelumgehung | Inhalt bleibt Datenquelle, keine neue Berechtigung oder Priorität |
| I14 | Index veraltet gegenüber Original | Abweichung erkannt oder Index aktualisiert; kein falscher Quellenbeleg |

Für I01 bis I08 eine kleine nachvollziehbare Fixture verwenden. Erwartete Zeilen manuell oder unabhängig vom produktiven Parser definieren, damit ein Parserfehler nicht von identischem Testcode reproduziert wird.

## 3. Memory, Kontextwechsel und Konsistenz

| Test-ID | Fall | Erwartetes Verhalten |
|---|---|---|
| M01 | Alte Projektentscheidung wird ersetzt | Aktuelle Aussage verwendet, Historie weiterhin als alt erkennbar |
| M02 | Eintrag basiert auf anschließend geänderter Datei | Neuprüfung beziehungsweise Veraltet-Markierung |
| M03 | Agent behauptet einen nicht ausgeführten Export | Keine bestätigte Erfolgserinnerung ohne Evidence |
| M04 | Zwei gleichzeitige Writer ändern denselben Eintrag | Kein stiller Lost Update; Konflikt/Version behandelt |
| M05 | Migration zweimal ausgeführt | Keine Duplikate oder beschädigten Referenzen |
| M06 | Wesentliches Detail fehlt in Zusammenfassung | Original lässt sich gezielt finden und abrufen |
| M07 | Mehrere Kontextwechsel in einem Run | Goal, offene Kriterien, unbekannte Aktionen und nächste Schritte bleiben korrekt |
| M08 | Ein Fehlversuch wird ohne neuen Zustand wiederholt | Wiederholung erkannt; neue Information oder andere Strategie erforderlich |
| M09 | Voraussetzungen nach Fehlversuch sinnvoll geändert | Erneuter Versuch bleibt möglich |
| M10 | Information wird gemäß Nutzer-/Aufbewahrungsregel gelöscht | Originale, Indizes und abgeleitete Ansichten folgen definiertem Löschvertrag |

Zusätzlich Recall auf unbekannten Fragen messen: Wurde die richtige Quelle gefunden? Wurde sie gelesen? Wurde sie korrekt angewandt? Diese drei Fehlerarten getrennt auswerten.

## 4. Tools, Executor und Kosten

| Test-ID | Fall | Erwartetes Verhalten |
|---|---|---|
| T01 | Viele installierte Tools, einfache Aufgabe | Nur erforderliche Definitionen im aktiven Kontext |
| T02 | Discovery liefert ähnliches, aber ungeeignetes Tool | Auswahl-/Validierungsfehler sichtbar; kein falscher Aufruf |
| T03 | Tool-Schema oder Version ändert sich | Veralteter Vertrag erkannt |
| T04 | Großes Ergebnis mit Pagination/Artefakt | Begrenzte Ausgabe, Vollständigkeit und Zugriffspfad klar |
| T05 | Programmatischer und direkter Call mit gleichem Scope | Gleiche Berechtigungs- und Schemavalidierung |
| T06 | Ein Call in einem Batch scheitert | Erfolgreiche Teilresultate und Fehler nachvollziehbar |
| T07 | Abbruch während einer Pipeline | Geordneter Abbruch; bereits erfolgte Mutationen erhalten ihren Status |
| T08 | Unbegrenzte Schleife oder zu großer Fan-out | Executor erzwingt Laufzeit-/Aufruf-/Speichergrenzen |
| T09 | Budget läuft während Worker-/Memory-Arbeit aus | Gemeinsame Grenze greift, keine unsichtbaren Zusatzläufe |
| T10 | Gültiger identischer Read wird wiederholt | Ergebnis kann wiederverwendet werden |
| T11 | Gleiche Argumente, aber anderer Zustand oder Scope | Cache wird nicht fälschlich wiederverwendet |
| T12 | Provider meldet Cache-Tokens als Input-Teilmenge | Keine Doppelzählung |
| T13 | Kosten/Usage fehlen in Providerantwort | Als unbekannt oder geschätzt markiert |
| T14 | Modellwechsel bei offenem Tool-Aufruf | Kein kaputtes Tool-Call-Paar, konsistente Übergabe |
| T15 | Voice-Trigger ohne relevantes Problem | Beobachten/kein Eingriff möglich; Budget sichtbar |

Performancevergleich: identische Datenaufgabe einmal über bisherige Aufrufkette und einmal über neue Bündelung ausführen. Datenkorrektheit unabhängig vom Modell prüfen. Gesamtkosten inklusive Ausführung, Wiederholungen und Verifikation zählen.

## 5. Linux, Evidence und Recovery

| Test-ID | Fall | Erwartetes Verhalten |
|---|---|---|
| A01 | Unterstützte Sitzung und eine nicht verfügbare Fähigkeit | Zutreffender Capability-Report |
| A02 | Fokus wechselt vor Tastatureingabe | Kein blindes Schreiben ins falsche Fenster |
| A03 | Screenshot oder Accessibility-Zustand veraltet | Neue Beobachtung oder explizite Ablehnung |
| A04 | Skalierung, Geometrie oder Dialog verändert sich | Koordinaten nicht ungeprüft weiterverwenden |
| A05 | Postcondition nach gesendetem Klick nicht erreicht | Kein fachlicher Erfolg gemeldet |
| A06 | Alter und neuer Worker konkurrieren | Nur aktueller Besitzer kann eingeben |
| A07 | Timeout direkt nach schreibender Aktion | Unbekannter Ausgang persistiert; vor Retry Zustand abgleichen |
| A08 | Absturz und Resume nach bestätigtem Ergebnis | Keine doppelte Aktion; korrekter Fortschritt |
| A09 | Prüfung war grün, Artefakt danach verändert | Alte Evidence erfüllt aktuelles Kriterium nicht automatisch |
| A10 | Echte Datei-/Tabellen-/Exportaufgabe | Inhalt und aktuelle Darstellung passend zum Ziel geprüft |

API-/Formular-Mutationen zunächst gegen eine kontrollierte Testumgebung ausführen. Reale Kommunikation, Veröffentlichung oder sonstige externe Aktionen nur innerhalb des tatsächlichen Nutzerauftrags und vorhandener Projektregeln testen.

Eine Mock-Sitzung beweist die State Machine; sie beweist keine Wayland-/X11-Kompatibilität. Im Bericht beide Ebenen getrennt nennen.

## 6. Skills und Lernen

| Test-ID | Fall | Erwartetes Verhalten |
|---|---|---|
| S01 | Skill mit neuen Parametern | Korrektes Ergebnis ohne memorisierte Beispielwerte |
| S02 | Falsche App-/Tool-Version oder fehlende Voraussetzung | Kontrollierter Fallback oder Ablehnung |
| S03 | Skill-Ausführung erfolgreich, Ergebnisprüfung scheitert | Kein Erfolg und keine positive Lernerfahrung |
| S04 | Neue Version aktiviert während alter Run läuft | Versionsbindung bleibt konsistent |
| S05 | Regression nach neuer Skill-Version | Alte funktionierende Version wiederherstellbar |
| S06 | Kandidat ohne ausreichende Evidence | Keine automatische aktive Fähigkeit |
| R01 | Optimierer schlägt Verbesserung eines einzelnen Moduls vor | Änderung bleibt im erlaubten Bereich |
| R02 | Kandidat versucht Bewertung/Budget/Permissions zu ändern | Vertrag verhindert Übernahme |
| R03 | Trainingsergebnis besser, zurückgehaltene Aufgaben schlechter | Keine unbegründete Promotion |
| R04 | Größerer Prompt ohne messbaren Nutzen | Änderung wird verworfen oder vereinfacht |
| R05 | Experimentbudget erschöpft | Sauberer Stopp, produktive Version bleibt konsistent |
| R06 | Wiederholt verwendeter Holdout | Nicht als völlig unabhängiger Nachweis ausgegeben |

R-Tests nur für implementierte RSI-Funktionalität ausführen. Wenn RSI wegen fehlender Voraussetzungen nicht aktiviert wird, den Stand und die noch fehlenden Belege offen dokumentieren.

## 7. Eigene Aufgaben-Suite

Beginne mit einem kleinen Smoke-Set, das die tatsächlich geänderten Abläufe trifft. Danach für belastbare Vergleiche ein repräsentatives Set ausbauen. Ein möglicher Zielumfang sind 60 Aufgaben:

| Bereich | Vorgeschlagener Umfang | Beispiele |
|---|---:|---|
| CLI/Coding | 12 | Fehler untersuchen, gezielte Reparatur, Build/Test |
| Browser | 12 | Informationen sammeln, kontrolliertes Formular, Download |
| Linux-Desktop | 12 | Datei bearbeiten, Tabelle ändern, Export prüfen |
| Recherche/Artefakte | 12 | Quellen abgleichen, Bericht erzeugen, Ergebnis überprüfen |
| Memory/Recovery | 12 | Entscheidung nach Kontextwechsel, veralteter Fakt, Resume |

Dies ist ein Vorschlag, keine Vorgabe, 60 oberflächliche Tests zu generieren. Reale wiederkehrende Skaldir-Aufgaben priorisieren. Ein kleineres aussagekräftiges Set mit klarer Unsicherheit ist besser als viele redundante Fälle.

Für jede Aufgabe festhalten:

```text
task_id, category, initial_state, user_request
constraints, allowed_resources, budget
success_predicate, evidence_collection
reset_procedure, known_external_dependencies
```

Der Agent bekommt die Nutzeraufgabe und erlaubte Ressourcen, nicht versteckte Erwartungsantworten oder intern zurückgehaltene Bewertungsdaten.

## 8. Vergleichsprotokoll

- Modell, Provider, Reasoning-Einstellung und Tool-Zugriff zwischen Baseline und Kandidat gleich halten, sofern genau diese Variable nicht untersucht wird.
- Ausgangszustand für jeden Lauf sauber zurücksetzen.
- Gleiche Aufgaben paarweise vergleichen.
- Bei stochastischen Ergebnissen mehrere Läufe nutzen, soweit das Budget reicht; Einzelbeispiele als solche kennzeichnen.
- Kalte und warme Cache-Zustände separat ausweisen oder kontrollieren.
- Memory-freie Ausgangsläufe und Läufe mit bereits erworbener Erfahrung trennen.
- Entwicklung, Auswahl und finale Evaluation unterscheiden.
- Externe Ausfälle separat erfassen, aber nicht still aus der Erfolgsstatistik entfernen.
- Alle Fehlversuche und abgebrochenen Kandidaten in der Kostenrechnung berücksichtigen.

## 9. Metriken

Primär:

```text
strict_success_rate = vollständig erfüllte Aufgaben / gestartete Aufgaben
cost_per_verified_success = Kosten aller zugehörigen Läufe / verifizierte Erfolge
```

Bei null Erfolgen ist die zweite Kennzahl nicht sinnvoll endlich; nicht als null ausgeben. Kosten unbekannt: Kennzahl unvollständig markieren.

Zusätzlich:

- Teilpunkte getrennt vom vollständigen Erfolg.
- Median und obere Quantile der Laufzeit, falls Stichprobe groß genug.
- Input-, Output- und Cache-Tokens nach Rolle.
- Nutzerinterventionen pro Aufgabe.
- Falsche Fertigmeldungen.
- Wiederholte Fehler ohne neue Information.
- Wiederherstellungserfolg und doppelte Mutationen.
- Recall-Treffer, tatsächlich gelesene Quellen und korrekt angewandtes Wissen.
- Ingestions-, Index-, Konsolidierungs- und Experimentkosten.

Tokens verschiedener Modelle sind nicht unmittelbar identische Rechenkosten. Für Produktentscheidungen reale Kosten, Qualität und Laufzeit zusätzlich ausweisen.

## 10. Ablation

Mindestens für größere Optimierungen einzelne Komponenten ausschalten und vergleichen:

1. Baseline.
2. Baseline plus bedarfsgeladene Information/Tool Discovery.
3. Zusätzlich begrenzte Tool-Ergebnisse und programmatische Bündelung.
4. Zusätzlich Failure Memory und Aktualitätsprüfung.
5. Zusätzlich geprüfte Skills.
6. Optional zusätzlicher Lern-/RSI-Pfad.

Nur passende Aufgaben auf die jeweilige Änderung anwenden. Aus einer schnellen Suchaufgabe keine Aussage über allgemeine Desktop-Kompetenz ableiten. Neue Komponenten entfernen oder zurückstellen, wenn ihr Nutzen den Aufwand nicht rechtfertigt.

## 11. Abnahme und Abschlussstatus

### Erforderlich für die implementierten Stufen

- [ ] Gap-Matrix vollständig und mit tatsächlichen Codebelegen gepflegt.
- [ ] Alle anwendbaren Anforderungen implementiert, durch vorhandene Mechanismen erfüllt oder konkret begründet angepasst.
- [ ] Keine stillen Platzhalter, simulierten Erfolge oder ungenutzten Feature-Pfade.
- [ ] Relevante Vertrags- und Integrationstests bestanden.
- [ ] Projektseitig verpflichtende Build-/Lint-/Testprüfungen ausgeführt.
- [ ] Mindestens repräsentative echte End-to-End-Abläufe ausgeführt.
- [ ] Unterstützte Linux-Umgebung und tatsächlich geprüfte Backends genannt.
- [ ] Datenmigration und Rückweg geprüft, falls Persistenz geändert wurde.
- [ ] Budget, Evidence und Versionsbindung funktionieren auch in Fehlerpfaden.
- [ ] Nutzen beziehungsweise fehlender Nutzen der Optimierungen berichtet.
- [ ] Verworfene Vorschläge und offene externe Voraussetzungen dokumentiert.

### Nicht als vollständig erledigt ausgeben

- Nur Plan/Audit erstellt, aber anwendbare Lücken nicht implementiert.
- Nur Unit-Tests bestanden, obwohl Desktop-/Provider-Integration geändert wurde.
- Pflichtablauf mangels Umgebung nicht ausgeführt.
- Neues Tool existiert, wird aber vom aktiven Agenten nicht verwendet.
- Neuer Skill besteht nur auf genau seinem Erzeugungsbeispiel.
- Ein Kandidat bewertet sich selbst ohne unabhängige Prüfung.

## 12. Vorlage für den Validierungsbericht

```markdown
# Validierungsbericht
Geprüfte Codeversion:
Umgebung und Backends:
Modelle/Provider und Einstellungen:

## Änderungen
Anforderungs-ID | Umsetzung | Codepfad | Status

## Ausgeführte Prüfungen
Test/Befehl | Umgebung | Ergebnis | Evidence

## Vergleich
Variante | Aufgaben/Läufe | vollständiger Erfolg | Kosten | Zeit | Einschränkungen

## Verworfen oder angepasst
Vorschlag | Grund | Alternative | Prüfung

## Offene Punkte
Fehlende Voraussetzung | betroffener Ablauf | nächster konkreter Schritt
```

## 13. CLI-UI und UX

Die zusätzlichen Abnahmen `CLI-01` bis `CLI-12` stehen in [10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md). Deterministische Renderer-Prüfungen, PTY-Integration und echte Coding-/Computer-Use-Abläufe kombinieren. Besonders fehlende Desktop-Evidence, nicht ausgeführte Builds, Dateiaktualisierung, Ctrl+C und maschinenlesbare Ausgabe prüfen.

## 14. Gesamtprodukt nach aktualisierten Nutzerprioritäten

Die zwölf Nutzerabläufe `E2E-01` bis `E2E-12` aus [11-SKALDIR-HANDOFF.md](11-SKALDIR-HANDOFF.md) ergänzen die technischen Verträge um echte Produktabnahmen: Coding, Dispatch, Computer Use, LibreOffice, allgemeiner und Container-Tool-Bau, schnelle Subtools, Voice/JEV, Memory, Recovery und CLI. Codezustand und Nachweisgrad dokumentieren; historische Testzahlen sind keine aktuelle Live-Abnahme.
