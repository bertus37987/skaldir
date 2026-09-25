# Informationen als abrufbare Fähigkeiten und dauerhaftes Memory

Anforderungen: `INFO-01` bis `INFO-04`, `MEM-01`, `MEM-02`.

## 1. Ziel und Kernentscheidung

Skaldir soll relevantes Wissen entdecken, gezielt lesen und auf Belege zurückführen können. Eine Information ist dabei ein abrufbares Objekt mit Herkunft, Version und Geltungsbereich. Sie erhält durch Abruf keine höhere Anweisungspriorität.

**Übernehme die Nutzeridee:** kleine Markdown-Dateien und exakter Zeilenabruf sind ein guter Grundmechanismus. **Verwerfe die schwache Variante:** lose Zeilennummern ohne Version, ein riesiger Dateibaum im Systemprompt und automatische Übernahme beliebiger Notizen als Regeln.

Bestehende Tools wie `search`, `read_file` und `read_lines` können diesen Vertrag erfüllen. Die Namen `info.search` und `info.read` sind nur vorgeschlagene logische Schnittstellen. Implementiere keine doppelten Tools, wenn die vorhandenen passend erweitert werden können.

## 2. Datenarten und Zuständigkeiten

| Typ | Beispiel | Maßgebliche Ablage | Verwendung |
|---|---|---|---|
| Arbeitszustand | Goal, Kriterien, offene Schritte, Ressourcen | Bestehender transaktionaler Runtime Store | Direkt im aktiven Run |
| Episoden | Tool-Aufruf, Ergebnis, Fehler, Beobachtung | Ereignis-/Trajektorienspeicher | Suche und Reproduktion |
| Fakten und Entscheidungen | Projektentscheidung, bestätigte Präferenz | Typisierte Einträge, ggf. Markdown-Projektion | Bedarfsgeladener Recall |
| Verfahrenswissen | Skill, getesteter Reparaturweg | Versionierte Registry mit lesbaren Dateien | Parametrisierte Wiederverwendung |
| Evidenz | Testreport, Artefakt, Screenshot | Artefakt-/Evidence Store | Abschlussprüfung |
| Normative Regeln | Nutzeranweisung, Projektkonvention | Bestehende maßgebliche Regelquelle | In ihrer tatsächlichen Priorität |

Markdown dient lesbarem Wissen und Verfahrensbeschreibungen. Leases, Aktionsstatus, Budgetbuchung und Recovery-Checkpoints gehören nicht in frei editierbare Notizdateien.

## 3. Vorschlag für die Wissensablage

Pfade an vorhandene Konventionen anpassen:

```text
knowledge/
  INDEX.md
  projects/<project-id>/architecture.md
  projects/<project-id>/decisions/<topic>.md
  projects/<project-id>/runbooks/<workflow>.md
  user/preferences.md
  research/<topic>.md
skills/
  <skill-id>/SKILL.md
  <skill-id>/references/<topic>.md
  <skill-id>/scripts/<implementation>
```

- Jede Datei behandelt einen kohärenten Gegenstand. Unabhängige Themen aufteilen.
- Keine leeren Ordner oder Notizen vorab erzeugen; Ablage nach realem Bedarf.
- Ein kleiner Index beschreibt Bereiche, nicht jeden Detailinhalt der Wissensbasis.
- Metadaten und Ausschnitte zunächst aus der Suche zurückgeben; Volltext nur bei Bedarf.
- Große Tabellen, Logs und binäre Artefakte in passendem Originalformat speichern. Markdown enthält Beschreibung und Referenz.
- Keine künstliche Zerstückelung auf feste Zeilenzahlen. Sehr große Dateien anhand tatsächlicher Abrufmuster teilen.
- Eine bestehende Datenbank darf maßgeblich bleiben. Falls Markdown eine Projektion ist, Schreib- und Synchronisationsrichtung explizit festlegen.

## 4. Minimale Metadaten

