# Effiziente Tools, Agentenkontext und Modellaufrufe

Anforderungen: `OBS-01`, `TOOL-01` bis `TOOL-03`, `CTX-01` bis `CTX-03`, `ROUTE-01`, `VOICE-01`.

## 1. Laufzeitbeobachtbarkeit zuerst

Erweitere vorhandene Ereignisse statt ein zweites Telemetriesystem einzuführen. Sinnvolle Felder:

```text
event_id, run_id, goal_revision, parent_event_id, role
model_id, provider, harness_version, tool_id, tool_version
started_at, finished_at, outcome, failure_category
input_tokens, output_tokens, cached_input_tokens
cost_amount, currency, cost_source
artifact_refs, evidence_refs, idempotency_key
```

Provider melden unterschiedliche Tokenkategorien. Erhalte die Rohzählung und dokumentiere die Normalisierung. Cache-Tokens können Teil der Input-Tokens sein: nicht doppelt summieren. Unbekannte Kosten bleiben unbekannt; Preisberechnungen als Schätzung mit Preisstand kennzeichnen.

Rollen mindestens unterscheidbar machen: Hauptagent, Voice, Worker, Verifier, Memory-Konsolidierung und Optimierer. Ein gemeinsam belastetes Run-Budget verhindert, dass ausgelagerte Arbeit unsichtbar weiterläuft.

Keine Secret-Inhalte in Events, Debuglogs oder Kostenreports aufnehmen. Vorhandene Redaktions- und Aufbewahrungsregeln verwenden.

## 2. TOOL-DISCOVERY: kleine Grundausstattung

Der Startkontext enthält nur häufig notwendige Grundfähigkeiten und den Weg zur Suche weiterer Fähigkeiten. Beispiele sind Informationssuche/-abruf, Dateiarbeit, Tool Discovery, begrenzte Ausführung und Statuszugriff.

Der Gesamtbestand bleibt in der Registry. Discovery liefert zunächst wenige passende Treffer mit:

- Stabiler Tool-ID und Version.
- Kurzer Zweckbeschreibung.
- Eingabe-/Ausgabevertrag oder Verweis auf dessen Abruf.
- Seiteneffektklasse und benötigten Berechtigungen.
- Verfügbarkeit, Health und wesentlichen Einsatzvoraussetzungen.
- Einem knappen Beispiel, wenn Schema allein nicht genügt.

Die Registry ist für Validierung und Berechtigungsprüfung maßgeblich. Ein Tool wird nicht allein dadurch ausführbar, dass das Modell seine Beschreibung gefunden hat.

### Zu vermeiden

- Hunderte vollständige MCP-Schemas in jedem Prompt.
- Discovery-Antworten, die den gesamten Katalog ausschütten.
- Ständig wechselnde Identitäten desselben Tools.
- Verwechslung nicht verfügbarer, experimenteller und freigegebener Tools.
- Veraltete Schemas aus früheren Runs ohne Versionsprüfung.

Bestehende dynamische Tool-Mechanismen zunächst auf diese Eigenschaften prüfen und nur fehlende Verträge ergänzen.

## 3. TOOL-RESULTS: Ausgabe kontrollieren

Tool-Antworten sollten bereits auf der Quellseite Filter, Pagination und Feldselektion unterstützen, wenn sinnvoll. Große Ergebnisse in einem Artefaktspeicher ablegen.

Möglicher Ergebnismantel:

```json
{
  "status": "success",
  "summary": "3 fehlgeschlagene Prüfungen in 240 Einträgen",
  "data": {"failed_ids": ["check-8", "check-19", "check-75"]},
  "artifact_id": "artifact-example",
  "artifact_revision": "sha256:example",
  "truncated": false,
  "next_cursor": null,
  "evidence_refs": ["evidence-example"]
}
```

`success` bezeichnet den Tool-Aufruf, nicht automatisch das Nutzerziel. Wurden Daten ausgelassen, muss das explizit sein. Ein Modell darf aus einer Teilmenge nicht auf Vollständigkeit schließen.

Zählungen, Summen und einfache Filter möglichst deterministisch berechnen. LLM-Zusammenfassungen als Ableitungen kennzeichnen. Fehlertexte so erhalten, dass Ursachendiagnose möglich bleibt; riesige Logs verlinken statt unverändert injizieren.

## 4. PROGRAMMATIC-TOOLS: wiederholte Inferenz vermeiden

