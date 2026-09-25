# Entscheidungsprotokoll

Stand: 25. September 2026. Jede größere Änderung hier mit reproduziertem Problem, warum die
vorhandene Lösung nicht genügt, kleinstmöglicher Alternative, Zuständigkeit, Rückweg,
Abnahme und messbarem Nutzen. Nicht bestätigte Ursachen sind als „Hypothese“ gekennzeichnet.

---

## D-01 — Dokumentation der Quelle behält ihre relativen Pfade

**Problem.** Beim ersten Migrationslauf wurden alle Quell-`docs/` nach `docs/upstream/`
umgelagert, um das neue Planpaket sauber zu trennen.

**Beobachtung.** Der anschließende Baseline-Testlauf brachte **63 Errors** in
`tests/test_gv_benchmark.py`:

```
gv_benchmark.ComparabilityError: Schema nicht gefunden:
  /home/willy/Agentic_Projects/Skaldir_Agent/docs/BENCHMARK-RUN-SCHEMA.json
```

Ursache (bestätigt, nicht Hypothese): Code, Tools und Tests referenzieren `docs/<name>` an
seinem ursprünglichen relativen Pfad — 37 verschiedene solche Referenzen wurden gefunden
(`docs/BENCHMARK-RUN-SCHEMA.json`, `docs/SECURITY.md`, `docs/CLI-REFERENCE.md`, …).

**Entscheidung.** Die Umlagerung zurücknehmen: Quell-`docs/` liegt wieder unter `docs/…`.
Nur drei Dateien liegen bewusst woanders, weil das Ziel sie bereits besaß oder sie History
sind: `AGENTS.md` → `docs/upstream/AGENTS-UPSTREAM.md`, `README.md` →
`docs/upstream/README-UPSTREAM.md`, `.hermes/plans/` → `docs/reference/upstream-plans/`.

**Rückweg.** Die Quelle ist unverändert; jeder Pfad ist im
[Migrationsmanifest](MIGRATION-MANIFEST.md) mit SHA-256 belegt.

**Nutzen.** `tests/test_gv_benchmark.py` und die dokumentreferenzierenden Tools laufen
wieder ohne einen einzigen Codeeingriff. **Kein Funktionsneubau allein wegen neuem Ordner.**

---

## D-02 — Vollbild-TUI als eigener Renderer, Ausführung bleibt wo sie ist

**Problem.** Die bestätigte UI (`03-TUI-ENTSCHEIDUNGEN.md`) verlangt eine Vollbild-TUI mit
**Chat links, Tools oben rechts, Goal unten rechts**. Der vorhandene Chat ist
`chatbar.py` (3420 Zeilen) + `chat_screen.py` (806 Zeilen) + `repl.py` (4370 Zeilen).

**Beobachtung.** `chatbar.Frame` ist ein *Fußzeilen-Frame in einem scrollenden Protokoll*:
`Frame.erase()` rechnet mit einem geparkten Cursor, `FrameStream.write()` schiebt Text über
den Frame. Das ist ein anderer Rendermodell als ein geteiltes Vollbild. Der Masterplan sagt
„Vorhandene Renderingbibliothek prüfen und erweitern; Ersatz nur bei belegter Grenze“ — die
Grenze ist hier belegt: aus einem geparkten Fußzeilen-Cursor lässt sich kein fixes
Spaltenlayout berechnen.

**Entscheidung.** `src/skaldir/tui.py` implementiert Layout und Rendering neu, **ohne**
Ausführungslogik (UI-01: „Keine zweite Ausführungslogik im UI“). Wiederverwendet werden die
geprüften Primitive: Zeilenbreiten-Arithmetik, ASCII-Faltung, Secret-/Control-Sequence-
Redaktion und die Indikator-Texte aus `desktop_lease`. Die vorhandene Chatbar bleibt der
Plain-/Pipe-Pfad.

**Abnahme.** `tests/test_tui_layout.py` (44 Tests): jede gerenderte Zeile ist exakt
`width` Zellen; Chat links, Tools über Goal; Aufklapp-Details nur bei `expanded`; Control-
Sequenzen aus Tool-Daten nie sichtbar; ASCII-Modus ohne ein nicht-ASCII-Zeichen;
Fehler als Symbol **und** Wort.

**Rückweg.** `tui.py` hängt an keinem Laufzeitpfad; die alte Chatbar bleibt unverändert
erreichbar.

---

## D-03 — Drei Approval-Modi über der vorhandenen Policy-Engine

