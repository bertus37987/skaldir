# Geprüfte Skills und kontrollierte Selbstverbesserung

Anforderungen: `SKILL-01`, `SKILL-02`, `LEARN-01`, `RSI-01`, `RSI-02`.

## 1. Drei getrennte Veränderungsebenen

| Ebene | Veränderungsgegenstand | Beispiel |
|---|---|---|
| Erfahrungslernen | Wissen und Verfahrensbeschreibungen | Bekannten Exportfehler und Reparatur speichern |
| Harness-Verbesserung | Tool-Auswahl, Kontextstrategie, Recovery oder einzelne Prompts | Veraltete GUI-Beobachtungen früher erkennen |
| Verbesserung des Optimierers | Verfahren zur Suche nach Verbesserungen | Andere Kandidatenauswahl nach Experimenthistorie |

Zuerst zuverlässiges Erfahrungslernen implementieren. Harness-Änderungen setzen eine Evaluation voraus. Änderungen am Optimierer selbst sind eine spätere Forschungsfunktion und kein Pflichtbestandteil der ersten Ausbaustufen.

Das zugrunde liegende Modell wird für diese Mechanismen nicht automatisch trainiert oder in seinen Gewichten verändert. Diese Unterscheidung in Dokumentation und UI beibehalten.

## 2. SKILL-CONTRACT

Ein Skill ist eine überprüfbare Fähigkeit mit lesbarer Beschreibung und gegebenenfalls ausführbarer Implementierung.

Mindestbestandteile:

```text
skill_id, version, status, owner_scope
purpose, selection_hints, non_goals
input_schema, output_schema
preconditions
required_tools_and_versions
required_capabilities
execution_entry_or_instructions
postconditions_and_verification
failure_modes_and_recovery
evidence_refs, evaluation_summary
created_from_runs
```

Die ausführbare Registry bleibt maßgeblich für Scopes und Berechtigungen. Ein frei editierbares `SKILL.md` darf sich keine höheren Rechte geben.

Beschreibung klein halten und bei Bedarf weitere Dateien zeilenweise laden. Parameter und Umgebungstrennung verhindern, dass ein Skill versehentlich konkrete Nutzerdateien oder einmalige Werte aus dem Lernbeispiel fest einbaut.

## 3. Lebenszyklus

```text
candidate → tested → active
    ↓          ↓        ↓
 rejected   rejected   deprecated / rolled_back
```

`Tested` bedeutet, dass benannte Prüfungen auf benannten Umgebungen erfolgreich waren. Es bedeutet keine universelle Gültigkeit. `Active` benötigt die im Projekt definierte Freigabelogik und eine konsistente Registry-Version.

Bestehende gleichwertige Zustände verwenden. Keine neue Registry neben einer bereits vorhandenen EXPAND-Registry einführen.

## 4. Aus Erfahrung einen Skill-Kandidaten erzeugen

Geeignete Auslöser:

- Wiederkehrender mehrstufiger Ablauf.
- Aufwendige, erfolgreich überprüfte Reparatur mit übertragbarem Mechanismus.
- Eine fehlende Fähigkeit, die EXPAND erfolgreich ergänzt hat.
- Nutzerwunsch nach einer wiederverwendbaren Fähigkeit.

Ungeeignete Auslöser:

- Ein einzelner unbestätigter Agenten-Claim.
- Ein Ablauf, dessen Erfolg nicht sinnvoll überprüft werden kann.
- Eine einmalige, stark situationsgebundene Koordinatensequenz.
- Reine Verlängerung des Prompts ohne klaren Nutzen.

Kandidatenbildung:

1. Ausgangsproblem und belegten Ablauf erfassen.
2. Inputs parametrisieren; Secrets, IDs und Pfade auf ihre Geltung prüfen.
3. Voraussetzungen und bekannte Grenzen formulieren.
4. Fachliche Ergebnisprüfung definieren.
5. Mindestens neue Eingaben und einen relevanten Fehlerfall testen.
6. Kosten und Nutzen mit freier Ausführung vergleichen.
7. Nur geeignete Kandidaten aktivieren.

Nicht jede Erfahrung braucht einen ausführbaren Skill. Manchmal genügt ein projektbezogener Fakt oder eine kurze Fehlerregel.

## 5. Wiederverwendung

Der Hauptagent findet den Skill über Discovery und liest zunächst Zweck, Voraussetzungen und Parameter. Vor Ausführung Umgebung und erforderliche Tool-Versionen prüfen.

Bei nicht erfüllter Voraussetzung:

- Skill nicht blind starten.
- Ursache als strukturierte Abweichung zurückgeben.
- Freie Planung oder eine geeignete vorhandene Alternative verwenden.
- Wiederkehrende Abweichungen als Reparaturkandidaten erfassen.

Ein nachträglich gescheiterter Skill muss schnell deaktivierbar sein. Laufende Ausführungen an eine Version binden. Keine Änderung der Implementierung mitten in einer bereits gestarteten Ausführung.

## 6. RSI: begrenzter Optimierungslauf

Voraussetzungen vor Aktivierung:

- Reproduzierbare Ausgangsversion und messbare Baseline.
- Tatsächliche Run-/Fehlerdaten.
- Repräsentative Entwicklungstests und getrennte Validierung.
- Definierter erlaubter Änderungsbereich.
- Isolierte Ausführungsumgebung und gemeinsames Budget.
- Rückweg zur letzten bekannten funktionsfähigen Version.

Lauf:

```text
Belegte Fehler sammeln
→ wiederkehrendes Muster bestimmen
→ Hypothese formulieren
→ kleine isolierte Änderung erzeugen
→ Vertragstests und Entwicklungsevaluation
→ getrennte Validierung
→ übernehmen oder verwerfen
→ Ergebnis und Versionslinie speichern
```

