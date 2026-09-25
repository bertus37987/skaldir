# Ist-Abgleich, Architekturentscheidungen und Migration

Anforderungsbereich: `AUD-01`, Grundlage für alle weiteren Bereiche.

## 1. Repository orientieren

- Lies anwendbare `AGENTS.md`, Entwicklungsanleitungen und lokale Projektregeln.
- Ermittle Sprache, Paketmanager, Monorepo-Struktur und Build-/Testbefehle.
- Prüfe den Arbeitsbaum. Vorhandene Nutzeränderungen nicht überschreiben oder pauschal zurücksetzen.
- Finde die aktive Runtime, ihre öffentlichen Einstiegspunkte und die tatsächlich verwendeten Provider-Adapter.
- Unterscheide aktuellen Produktcode, alte Implementierungen, Experimente und nur dokumentierte Pläne.
- Erfasse die unterstützten Desktop- und CLI-Einstiegspunkte sowie deren gemeinsame oder getrennte Zuständigkeiten.
- Lies keine Secret-Inhalte für die Inventur. Konfigurationsnamen und Broker-Verträge reichen normalerweise aus.

Suche zuerst gezielt nach Symbolen und Dateien, etwa mit `rg` und `rg --files`. Lade nicht das gesamte Repository in den Modellkontext.

## 2. Komponenten nach Funktion zuordnen

| Funktion | Gesuchte Codebelege | Zu klärende Frage |
|---|---|---|
| Goal-Verwaltung | Goal-Modell, Revisionen, Kriterien, Statusübergänge | Wer darf Ziel und Status verändern? |
| Hauptagent | Agent loop, Scheduler, Provider-Aufruf | Wer besitzt den aktuellen Arbeitszustand? |
| Tool-System | Registry, Schemas, Dispatcher, Discovery | Sind Tools statisch im Prompt oder nachladbar? |
| EXPAND | Tool-Erzeugung, Validierung, Registrierung | Werden Kandidaten von freigegebenen Tools getrennt? |
| ACT | Browser-/Desktop-Adapter, Beobachtungen, Aktionen | Welche Linux-Sitzungen funktionieren wirklich? |
| JEV | Verifier, Kriterienbindung, Evidence Store | Was verhindert falsche Fertigmeldungen? |
| Voice | Trigger, Eingriffe, Lesezugriff | Wie viel Kontext und Budget verbraucht sie? |
| Memory | Persistenz, Suche, Kompression, Nutzerprofil | Welche Daten sind maßgeblich, welche abgeleitet? |
| Recovery | Checkpoints, Leases, Retry-Logik | Was passiert nach unbekanntem Side Effect? |
| Credentials | Treasury/Secret Broker, Capability Scopes | Können neue Executor-Pfade dieselben Grenzen nutzen? |
| Subagents | Kontextpaket, Budget, Workspaces, Rückgabe | Wer integriert und verifiziert Ergebnisse? |
| UI und CLI | Commands, Ereignisdarstellung, Resume | Verwenden beide denselben Runtime-Vertrag? |

Eine passend benannte Klasse ist kein Funktionsnachweis. Verfolge mindestens einen realen Aufrufpfad vom Nutzereinstieg bis zur Persistenz und Ergebnisrückgabe.

## 3. Mindestabläufe vor Änderungen nachvollziehen

1. Ein einfacher lesender Tool-Aufruf.
2. Ein Tool-Aufruf mit großem Ergebnis.
3. Ein schreibender Aufruf mit Timeout oder vergleichbarem Fehlerpfad.
4. Ein langer Run mit Kontextwechsel oder Kompaktierung.
5. Abruf einer Information aus einer früheren Sitzung.
6. Ein Browser- oder Desktop-Schritt mit Ergebnisprüfung.
7. Ein Abschluss-Claim, den JEV akzeptiert oder ablehnt.

Sind keine geeigneten Fixtures vorhanden, lege kleine repräsentative Fixtures für die geänderten Verhaltensverträge an. Keine großflächige Testsuite nur für das Audit aufbauen.

## 4. Gap-Matrix

Erzeuge eine Zeile pro Anforderung aus `00-MIMO-START.md`, ausdrücklich auch für jede einzelne `BASE-01` bis `BASE-24` aus [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md). Starte mit dem Coding-Kern und verfolge einen echten Bugfix vom Nutzerauftrag über Edit und Test bis zum Abschluss.