**Problem.** APR-01 verlangt drei benannte Modi und Tab-Wechsel. `skaldir/approval.py` ist
eine echte Policy-Engine (`allow | prompt | forbid`, Grants, Banned-Prefix-Leiter,
Evidence-Ledger), kennt aber keine Modi.

**Entscheidung.** Keine zweite Policy-Engine. `approval_modes.py` bildet die drei Modi als
**Dokumente** ab, die `approval.py` lesen kann (`policy_for()`), und hält die Modiauswahl
pro Run. Die Freiheitsgrade werden in jedem Modus **enger** gesetzt als die Engine es
erlauben würde: APR-02 (Geldausgaben, irreversible Effekte) steht in allen drei Modi auf
`prompt` und ist über `ALWAYS_NEEDS_APPROVAL` nicht abschaltbar.

**Entscheidung APR-05.** Modus pro Goal/Run, konfigurierbarer Default für neue Runs
(`ApprovalModeStore.set_default`). Ein Default-Wechsel ändert bestehende Runs nicht
(APR-04). Das ist eine Implementierungsentscheidung, **keine** beantwortete Nutzerfrage.

**Abnahme.** `tests/test_tui_layout.py` (APR-Block): „Approve nichts“ ist Alias, kein
vierter Modus; unbekannte Modi werden abgelehnt statt geraten; der Zyklus ist pro Run
persistent; die Exceptions stehen in jedem Modus.

---

## D-04 — Doppel-Escape: Detektor sofort, Backend ehrlich als „nicht nachgewiesen“

**Problem.** STOP-01..05 verlangen einen globalen Doppel-Escape-Notstopp. Der Masterplan
macht ihn zu einem **frühen** Machbarkeits- und Abnahmepunkt (00-START-HIER.md Ziffer 4).

**Gemessene Umgebung** (25.09.2026, `skaldir stop report`):

| Backend | Verfügbarkeit | Messung |
|---|---|---|
| `evdev` | **blocked** | 33 `/dev/input/event*`-Knoten vorhanden, für uid 1000 nicht lesbar (root:input; der Nutzer ist nicht in Gruppe `input`) |
| `libinput` | **blocked** | `libinput list-devices` meldet für die meisten Knoten `Permission denied`; Geräte auflisten ist keine Erfassung |
| `xdg-global-shortcuts` | available | `org.freedesktop.portal.GlobalShortcuts` v2 mit `BindShortcuts`/`Activated`/`Deactivated` auf dieser Sitzung vorhanden |
| `x11-record` | available | X11-RECORD-Extension vorhanden (XWayland, `DISPLAY=:0`) |
| `agent-injection` | available | `ydotool`, `xdotool`, `wtype` — laut STOP-03 **nie** eine Stopp-Quelle |

**Entscheidung (STOP-05).** Der Notstopp wird **nicht** als fertig freigegeben. Der
Detektor (`stop_signal.DoubleEscapeDetector`) ist implementiert und getestet, aber **kein**
Backend auf diesem Host ist gezeigt worden, das zwei physische Escape-Drücke von der echten
Tastatur liefert, während eine andere Anwendung den Fokus hält.

Konkreter Blocker: Lesezugriff auf `/dev/input/event*` (Gruppe `input`) ist eine
Systemänderung, die der Besitzer vornimmt; Skaldir fordert kein `sudo` an (AGENTS.md).
Die Portal-Route bindet fokusunabhängige Shortcuts, garantiert aber **nicht**, dass
„Escape zweimal innerhalb von 500 ms“ als ein Trigger ausdrückbar ist — das ist im
Report als `double_escape_sequence: unproven` festgehalten, nicht behauptet.

**Ersatzsicherung.** Ein alternativer Stopptaste/-Indikator ist eine *zusätzliche* Sicherung,
kein stiller Ersatz für den Nutzerwunsch.

**Abnahme.** `tests/test_stop_signal.py` (23 Tests): Autorepeat zählt nie als zwei Drücke;
ein synthetisches Escape **bricht** die Folge; eine Stoppfeuerung erfolgt einmal pro Folge;
das Fenster ist konfigurierbar; der Report behauptet `double_escape_proven: false`.

---

## D-05 — Desktop-Lease als eigene Ressource, nicht als zweiter Run-Store

**Problem.** DESK-01..04 verlangen exklusive Host-Desktop-Steuerung mit Fencing und
sichtbarem Indikator. `run_supervisor.RunStore` hat bereits `grant_lease`/`heartbeat`/
`release_lease`/`wait`/`hold`/`wake` plus Fencing für *Runs*.