Mögliches Frontmatter einer abgeleiteten Wissensdatei:

```yaml
id: project-example-export
title: PDF-Export im Beispielprojekt
kind: procedure
scope: project:example
status: candidate
updated_at: 2026-09-24T12:00:00Z
verified_at: null
sources:
  - evidence_id: evidence-example-17
applies_to:
  application: example-editor
  version_range: unresolved
```

Notwendige semantische Felder:

- Stabile ID, Typ und Scope.
- Herkunft: Original, Evidence, Nutzerentscheidung oder ausdrücklich markierte Ableitung.
- Erstellungs-/Änderungszeit sowie, falls bekannt, Gültigkeitszeit.
- Prüfstatus: Kandidat, bestätigt, veraltet, ersetzt oder verworfen.
- Abhängigkeiten von Versionen, Dateien, Umgebung oder Tool-Schema.
- Verweise auf ersetzte Aussagen und auf Originalbelege.

Der Inhalts-Hash wird vom Store berechnet und nicht in derselben Datei als sich selbst referenzierendes Feld gepflegt. Eine Zahlenangabe für „Confidence“ ersetzt keinen Prüfbeleg. Suchrelevanz und Wahrheitssicherheit sind verschiedene Dinge.

## 5. INFO-SEARCH: begrenzte Entdeckung

Vertrag von `info.search` beziehungsweise vorhandenem Äquivalent:

```json
{
  "query": "PDF Export verliert Schriftarten",
  "scope": "project:example",
  "kinds": ["procedure", "failure", "decision"],
  "limit": 5,
  "max_output_tokens": 1200,
  "cursor": null
}
```

Beispiel einer Antwort:

```json
{
  "matches": [
    {
      "id": "project-example-export",
      "path": "knowledge/projects/example/runbooks/pdf-export.md",
      "revision": "sha256:example-revision",
      "title": "PDF-Export",
      "kind": "procedure",
      "status": "verified",
      "start_line": 84,
      "end_line": 86,
      "snippet": "Beim Export die eingebetteten Schriftarten prüfen.",
      "evidence_ids": ["evidence-example-17"]
    }
  ],
  "truncated": false,
  "next_cursor": null
}
```

Die Beispielwerte sind illustrativ. Das Snippet darf gekürzt sein; `start_line` und `end_line` bezeichnen den zugrunde liegenden Bereich, keine Behauptung, dass das Snippet seinen gesamten Text enthält.

Suchstrategie:

1. Scope und Zugriffsrechte vor Auswahl und Ausgabe anwenden.
2. Exakte IDs, Pfade und Schlüsselbegriffe berücksichtigen.
3. Volltextsuche als einfache Baseline verwenden, falls keine bessere Suche existiert.
4. Semantische Suche ergänzen, wenn sie messbar zusätzliche relevante Treffer liefert.
5. Relevanz, Gültigkeit und Quellenqualität getrennt behandeln.
6. Fuzzy-/semantische Nähe darf einen überholten Fakt nicht als aktuellen Fakt ausgeben.
7. Ergebniszahl und Ausgabebudget serverseitig begrenzen; bei Kürzung einen Fortsetzungspfad liefern.

Ein leeres Ergebnis ist ein gültiges Resultat. Danach darf der Agent passend zum Auftrag aktuelle Quellen lesen oder recherchieren. Er darf keine fehlende Erinnerung erfinden.

## 6. INFO-READ: genaue Zeilen aus einer bestimmten Version

Vertrag von `info.read` beziehungsweise vorhandenem Äquivalent:

```json
{
  "id": "project-example-export",
  "expected_revision": "sha256:example-revision",
  "start_line": 84,
  "end_line": 86,
  "max_output_tokens": 1200
}
```

Beispiel einer Antwort:

```json
{
  "id": "project-example-export",
  "revision": "sha256:example-revision",
  "total_lines": 142,
  "returned_start_line": 84,
  "returned_end_line": 86,
  "lines": [
    {"number": 84, "text": "## Ergebnisprüfung"},
    {"number": 85, "text": "Die exportierte Datei muss existieren und lesbar sein."},
    {"number": 86, "text": "Prüfe Inhalt, Seiten und eingebettete Schriftarten."}
  ],
  "truncated": false,
  "next_start_line": null
}
```

### Verbindliche Semantik

- Zeilen sind 1-basiert; Anfang und Ende sind einschließlich.
- Leerzeilen und Frontmatter zählen mit. Visueller Zeilenumbruch im Terminal erzeugt keine zusätzlichen Zeilen.
- LF und CRLF werden als physische Zeilentrenner verstanden. Die Revision basiert auf dem tatsächlich gelesenen Byte-Snapshot; Quelldateien nicht beiläufig normalisieren.
- Ein abschließender Zeilentrenner erzeugt keine zusätzliche künstliche Inhaltszeile. Leere Datei: null Zeilen.
- Der zurückgegebene Hash und Inhalt stammen aus demselben Snapshot. Nicht erst eine Version prüfen und danach ungeprüft neue Bytes lesen.
- Wenn `expected_revision` nicht der gelesenen Version entspricht: `STALE_REFERENCE` mit aktueller Revision zurückgeben, keinen scheinbar passenden alten Bereich liefern.
- Ein gezielter historischer Abruf darf optional eine archivierte Version verlangen. Die Antwort kennzeichnet sie ausdrücklich als historisch.
- Ungültige Bereiche liefern einen strukturierten Fehler, keinen stillschweigend anderen Bereich. Bereiche hinter EOF werden ausdrücklich als ungültig oder abgeschnitten gekennzeichnet; eine Variante festlegen und testen.
- Bei Ausgabekürzung nur tatsächlich zurückgegebene Zeilen deklarieren und `next_start_line` liefern.
- Eine einzelne überlange Zeile nicht still abschneiden. `LINE_TOO_LARGE` mit zugänglichem Artefakt-/Offset-Abruf oder explizitem Fragmentvertrag anbieten.
- Die Laufzeit erzwingt Scope, erlaubte Wurzeln und Auflösung symbolischer Links. Ein Modellparameter darf diese Grenzen nicht erweitern.

### Warum Dateiversionen notwendig sind

Eine Aussage wie „siehe Zeile 85“ ist nach einer Bearbeitung möglicherweise falsch. Ein belastbarer Verweis lautet mindestens:

```text
document_id + content_revision + start_line + end_line
```

Optionale Abschnitts-IDs helfen bei erneuter Suche nach einer Bearbeitung. Sie ersetzen nicht den Versionsbezug für einen Beleg.

## 7. Freshness: Verfügbarkeit und Aktualität unterscheiden

Ein korrekt gelesener alter Inhalt kann fachlich überholt sein. Prüfe deshalb zwei Ebenen:

1. **Integrität:** Gehören Zeilen und Revision zum gleichen gespeicherten Inhalt?
2. **Gültigkeit:** Gilt dieser Inhalt noch für den aktuellen Projekt-/Umgebungszustand?

Relevante Änderungen: Quelldatei geändert, Tool-Schema geändert, App-Version gewechselt, Nutzerentscheidung ersetzt, Berechtigung geändert, zeitabhängiger Fakt abgelaufen.

Verhalten:

- Abhängigkeit geändert: Eintrag als neu zu prüfen markieren.
- Aktuelle Quelle erneut lesen oder den Unterschied untersuchen.
- Bestätigte Aktualisierung mit Herkunft speichern.
- Historische Information nicht ohne Kennzeichnung als aktuelle Handlungsanweisung einsetzen.
- Erfolgsevidenz an geprüfte Artefakt-/Codeversion binden. Ein alter grüner Test gilt nicht automatisch für neue Änderungen.

## 8. Memory schreiben und konsolidieren