Wenn mehrere Aufrufe mit bekannten Abhängigkeiten nötig sind, kann der Agent sie in einem begrenzten Programm kombinieren:

```text
Datensätze abrufen
→ nur relevante IDs auswählen
→ erlaubte Detailabfragen ausführen
→ deterministisch aggregieren
→ kompaktes Resultat und Belegreferenzen zurückgeben
```

Die konkrete Sprache an die bestehende Runtime anpassen. Python, JavaScript oder ein vorhandener Workflow-Executor sind mögliche Träger, kein Selbstzweck.

### Anforderungen an den Executor

- Derselbe Dispatcher und dieselben Berechtigungen wie bei direkten Tool-Aufrufen.
- Schema- und Versionsprüfung für jeden Call.
- Gesamtlaufzeit, Einzelaufrufe, Fan-out, Speicher und Ausgabe begrenzen.
- Abbruchsignale an laufende Aufrufe weiterreichen.
- Teilresultate und Fehler sichtbar machen.
- Seiteneffektklasse und Idempotenz pro Aufruf berücksichtigen.
- Unabhängige lesende Aufrufe dürfen parallel laufen; abhängige Mutationen sequenziell.
- Zugangsdaten über bestehende Handles/Broker beziehen, nicht in generierten Code einbetten.
- Laufzeitereignisse für jeden tatsächlichen Aufruf erfassen.
- Fehler im Programm dürfen keine bereits erfolgreichen externen Mutationen unsichtbar machen.
- Prozessverlust und Wiederaufnahme über persistente Aktionszustände behandeln.

Ein persistenter Kernel kann Wiederverwendung erleichtern, ist aber kein Recovery-Speicher. Falls Reproduzierbarkeit oder Isolation dadurch schlechter werden, stateless Executor plus persistente Artefakte bevorzugen.

## 5. CONTEXT-ASSEMBLY: aktiver Kontext als berechnete Ansicht

Empfohlene Reihenfolge, an Provider-Verträge anpassen:

1. Maßgebliche Regeln und stabile Runtime-Anweisungen.
2. Kleine Grundausstattung von Tools und Fähigkeiten.
3. Aktuelles Goal mit stabilen Kriterien und Nutzervorgaben.
4. Kompakter Arbeitszustand.
5. Relevante Wissensausschnitte mit Herkunft und Version.
6. Jüngste Beobachtungen, offene Fehler und aktuelle Nutzersteuerung.

Setze Budgets pro Kontextkategorie. Geeignete Startwerte aus realer Nutzung ableiten, nicht pauschal von einem bestimmten Modellfenster ausgehen. Reserve für Modellantwort und erwartete Tool-Ergebnisse berücksichtigen.

Folgende Daten dürfen nicht durch Verdichtung verloren gehen:

- Offene Nutzeranforderungen und Entscheidungen.
- Nicht abgeschlossene Aktionen und unbekannte Ergebnisse.
- Wesentliche Fehlversuche, solange sie noch relevant sind.
- Referenzen auf Originalbelege.
- Versionsbindung von Erfolgsevidenz.

Sehr große Altbeobachtungen auslagern. Informationsverlust durch wiederholte Zusammenfassungen vermeiden, indem bei Bedarf erneut die Originale abgerufen werden.

## 6. PROVIDER-CONTEXT: keine universelle Cache-Annahme

Erfasse pro Adapter Fähigkeiten wie:

```text
supports_tool_discovery
supports_programmatic_tools
supports_explicit_cache_breakpoints
supports_context_compaction
context_mutation_constraints
usage_accounting_semantics
```

Nicht aus dem Modellnamen ableiten; anhand aktueller Provider-Verträge und vorhandener Adapter implementieren.

- Stabile Präfixe deterministisch serialisieren, soweit sinnvoll.
- Variable Zeitstempel und kurzlebige Statuswerte nicht unnötig in stabile Präfixe setzen.
- Änderungen von Tools oder Reasoning-Einstellungen können je nach Provider unterschiedliche Cache-Effekte haben.
- Geschützte oder providerseitig gebundene Kontextblöcke nicht eigenmächtig umschreiben.
- Prompt-Caching verringert Kosten beziehungsweise Verarbeitung, aber nicht automatisch den logisch sichtbaren Kontext.
- Cache-Hits, tatsächliche Inputmenge und Kosten getrennt anzeigen.
- Providerwechsel nur an unterstützten Zustandsgrenzen durchführen. Tool-Call-Paare und ausstehende Ergebnisse konsistent abschließen.

