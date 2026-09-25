# Audit: Slash-Anhang und Abgleich mit Skaldirs Produktziel

Stand: 25. September 2026. Geprüftes Dokument: `outputs/Skaldir-MiMo-Implementierungsplan/skaldir-plan/11-SKALDIR-HANDOFF.md`, Abschnitt 20, Zeilen 464–583.

## Urteil

Die Inventur der Namen, Aliasse und Default-CLI-Zuordnungen ist korrekt. Als vollständige Beschreibung des Chatverhaltens und als Nachweis ausführbarer Produktfunktionen ist der Anhang noch nicht ausreichend. Es bestehen zwei wichtige Dokumentationsfehler zu Computer Use und Verfügbarkeit, fehlende Pflichtargumente, eine falsche Suchbeschreibung sowie unvollständig erfasste Sonderwege und veraltete Paketmetadaten.

Das Audit verändert weder den Skaldir-Code noch den geprüften Anhang. Es startet keine Browser, Container oder Agentenruns und benötigt keine vollständige Testsuite.

## Prüfmethode und bestätigte Befunde

Lokal gelesen: `AGENTS.md`, `src/skaldir/slash.py`, relevante Teile von `repl.py` sowie die Ziel- und Indexdateien. Mit dem vorhandenen Python aus `.venv` den echten CLI-Parser gebaut, die dadurch initialisierte Contract-Registry abgeglichen, Slash-Zeilen aufgelöst und Argumente geparst. Keine Command-Handler ausgeführt. Bytecode-Schreiben deaktiviert.

- Repository: `/home/willy/projects/gods-voice`.
- HEAD beim Audit: `a8fcaa1beb2cd3da81262e46b6378d8ecd66cc0a`; Arbeitsbaum enthält zusätzliche Änderungen. HEAD allein identifiziert die geprüften Quelldateien daher nicht ausreichend.
- SHA-256 von `src/skaldir/slash.py`: `bcd97724175a216efca8700b2f0e16d77ca5608ecd00343c29d2b05d18d98d1d`.
- 101 Tabellenzeilen: exakt dieselben Namen wie `SLASH_COMMANDS`.
- Alle Aliasse und alle 89 Default-CLI-Zuordnungen stimmen mit der Registry überein.
- Exakte Aufteilung: **89 CLI-Mappings + 8 Builtins + 4 geplante Einträge = 101**.
- Nach tatsächlicher Parserinitialisierung: **278 Contract-Einträge**, kein fehlender Contract für die 89 Default-Pfade.
- Dokument: **583 Zeilen**. Die ursprünglichen 462 Zeilen sind gegenüber `outputs/skaldir-plan/11-SKALDIR-HANDOFF.md` tatsächlich byte-identischer Präfix.
- Die vier geplanten Einträge sind aktuell `/plan`, `/rename`, `/newtask`, `/debug`. Der Modul-Docstring von `slash.py` nennt noch fünf einschließlich `/review`; dieser Kommentar ist veraltet, die Registry nicht.

Nicht geprüft: funktionale Ergebnisse aller Commands, echte Shell-Picker-Interaktionen, Modellqualität, Backend-Verfügbarkeit, Credentials, Geschwindigkeit oder Ende-zu-Ende-Erfolg. Der Contract-Abgleich ist kein Ersatz dafür.

## Befunde nach Priorität

### P1 / A01: Computer-Use-Aufrufweg wird unvollständig und irreführend dokumentiert

**Dokument:** Zeile 523 beschreibt einen „isolierten Desktop“ und ausschließlich `skaldir act check`.

**Code:** `slash.py:854` sowie `resolve()` unterscheiden Default, Unterbefehl und Freitextauftrag. `repl.py:3650` behandelt den Freitextpfad zusätzlich mit einem Live-Desktop-Freigabehaken.

Reproduzierte Auflösungen, ohne Ausführung:

| Eingabe | Tatsächliches argv |
|---|---|
| `/computer-use` | `act check` |
| `/computer-use open zen browser` | `agent run --chat-output --agent-tools "open zen browser"` |
| `/computer-use subtool list` | `act subtool list` |