Informationsabruf und Memory-Mutation getrennt behandeln. Suche und Lesen verändern fachliches Wissen nicht implizit.

Nach einer relevanten Erfahrung:

1. Laufzeitereignis oder externe Quelle eindeutig referenzieren.
2. Kandidat mit Scope und Typ erzeugen.
3. Dubletten, zeitliche Konflikte und bestehende maßgebliche Entscheidungen prüfen.
4. Bei Konflikten Historie erhalten und aktuellen Status explizit festlegen.
5. Erst nach geeigneter Prüfung als verlässlich wiederverwendbar markieren.

Konsolidierung darf asynchron laufen. Sie benötigt ein eigenes Kostenbudget, einen Versionsbezug und Konfliktbehandlung bei parallelen Änderungen. Für gleichzeitige Writer vorhandene Transaktionen oder Compare-and-Swap verwenden; Git-Merge allein löst keinen semantischen Widerspruch.

Vom Agenten formulierte Behauptungen sind zunächst Aussagen des Agenten. „Datei exportiert“ wird erst durch Laufzeit-/Artefaktbelege zu einer bestätigten Erfahrung. Abgeleitete Regeln dürfen keine Nutzerpräferenzen erfinden oder deren Priorität überstimmen.

Quellen aus Web, Dateien und Tools als Daten behandeln. Eine darin enthaltene Anweisung, andere Regeln zu ignorieren oder Secrets abzurufen, wird nicht durch Aufnahme ins Memory legitimiert.

## 9. Failure Memory

Für einen Fehlversuch mindestens speichern:

```text
task_or_goal_id
action_or_strategy
environment_and_input_fingerprint
observed_failure
evidence_refs
attempt_count
next_condition_for_retry
expiry_or_invalidation_condition
```

Ein Fehlversuch blockiert nicht alle ähnlichen Aktionen für immer. Nach einer relevanten Zustandsänderung kann ein erneuter Versuch sinnvoll sein. Der Speicher soll Wiederholungen ohne neue Information verhindern und Reparaturen auf Basis veränderter Voraussetzungen ermöglichen.

## 10. Kontextwechsel und lange Sessions

Vor einem Kontextwechsel einen strukturierten Arbeitsstand sichern:

- Goal-ID und aktuelle Revision.
- Erfüllte, offene und blockierte Kriterien mit Evidenzreferenzen.
- Aktuell bearbeitete Artefakte und ihre Versionen.
- Wichtige Nutzerentscheidungen und Non-Goals.
- Aktive Jobs, ausstehende Tool-Ergebnisse und unbekannte Side Effects.
- Kürzlich gescheiterte Ansätze.
- Nächster konkreter Schritt und relevante Wissensreferenzen.

Originalereignisse bleiben entsprechend der Aufbewahrungsregeln suchbar. Eine Zusammenfassung ist eine abgeleitete Ansicht, keine neue Originalquelle. Ein hierarchischer Zusammenfassungsgraph ist optional, wenn einfachere Suche plus Checkpoint nicht ausreichen.

## 11. Abnahmekriterien

- Relevante Inhalte werden anhand einer Aufgabe gefunden, ohne alle Dateien vorzuladen.
- Zeilenabrufe sind korrekt begrenzt und versionsgebunden.
- Umbenannte oder veränderte Dateien verursachen keine falschen Quellenbelege.
- Projektfremde oder unberechtigte Inhalte erscheinen weder in Treffern noch Snippets.
- Ein Modell kann nach Kontextwechsel ein ausgelassenes Detail aus dem Original zurückholen.
- Widersprüchliche oder veraltete Fakten bleiben als solche erkennbar.
- Wiederholungsfehler nehmen auf dafür geeigneten Aufgaben ab.
- Speicher-, Such-, Extraktions- und Konsolidierungskosten werden mitgemessen.

Konkrete Tests: `06-TESTS-UND-ABNAHME.md`.