## 7. READ-DEDUP: identische lesende Arbeit vermeiden

Ein Wiederverwendungsschlüssel kann enthalten:

```text
tool_id + tool_version + normalized_arguments
+ project_scope + authorization_scope
+ relevant_state_fingerprint + freshness_policy
```

Wiederverwendung nur bei nachweislich gültigem Zustand. Nach Dateiänderung, anderer Berechtigung, Tool-Update oder Ablaufzeit neu lesen. Schnell veränderliche externe Zustände brauchen kurze Gültigkeit oder explizite Abfrage.

Schreibende Aufrufe niemals durch einen allgemeinen Lesecache „deduplizieren“. Dort sind Idempotenzschlüssel, Statusabgleich und Reconciliation erforderlich. Ein Timeout ist kein Beleg, dass nichts passiert ist.

## 8. MODEL-ROUTING: austauschbar und messbar

Nutze vorhandene Modell-Aliasse und Provider-Konfigurationen. Der Plan schreibt keinen Modellanbieter vor. Aktuelle Produktnamen in der Recherche sind Vergleichskandidaten, keine dauerhaften Abhängigkeiten.

Mögliche Aufgabentypen:

- Kleine strukturierte Extraktion.
- Routine-Tool-Ausführung.
- Schwierige Planung oder Architekturentscheidung.
- Visuelle Lokalisierung und Desktop-Arbeit.
- Unabhängige Ergebnisprüfung.

Eskalation durch beobachtbare Signale auslösen: wiederholte Fehler, unzureichende Fähigkeiten, widersprüchliche Belege oder scheiternde Postconditions. Bloße Selbsteinschätzung des Modells reicht nicht als kalibrierte Zuverlässigkeitsmessung.

Bei einem Wechsel ein begrenztes Zustands- und Evidenzpaket übergeben. Nutzerseitige Modellwahl respektieren. Der Router erweitert keine Credentials, Scopes oder Budgetgrenzen.

## 9. VOICE: gezielte Supervision

Sinnvolle Trigger:

- Wiederholte Aktion ohne relevante Zustandsänderung.
- Längere Phase ohne überprüfbaren Fortschritt.
- Widerspruch zwischen Goal und geplantem Schritt.
- Neues Nutzerziel oder geänderte Anforderung.
- Behaupteter Abschluss bei fehlender oder veralteter Evidence.
- Fehlerhäufung oder nahende Budgetgrenze.

Voice erhält Ereigniszusammenfassung und relevante Referenzen, nicht automatisch den gesamten Verlauf. `observe` beziehungsweise kein Eingriff ist ein gültiges Ergebnis.

Mögliche Rückgaben: weiter beobachten, neu beobachten, aktuellen Plan prüfen, Information nachladen, Ergebnis verifizieren oder zulässige Eskalation empfehlen. Goal-Revisionen und schreibende Aktionen bleiben bei den vorhandenen zuständigen Komponenten.

## 10. Subagents

Vorhandene Delegation nur einsetzen, wenn eine klar abgrenzbare Aufgabe sinnvoll parallel bearbeitet werden kann. Kein automatischer Schwarm für jede Nutzeranfrage.

Jeder Worker erhält Scope, Budget, Abnahmekriterium und relevantes Kontextpaket. Rückgabe: Resultat, offene Punkte, Artefakte, Revisionen und Belege. Der Hauptagent integriert und verifiziert. Ein Worker-`done` ist kein Gesamtabschluss.

Mehrere Worker dürfen nicht unkoordiniert dieselbe Desktop-Sitzung bedienen oder denselben Zustand überschreiben. Für Codeänderungen bestehende Isolation verwenden; für gemeinsames Memory Konfliktbehandlung aus der Memory-Spezifikation.

## 11. Abnahme

Auf geeigneten Vergleichsaufgaben zeigen:

- Weniger unnötig geladene Tool-Schemas und Rohdaten.
- Keine verlorenen Fehler oder verschwiegenen Teilresultate.
- Identische Berechtigungsprüfung in direktem und programmatischem Pfad.
- Ein korrektes Gesamtbudget über alle Rollen.
- Keine verschlechterte vollständige Erfolgsquote durch aggressive Verdichtung.
- Keine falsche Wiederverwendung veralteter Such-, Datei- oder Testergebnisse.
- Beobachtbarer Nutzen von Routing und Voice gegenüber ihrem zusätzlichen Aufwand.