| Feld | Inhalt |
|---|---|
| ID | Zum Beispiel `INFO-03` |
| Status | `vorhanden`, `teilweise`, `fehlt`, `nicht anwendbar`, `ungeklärt` |
| Codebeleg | Existierender Pfad, Symbol und geprüfte Revision |
| Laufzeitbeleg | Test, reproduzierbarer Ablauf oder konkretes Ergebnis |
| Nachweisgrad | Statisch, Vertragsprüfung, Integration oder echter Agentenlauf; fehlende Laufzeitprüfung als nicht verifiziert markieren |
| Lücke | Fehlendes Verhalten, möglichst als Vorher/Nachher-Beispiel |
| Entscheidung | Behalten, erweitern, ersetzen oder verwerfen |
| Abhängigkeiten | Andere Anforderungen oder externe Voraussetzungen |
| Validierung | Konkrete Abnahme aus `06-TESTS-UND-ABNAHME.md` |

`Vorhanden` nur verwenden, wenn die benötigte Funktion tatsächlich im aktiven Pfad liegt. Bei rein statischer Codeprüfung den fehlenden Laufzeitnachweis ausdrücklich markieren.

## 5. Entscheidungsverfahren

Für jeden größeren Vorschlag beantworten:

1. Welches beobachtete Problem löst er?
2. Kann das vorhandene System mit einer kleineren Änderung dasselbe leisten?
3. Welche neue Zuständigkeit, Datenhaltung oder Abhängigkeit entsteht?
4. Wie wird sein Nutzen gemessen?
5. Welche Migration und welcher Rückweg sind nötig?

Entscheidungsvorlage:

```markdown
### DEC-001: Titel
Status: angenommen | angepasst | verworfen | zurückgestellt
Anforderungen: INFO-02, INFO-03
Ist-Befund: Codepfad und beobachtetes Verhalten
Entscheidung: konkrete Änderung oder vorhandene Alternative
Begründung: Qualität, Kosten, Wartbarkeit, Kompatibilität
Validierung: Testfälle und Vergleichsverfahren
Offene Voraussetzung: nur falls tatsächlich vorhanden
```

Wähle kleine Änderungen mit hohem Nutzwert vor großen Framework-Wechseln. Ersetze keine vorhandene funktionierende Datenbank durch SQLite, nur weil die Recherche SQLite als möglichen Einstieg nennt.

## 6. Ausgangsmessung

Halte für die später veränderten Abläufe mindestens fest:

- Modell-/Provider-Version und Reasoning-Einstellung.
- Tool-Katalog, Prompt-/Harness-Version und Feature-Konfiguration.
- Zahl der Schritte, Tool-Aufrufe, Fehler und Wiederholungen.
- Eingabe-, Ausgabe- und gemeldete Cache-Tokens, jeweils pro Rolle.
- Tatsächliche beziehungsweise geschätzte Kosten getrennt kennzeichnen.
- Endzustand, vollständiger Erfolg und zugehörige Evidence.
- Laufzeit und notwendige Nutzereingriffe.

Fehlende Werte sind `unknown`, nicht null Kosten oder null Tokens. Bei fehlenden historischen Messungen eine kleine aktuelle Baseline erzeugen.

## 7. Migration bestehender Daten

- Bestehende Memory-Inhalte zunächst klassifizieren: Regeln, Fakten, Episoden, Skills oder unklare Notizen.
- Bestehende Quellenreferenzen und Zeitinformationen erhalten.
- Keine alten Agentenaussagen rückwirkend zu verifizierten Fakten erklären.
- Bereits gespeicherte Secrets nicht in neue Markdown-Indizes oder Prompts kopieren.
- Neue Schemas versionieren und bestehende Migrationsmechanismen verwenden.
- Migration wiederholbar machen; doppelte Ausführung darf keine doppelten Erinnerungen erzeugen.
- Für jede verlustbehaftete Umformung Original oder wiederherstellbare Vorgängerversion behalten, soweit Aufbewahrungsregeln das erlauben.
- Bestehende Lösch- und Aufbewahrungsregeln auf Originale, Zusammenfassungen, Indizes und Sicherungen anwenden. „Append-only“ bedeutet nicht unbegrenzte Unlöschbarkeit.
- Kompatibilität von Checkpoints und laufenden Sessions explizit prüfen.

## 8. Grenze des Codeabgleichs

Ändere den Plan bei belegten Architekturabweichungen. Dokumentiere keine fiktiven Pfade und behaupte keine Testergebnisse aus bloßer Codelektüre. Falls der Nutzer einen Mechanismus bereits besitzt, ist die richtige Arbeit häufig dessen Integration oder Reparatur, nicht seine Neuerfindung.