Ein Loop darf nicht unbegrenzt weiterlaufen. Begrenze Kandidatenzahl, Modell-/Tool-Kosten, Laufzeit und erlaubte Änderungsmenge. Für jede Ablehnung den Grund speichern, damit derselbe Versuch nicht ohne neue Information wiederholt wird.

## 7. Komponentenweise Änderungen

In Anlehnung an ModularRSI folgende Bereiche getrennt optimieren, soweit sie im Code tatsächlich existieren:

- Tool-Auswahl und Tool-Beschreibungen.
- Beobachtungsverarbeitung.
- Kontextzusammenstellung und Retrieval.
- Wiederherstellungsstrategien.
- Planung und Skill-Auswahl.

Der produktive Nachweis der Aufgabenerfüllung bleibt unabhängig. Änderungen an Verifiern können separat vorgeschlagen und gegen vertrauenswürdige Fixtures geprüft werden; derselbe Kandidat darf seine eigenen Bestehensregeln nicht frei ändern.

Beispiel einer sinnvollen Hypothese:

```text
Befund: Nach Dialogwechseln werden wiederholt alte Koordinaten benutzt.
Änderung: Beobachtung nach erkanntem Dialogwechsel invalidieren.
Erwartung: Weniger Fehlklicks bei begrenzten zusätzlichen Beobachtungskosten.
Prüfung: Alte und neue Dialogabläufe, Fokuswechsel, störungsfreier Normalfall.
```

## 8. Auswahl und Generalisierung

Vor Beginn festlegen:

- Primäres Ziel, zum Beispiel vollständiger Erfolg bei festem Kostenbudget.
- Harte Vertragsbedingungen: keine unautorisierten Aktionen, keine falschen Quellenreferenzen, keine verschwiegenen Mutationsresultate.
- Welche Qualitätsverschlechterung tolerierbar ist; Standard für wichtige Korrektheitsverträge: keine.
- Welche Kosten-/Laufzeitänderung vertretbar ist.
- Welche Messunsicherheit die Stichprobe hat.

Kandidaten nicht allein nach höherem Score auf immer denselben Aufgaben auswählen. Erfolg, Kosten und Komplexität getrennt ausweisen. Verbesserungen unter Messrauschen nicht als gesichert darstellen.

Beachte die begrenzte Lebensdauer eines Holdouts: Wiederholte Auswahl anhand derselben Rückmeldungen passt indirekt auch an den Holdout an. Eine finale Auditmenge zurückhalten, Validierung rotieren oder bei häufiger Nutzung erneuern. Aufgaben, die dem Optimierer als Fehlerfeedback gezeigt wurden, sind keine unbekannten Abschlussaufgaben mehr.

Regeln, die nur konkrete Benchmarknamen, Dateiinhalte oder erwartete Antworten memorisieren, verwerfen. Neue Eingaben und andere passende Umgebungen prüfen.

## 9. Was unverändert maßgeblich bleibt

Der Lernpfad darf nicht automatisch verändern:

- Nutzerziel, Non-Goals und ausdrücklich gesetzte Grenzen.
- Credentials, Secret Broker und Berechtigungsregeln.
- Budgetabrechnung oder Audit-Aufzeichnung.
- Maßgebliche Ergebnisdaten der Evaluation.
- Die unabhängigen Regeln, anhand derer der eigene Kandidat besteht.

Diese Trennung über Codegrenzen und Tests absichern, nicht allein über eine Promptanweisung.

## 10. Rollback und Auslieferung

- Jede aktive Version verweist auf Vorgänger, Hypothese und Evaluation.
- Aktivierung atomar oder über vorhandenen Versions-/Release-Mechanismus.
- Bestehende Runs behalten ihre gebundene Version, soweit nicht eine explizite kompatible Migration vorgesehen ist.
- Fehlerhafte neue Version deaktivieren können, ohne Originale oder Evidence zu löschen.
- Produktiven Rollout im Rahmen vorhandener Projektprozesse durchführen. Keine stille Selbstveröffentlichung als Nebeneffekt eines Tests.

## 11. Optional und erst später

**Adaptive Memory-Suche:** Retrieval-Gewichte, Auswahlumfang und Konsolidierungsregeln anhand echter Fehlermuster optimieren. Sowohl Extraktions- als auch Antwortkosten messen.

**Replay für Experimentauswahl:** Frühere Experimente nutzen, um teure Suchpfade besser zu priorisieren. Replay belegt nur Verhalten innerhalb seiner beobachteten Abdeckung; neue Tool-/GUI-Zustände weiterhin real evaluieren.

**Optimierer verändert sich selbst:** Erst nach separater Bewertung und mit unverändertem externem Kontroll- und Bewertungssystem erforschen. Nicht nötig, um die ersten nutzbaren Verbesserungen zu erzielen.

## 12. Abnahme

- Ein neuer Skill löst eine bekannte Aufgabenklasse mit neuen Parametern.
- Ein unpassender Skill erkennt seine fehlenden Voraussetzungen.
- Eine fehlgeschlagene Verbesserung wird abgelehnt und korrekt zurückgerollt.
- Quellen und Tests jedes aktiven Kandidaten sind nachvollziehbar.
- Der Optimierer kann weder sein Budget verbergen noch eigene Testergebnisse manipulieren.
- Erfolg auf zurückgehaltenen Aufgaben und tatsächliche Gesamtkosten werden getrennt berichtet.
- Der Begriff RSI wird nur für implementiertes und geprüftes Verhalten verwendet.