**Entscheidung.** Keine zweite Run-Registry (AUD-02). `desktop_lease.py` bewacht eine
**andere** Ressource: den physischen Eingabepfad. Verbunden sind die beiden nur über
`run_id`. Der Fencing-Generation-Token wird am Eingabe-Adapter geprüft
(`DesktopLeaseStore.fence`) und bei jedem Eigentümerwechsel, Stopp und Resume erhöht —
dadurch ist DESK-02 erfüllt: eine verspätete Aktion wird verworfen, nicht ausgeführt.

**Abnahme.** `tests/test_desktop_lease.py` (24 Tests), davon vier ausschließlich zum
Verwerfen: Stale-Input nach Stopp, nach Freigabe, nach Takeover und nach Crash-Recovery.

---

## D-06 — Logo-ASCII-Kunst neu abgeleitet

**Problem.** `tests/test_logo_art.py::test_regenerating_reproduces_the_checked_in_art`
schlug nach der Migration fehl: die eingecheckte Datei `assets/cli-logo.txt` trägt in
Zeile 3 den **absoluten Quellpfad** `/home/willy/projects/gods-voice/src/skaldir/assets/skaldir-logo.png`
als `# source:`-Vermerk.

**Entscheidung.** Die Kunst ist eine Ableitung („same PNG in, byte-identical file out“).
Sie wurde im neuen Workspace neu erzeugt (`skaldir.logo.regenerate`), damit die Datei den
tatsächlichen Herkunftspfad nennt. Damit entfällt zugleich die letzte absolute Referenz auf
das alte Checkout in den Assets.

**Rückweg.** Die PNG-Quelle ist unverändert; die Ableitung ist deterministisch.

---

## D-07 — Nicht übernommene Altlasten

**Problem.** Der Auftrag verlangt, Caches, Credentials, private Laufzeitdaten und
nachweislich ungenutzte Altlasten zurückzulassen, ohne die Quelle zu verändern.

**Entscheidung.** Ausgeschlossen und begründet im
[Migrationsmanifest](MIGRATION-MANIFEST.md): `.git`/`.venv`/`node_modules`/Caches,
`events.db` + `cu_runs.jsonl` (Live-Runtime-Datenbanken), `outputs/` (216 MB generierte
Test-/UI-Audit-Outputs, nur in Dokstrings referenziert), `ui-artifacts/` (8,4 MB
Screenshots alter UI-Stände), `audit/evidence/` (199 rohe Probe-Logs). Nicht übernommene
Dateien wurden **nicht gelöscht**.

**Nachweis der Nichtbenutzung.** `grep` über `src/`, `tests/`, `tools/`, `scripts/`,
`pyproject.toml`, `.github/`: `ui-artifacts/` wird nur von `tools/build_artifact_manifest.py`
referenziert; `outputs/` nur als Ausgabeverzeichnis von Probe-Skripten und in drei
Dokstrings. `audit/evidence/` hat **keinen** Code-Referenzenten.

---

## D-08 — Secret-Prüfung des tatsächlich zu committenden Bestands

**Durchgeführt** vor dem Initialcommit über 1123 gestagte Dateien mit neun Mustern
(GitHub-PAT, fine-grained PAT, OpenAI-/Anthropic-/AWS-/Slack-Tokens, Private-Key-Blöcke,
Bearer, URL-Basic-Auth, generische `api_key`/`secret`/`token`/`password`-Zuweisungen).

**Ergebnis.** 21 Treffer, **alle** dokumentierte Test-Platzhalter bzw. Fachprosa:
`sk-abcdefghijkl…` in `docs/SECURITY-BOUNDARIES.md` (Redaktionsbeispiel), `test-token`/
`test-one`/`test-master`/`a-cons…` in Tests, `sk-live_…` als dokumentierte Musterform in
`tests/test_system_one_sanitizer.py`. Zwei Fundstellen der Form ``vnc://<user>:<pass>@<host>``
in `audit/findings.json` sind **Fundstellenbeschreibungen** einer Redaktionslücke, keine
Zugangsdaten. Kein `.env`, kein `.pem`, keine `.key`, keine Datenbank im Commitbestand.

---

## D-09 — `docs/masterplan/07-BROWSER-UPGRADE.md` gehört dem Nutzer