**Auswirkung:** Genau der für den Nutzer wichtigste Pfad ist im Anhang nicht nachvollziehbar. Die Bezeichnung „isoliert“ kann eine Eigenschaft suggerieren, die der Chatpfad nicht allgemein garantiert. Eine Live-Desktop-Freigabe ist keine Garantie einer isolierten Umgebung.

**Korrektur:** Alle drei Modi mit konkreten Beispielen dokumentieren, Host/isolierten Desktop anhand tatsächlicher Konfiguration unterscheiden. `task_argv`, Preconditions und Freigabehaken berücksichtigen. Ein Check-Aufruf beweist keine Browsersteuerung und keine schnelle Subtool-Ausführung.

### P1 / A02: „bereit“ und „97 führbar“ sind stärker als der erhobene Nachweis

**Dokument:** Zeilen 468 und 583 sowie die Statusspalte setzen Contract-Abdeckung sprachlich mit ausführbarer Funktion gleich.

**Code:** `slash.py:1714` prüft Parserverdrahtung und optional Layer-Erreichbarkeit. Ohne Erreichbarkeitsdaten landet ein verdrahteter Eintrag ebenfalls auf `ready`. `PaletteEntry.runnable` und `repl.py:3780` zeigen außerdem: `blocked` kann eine Selftest-Warnung sein; der Command wird dennoch zur eigenen Prüfung ausgeführt.

**Auswirkung:** „bereit“ sagt hier weder aus, dass erforderliche Argumente vorhanden sind, noch dass Umgebung oder Credentials passen oder die Funktion live bestanden hat. Auch ein Builtin ohne angehängten Run kann seine Aufgabe nicht erfüllen.

**Korrektur:** Statische Dokumentstatus wie `CLI registriert`, `Shell-Builtin`, `geplant` verwenden. Daneben getrennte Nachweisstufen: Parser geprüft, Handler getestet, Integration getestet, Live-Ablauf bestätigt. Live-Palettenstatus samt Herkunft und Bedeutung separat erläutern. Die Zählung als 89 + 8 + 4 ausweisen; 97 sind nicht 97 externe CLI-Pfade.

### P1 / A03: Spalte „Benötigt“ lässt echte Pflichtargumente aus

**Dokument:** `/remember` in Zeile 544 und `/forget` in Zeile 546 zeigen `—`.

**Reproduziert im echten Parser:**

```text
/remember → memory remember → exit 1: required: text
/forget   → memory forget   → exit 1: required: id
```

**Ursache der Dokumentationslücke:** Das manuell gepflegte Registryfeld `needs` ist nicht die vollständige Argumentdefinition des Parsers. Eine leere Beschreibung beweist keine Argumentfreiheit. Ein Parser kann außerdem optionale Felder akzeptieren, die erst der Handler abhängig von State zwingend benötigt.

**Korrektur:** Pflichtpositionals und erforderliche Optionen aus dem Parser ableiten; zusätzliche semantische Voraussetzungen aus Handler und Registry ergänzen. Bei Builtins `/steer` und `/queue` Text sowie angehängten Run aufführen. `—` nur verwenden, wenn die entsprechende Kategorie tatsächlich nicht benötigt wird. Syntax und Laufzeitvoraussetzungen möglichst getrennte Spalten.

### P2 / A04: `/find` und `/search` sind unterschiedliche Aktionen

**Dokument:** Zeile 532 behauptet gleiche Aktion. Die fehlerhafte Beschreibung ist bereits in `slash.py:979` vorhanden und wurde vom Generator übernommen.

**Reproduziert:**

```text
/find hello   → code search hello
/search hello → session search hello
```

**Korrektur:** `/find` = Codesuche; `/search` = Suche in Session-Nachrichten. Beschreibung in Registry und generiertem Dokument gemeinsam korrigieren. Ein Regressionstest sollte beide Auflösungen gegenüberstellen. Das ist ein Beispiel dafür, dass „aus Code generiert“ keine semantische Richtigkeit garantiert.

### P2 / A05: Default-argv ist nicht das vollständige Chatverhalten

**Dokument:** Zeile 466 behauptet für jeden Eintrag echten CLI-Aufruf, gleiche Exitcodes und gleiche JSON-Hülle. Die Tabelle enthält gleichzeitig acht Builtins ohne CLI-Mapping.