**Beobachtung.** Während Sitzung 1 lag in `docs/masterplan/` eine neue Datei
`07-BROWSER-UPGRADE.md` (140 Zeilen, Recherche zu Browser-Backends) mit nachgezogenem
`06-LESEINDEX.md` und ergänzter Tabellenzeile in `00-START-HIER.md`. Da die Herkunft nicht
dokumentiert war, wurde sie zunächst als mögliche stille Paketerweiterung behandelt und
aus dem Planpaket herausgelöst.

**Klärung durch den Nutzer (25.09.2026):** „Das ist ein Plan an was ich arbeite von mir.“
Die Datei ist also **Nutzermaterial**, kein Agentenfund, und gehört an ihren Platz.

**Entscheidung.** Vollständig wiederhergestellt:
`docs/masterplan/07-BROWSER-UPGRADE.md` im Wortlaut unverändert (16 416 Bytes, Kopfzeile
`# Browser-Upgrade für Skaldir`), die Tabellenzeile in `00-START-HIER.md` wieder ergänzt,
`06-LESEINDEX.md` mit demselben Format neu gerechnet. Der erzeugte Index unterscheidet sich
vom committed Stand **nur** in den erwarteten Stellen: Zeilenzahl 57→58 für `00-START-HIER.md`
und der neue Abschnitt für `07`; die Dateien 01–05 erzeugen byte-identische Zeilen, was das
Format bestätigt.

**Grund.** Nutzerpläne stehen über der Sorgfaltsregel eines Agenten. Der Auftrag lautet
zudem, **zuerst die anderen Tasks fertigzustellen** — der Browser-Vorschlag wird nicht
weiterbearbeitet, sondern nur an seinem Ort belassen.

**Rückweg.** Keine inhaltliche Änderung an der Nutzerdatei; nur Position und Index sind
konsistent.

---

## D-10 — `tool_search` reicht den typisierten Vertrag durch

**Reproduziertes Problem.** Die P0-Lücke der Testdatei
`tests/test_tool_search_integration.py`: „tool_search returned tool names but the model
couldn't call them“. Gemessen: Suchzeilen trugen `name`, `effect`, `limits`, `unknown`,
aber **keine** Argumentform.

**Warum die vorhandene Lösung nicht genügte.** `capability_mining.contract_metadata`
erzeugte `inputs: ["path:string"]`. Das ist ein **Etikett** („ein String namens path“),
kein Aufrufvertrag — kein Typenumfang, keine Pflichtfelder, keine `additionalProperties`-
Regel. Ein Modell kann damit raten, nicht aufrufen.

**Wo der Vertrag wirklich liegt.** Das Manifest, das `capability_mining` liest, benennt
ihn selbst: `contract: <descriptor-pfad>` mit dem Hinweis „the typed contract lives in the
descriptor file named above“. Der Descriptor trägt `input_schema`/`output_schema` als
echtes JSON Schema (bei `read_file` z. B. `limit: integer, 1..5000` plus `max_bytes`,
`offset`, `expected_revision`). Er wurde nur nie weitergereicht.

**Kleinstmögliche Alternative.** Zwei Zeilen Zuständigkeit, keine neue Registry:
`capability_mining._typed_contract()` liest den benannten Descriptor,
`discovery._public_row()` gibt beide Schemata an die Zeile weiter.

**Bewusste Unterlassung.** Kein Schema wird **erfunden**. Ohne Descriptor oder ohne
Schema bleibt der Schlüssel weg — dafür gibt es
`test_the_call_contract_is_absent_rather_than_invented_when_none_is_declared`. Ein
leeres `{}` zählt nicht als Vertrag; der Test verlangt `properties` oder `required`.

**Abnahme.** `test_tool_search_gives_the_model_a_call_contract` (positiv, verschärft:
`read_file` muss `path`/`limit`/`offset` deklarieren) und der Negativtest oben. Gemessen
9/9 Metadateneinträge und alle Suchzeilen mit `input_schema`.

**Messbarer Nutzen.** Ein Entdeckungstreffer ist jetzt handlungsfähig: der nächste
Aufrufweg steht in derselben Zeile. Das ist TOOL-02 („`tool_search` liefert einen gültigen
nächsten Aufrufweg“) und schließt die P0, für die diese Testdatei überhaupt entstand.

---

**Privatsphäre-Hinweis.** Dokumentationsartefakte unter
`docs/artifacts-94-token-economy/` enthalten absolute lokale Pfade
(`/home/willy/projects/gods-voice/…`). Das ist ein Maschinenpfad, kein Geheimnis; im
privaten Repository akzeptabel und hiermit vermerkt.