**Code:** `repl.py:2843` behandelt Builtins; `handle_picker_verb` ab Zeile 2888 fängt unter anderem nackte `/sessions` und `/resume` als Auswahloberflächen ab. `run_slash` ab Zeile 3650 konsultiert außerdem Modell-Picker und CU-Freigabe. `/resume abc` faltet den Wert in `--session abc`. Unterbefehle ersetzen teilweise Default-Verben.

**Korrektur:** Spalte in „Default-CLI-Zuordnung“ umbenennen. Eigene Rubrik für Builtins, Picker, Unterbefehle, `value_flag`, `value_rest`, `task_argv` und Preconditions. Beispiele `/resume <id>`, `/tools show <name>` und `/computer-use <Auftrag>` aufnehmen. Die Herkunft beschreibt mindestens Registry + Resolver + REPL-Hooks + Parser + Handler, nicht ausschließlich `slash.py`.

Zusatz: Die bestehende `slash.mapping()`-Ausgabe enthält im geprüften Stand zwar `value_rest` und `precondition`, aber kein `task_argv`. Sie sollte für einen vollständigen Generator entsprechend ergänzt oder mit der Registry kombiniert werden.

### P2 / A06: Index und ausgeliefertes Dokument sind nicht synchron

**Bestätigt:** Der Leseindex im entpackten Zielpaket nennt für den Handoff weiterhin 462 Zeilen und den Hash der alten Datei. Die aktuelle Datei hat 583 Zeilen und einen anderen Hash; Abschnitt 20 fehlt im Index.

**Auswirkung:** Der ausdrücklich gewünschte versionsgebundene Zeilenabruf und die Hashprüfung funktionieren für den erweiterten Handoff nicht mehr mit diesem Index. Daneben existiert weiterhin die ursprüngliche 462-Zeilen-Kopie unter `outputs/skaldir-plan/`.

**Korrektur:** Eine maßgebliche Paketquelle festlegen, Index für diese Version neu erzeugen und das verteilte ZIP kontrolliert aktualisieren. Nicht beim nächsten Export versehentlich die ältere Kopie über den Anhang schreiben. ZIP-Konsistenz ist hier nicht erneut bestätigt worden.

### P2 / A07: Geplanter `/rename`-Grund wird mitten im Markdown-Code abgeschnitten

**Dokument:** Zeile 579 endet im Grund mit einem geöffneten Codefragment `se…`; der nächste Backtick steht bereits in der Nachbarzelle. Außerdem sind die Gründe für `/rename` und `/newtask` sichtbar abgeschnitten.

**Auswirkung:** Je nach Renderer fehlerhafte Inline-Code-Formatierung; vor allem fehlt der vollständige Grund, obwohl die Untertabelle gerade diese Information liefern soll. Die Reparatur bei `/locking` und `/run` deckt diese Zeile nicht ab.

**Korrektur:** Gründe vollständig in kurzen Absätzen unter der Tabelle oder als gezielt formulierte Zusammenfassungen wiedergeben. Roh-Markdown nicht mit einem Zeichenlimit mitten in Backticks abschneiden. Tabellen mit dem vorgesehenen Renderer prüfen.

## Abgleich mit den Nutzerprioritäten

Der Anhang inventarisiert Bedienzugänge. Er liefert allein keine neue Abnahme der hoch priorisierten Produktfähigkeiten.

| Nutzerziel | Sichtbare Zugänge | Was weiterhin belegt werden muss |
|---|---|---|
| Standard-Agent-Funktionen | `/exec`, `/find`, `/apply`, `/testfix`, `/run` | Tatsächlicher Coding-Auftrag mit richtigen Ergebnissen und Änderungsschutz |
| Vollständiges Computer Use | `/computer-use`, `/act` | Freitext- und Subtool-Pfad, Backend, Beobachtung, Eingabe, keine Fensterflut |
| Ultrafast Subtools | Versteckt unter ACT-Unterbefehlen | Erstellung/Nutzung/Recovery und Vergleich der Latenz bei gleicher Qualität |
| Docker Tool Builder | `/docker`, `/mine`, `/register` | Engine-Prüfung ist kein Build; vollständiger Build→Test→Registry→Aufruf |
| Allgemeiner Tool Builder | `/tools`, `/tools-core`, `/compose` | Tool-Erzeugung und tatsächlich erreichbarer dynamischer Aufrufweg |
| The Voice mit Jev | `/voice`, `/review`, `/jev` | Aktive ereignisgetriebene Triage, tatsächliche Intervention und getrennte Ergebnisverifikation |
| Memory/Markdown-Wissen | `/memory`, `/remember`, `/recall` | Knowledge-Suche und versionierter Zeilenabruf im normalen Agentenablauf; kein eigener Knowledge-Eintrag in dieser Palette |
| Geschwindigkeit | `/telemetry`, `/metrics`, `/warm` | Messung realer vollständiger Abläufe; ein Statuskommando ist keine Optimierung |
| UI-Polish | `/style`, Picker, Palette | Verständliche Defaults, zuverlässige Interaktion, Reaktionszeit, Animationen und Abbruch |

Fehlender eigener Slash-Name bedeutet nicht automatisch fehlende Funktion. Es muss kein weiterer Befehl für jede Funktion hinzugefügt werden. Sinnvoll sind eine kleine primäre Palette für häufige Aufgaben, Gruppen und auffindbare Detail-/Expertbefehle; der vollständige Katalog bleibt erreichbar.

## Korrektur am früheren Handoff: Jev ist mehr als ein Verifier

Beim jetzigen Audit ist das lokale `AGENTS.md` erstmals als direkte Architekturquelle verfügbar. Es beschreibt in Zeilen 21–26:

- ROUTE/Jev als schnelle Entscheidungsebene für Tool-/Subtool-Routing, Memory-Injektion, Voice-Triage, Kontextkompaktierung und Fähigkeitswahl.
- The Voice als Supervisor, der eingreift, wenn der Hauptagent das Problem nicht bereits sinnvoll behandelt.
- VERIFY als eigene fachliche Prüfung mit PASS/FAIL/UNKNOWN.

Die frühere Handoff-Tabelle „JEV / Verifier“ sollte diese Rollen nicht zusammenziehen. Präzisierung: **Jev entscheidet/triagiert; Voice supervisiert; VERIFY prüft Ergebnisse; ENFORCE setzt Abschlussbedingungen durch.** Das ist ein Architekturbeleg aus Projektregeln, noch kein Nachweis der Live-Verdrahtung.

Weitere lokale Produktkonventionen: Englisch und monochrome Darstellung. Der deutschsprachige private Handoff ist davon zu unterscheiden. Die früher vorgeschlagene farbige CLI ist eine optionale Designidee, kein bereits vereinbarter Wechsel dieser Produktkonvention. Für eine Umsetzung bestehende Vorgaben beachten oder eine explizite spätere Nutzerentscheidung dokumentieren.

## Konkreter Reparaturauftrag

1. A01–A05 im Generator und gegebenenfalls in falschen Registrybeschreibungen korrigieren.
2. Provenienz mit HEAD plus Hash der tatsächlich gelesenen Dateien dokumentieren; Datum und Branch allein reichen bei dirty tree nicht.
3. Alle 101 Namen/Aliasse/Defaults weiterhin automatisiert abgleichen; Pflichtargumente und repräsentative alternative Auflösungen separat testen.
4. Builtins und Picker in isolierten Shell-Tests prüfen, keine riskanten Commands zur Dokumentgenerierung ausführen.
5. Den Handoff hinsichtlich Jev/Voice/VERIFY präzisieren; ursprüngliche Textänderungen als solche dokumentieren statt weiterhin Byte-Identität zu behaupten.
6. Index und Paket synchronisieren, Markdown-Tabellen prüfen.
7. Für Produktfortschritt anschließend ausgewählte echte Nutzerabläufe aus E2E-01 bis E2E-12 nachweisen. 101 Palette-Einträge sind keine 101 funktionalen Abnahmen.

Abnahme dieses Dokumentationsfixes: korrekte Zählung, eindeutige statische Statuslabels, vollständige CU-Modi, korrekte Suchsemantik, dokumentierte Pflichtargumente, intakte Tabellen und aktuelle Zeilen-/Hashreferenzen. Keine vollständige Testsuite nur für Textkorrekturen notwendig.
