# Skaldir Agent — alle Features im Detail

Stand: **v1.4.0** · 472 Tests · bandit 0 · CI grün · Live-App gebaut
(`~/.hermes/godsv/app/linux-unpacked/gods-voice`)

Skaldir Agent ist ein Agent-Harness auf Hermes-Basis: dieselbe Engine, ein eigenes
Gesicht (goldene Skin, eingebackenes Panel, eigene App-Instanz) und ein Satz
nativer Features, die **im CLI-Tool und in der Desktop-App** verfügbar sind.

---

## 1. The Voice — Live-Lenkung (Pausen & Redirects)

**Das Problem:** Ein Agent, der in einer Schleife festhängt, verbrennt Zeit und
Tokens — und merkt es selbst nicht zuverlässig.

**Was es tut:** Skaldir Agent erkennt Loop-/Burst-Signale, **pausiert** den Agenten
mitten im Lauf, schickt einen isolierten Research-Run los, verdichtet dessen
Ergebnis zu einer Richtungsänderung und **flüstert** sie dem Agenten als
nächsten Schritt zu — dann läuft er weiter.

**Mechanik:**
- Auslöser sind **konkrete Signale** (2× wiederholte Tool-Aufrufe / Bursts),
  ein Cooldown (60 s) und ein Budget (max. 4 Pausen/Stunde). Kein Zufall, kein Dauerfeuer.
- Ablauf: `pause_requested → research_started → research_done → pause_delivered →
  resume`. Zustellung **genau einmal** (bedingter Übergang `ready → delivered`),
  abgesichert durch SQLite-Leases + Heartbeat + Watchdog.
- Der Agent bekommt **nie rohe Recherche**, sondern einen kondensierten Redirect
  (Problem / Evidenz / Richtung / Vermeiden); die Prompt-Tier ist klein gewählt
  (Latenz), Hold-Fenster über `GODSV_PAUSE_WAIT` (Default 18 s).
- Strikt getrennte Kanäle: `voice` / `user` / `safety` — ein Redirect kann keine
  Nutzer-Nachricht fälschen.

**Wo:** wirkt im Agenten-Loop (Plugin-Hooks, Terminal **und** Desktop-App gleichermaßen;
`GODSV_MODE=shadow|whisper|off` steuert die Schärfe). Anzeige: Desktop-Panel
→ **Live** (KPIs „Pausen aktiv/zugestellt") und **Pausen** (Historie, Versuche, TTL).

**Belegt:** Live-E2E auf Willys Maschine: `pause_requested` (Step 11) →
`pause_delivered` (Step 17), Recovery **27,4 s**, exactly-once geprüft, Redirect
im Transcript sichtbar — plus Unit-Tests (Teil der 351).

---

## 2. Completion Guards — „fertig" nur mit Beleg

**Das Problem:** Agenten behaupten gern „erledigt", ohne es geprüft zu haben.

**Was es tut:** Bevor die Antwort als final gilt, prüft Skaldir Agent, ob die
Behauptungen durch **echte Ausführungen** gedeckt sind — fehlt der Beleg, kommt
eine Nachforderung, notfalls ein sichtbarer Hinweis-Footer.

**Mechanik:**
- Zwei Kanäle: `pre_verify` (fordert eine Verifikation an, solange der Lauf
  weitergeht) und `transform_llm_output` (setzt den `[voice·guard]`-Footer in
  die finale Antwort — wirkt auch ohne weiteren Tool-Call).
- **Deterministische Prüfung zuerst**, ein LLM-Judge ergänzt nur (fail-open).
- Belegregel: **nur Ausführungs-Tools** (terminal/exec) zählen als Verifikation —
  Schreiben/Patch zählt nie.
- Zähler (`guard_check`, `claim`) landen im Event-Store und im Panel.

**Wo:** wirkt im Agenten-Loop; Desktop-Panel → **Live** (Guard-/Claim-KPIs,
Ereignis-Kanal), **Pausen** (gefilterte Guard-Events).

**Belegt:** Live-Smoke: Nudge → Agent verifizierte → Footer im finalen Text
(Trace-belegt); 45er-Test-Batch + `test_guard.py`; bewusster Regressionsfall
(„Schreiben zählt als Beleg") wurde als Bug erkannt und durch `_EXEC_TOOLS` gefixt.

**Seit v1.4.0:** Der Guard ist Teil des **Strict Completion Supervisors** (§20):
Die Claim-Prüfung läuft bei *jedem* Versuch (kein Einmal-Nudge mehr), und über
Claims hinaus entscheiden echte Gates über Repository, Tests, Build, Integration,
Fake-Daten, Security und Definition of Done. `INCOMPLETE` beendet den Lauf nicht.

---

## 3. Memory — Incidents & Fakten mit Herkunft

**Das Problem:** Derselbe Fehler darf nicht dreimal diagnostiziert werden; Recherche
ohne Quelle ist wertlos.

**Was es tut:** Zwei Speicher: **Incidents** (wiederkehrende Fehlerklassen,
per Signatur dedupliziert, mit Zähler/Confidence/Status) und **Notizen**
(Recherche-Aussagen mit Quelle, Trust-Level und Gültigkeit). Dazu
FTS5-Volltextsuche.

**Mechanik:**
- SQLite mit WAL + FTS5 (`godsv/memory.db`), thread-safe.
- `nightshift` konsolidiert eine beendete Session in die Memory (Default Dry-Run).
- Schreibpfade bleiben **propose-first** — die Memory füllt sich kontrolliert,
  nicht per Autopilot.

**Wo:**
- CLI: `godsvoice memory-stats`, `godsvoice memory-search "…"`, `godsvoice nightshift`
- Desktop: Panel → **Memory** (Incidents-Tabelle + Notizen-Tabelle, live)

**Belegt:** `test_memory.py`; Panel-Payload-Tests (`/memory`, defensiv gegen
fehlende DB, nie Secrets); Node-Smoke rendert beide Tabellen; live zeigt das
Panel die realen Incidents der Maschine.

---

## 4. Skill-Mining — Wissen aus Wiederholung

**Das Problem:** Wiederkehrende Tool-Ketten („immer erst X, dann Y") sind
kostbar — aber niemand schreibt sie auf.

**Was es tut:** Erkennt eigenständige Ketten, die **≥ 3× über ≥ 2 Sessions**
auftreten, und erzeugt daraus **Skill-Kandidaten** (SKILL.md-Entwürfe).

**Mechanik:**
- Kandidaten durchlaufen einen Lebenszyklus (`proposed → promoted → …`) und
  werden **nie automatisch aktiviert** (propose-first).
- Konsumption wird gezählt (`applied` / `succeeded`) — man sieht, ob ein Skill
  wirklich benutzt wird und funktioniert.

**Wo:** CLI: `godsvoice skills mine | list | promote …` ·
Desktop: Panel → **Skills** (Kandidaten, Lifecycle, Zähler, read-only).

**Belegt:** `test_skills.py`; Panel-Smoke mit Kandidaten-Daten; Panel-Payload
`/skills` in pytest.

---

## 5. Tool-Suiten — propose-first, gated, MCP-exportierbar

**Das Problem:** Neue Werkzeuge (z. B. ein CLI-Tool) sollen dem Agenten zur
Verfügung stehen — aber nur geprüft, nicht auf Zuruf.

**Was es tut:** Der Agent kann Tool-Suiten **vorschlagen**; Freischaltung läuft
über Gates: **Secret-Scan → Import-Test → Tests → Determinismus-Check**. Erst
wenn alles grün ist, wird die Suite „approved".

**Mechanik:**
- `tools propose --spec …` → Gates → `tools approve` (nur nach vollständigem
  Gate-Pass).
- Genehmigte Suiten lassen sich als **MCP-Server** exportieren (`godsvoice mcp`),
  damit auch andere Harnesses sie nutzen können.
- **Usage-Ledger** (seit v1.3.5): MCP- und CLI-Wrapper zählen jeden Aufruf
  (`tool_usage.jsonl`); Fehlertexte werden redigiert, Payloads nie gespeichert.

**Wo:** CLI: `godsvoice tools …`, `godsvoice mcp …` ·
Desktop: **Tools-Tab** mit Installed (inkl. Aufrufe-Spalte), Capability
Requests, Build Queue, MCP-Servern und Drilldown ins Tool-Detail
(Manifest, Gates, Grants, **Nutzung & Fehler**, Aktionen).

**Belegt:** Tool-Tests in der Vollsuiten; MCP-Tests laufen in CI (`.[dev,mcp]`);
Usage-Ledger-Tests (MCP-Roundtrip + Panel), Live-Beweis im echten Home.

---

## 6. Computer Use — isolierte Ausführung

**Das Problem:** Ein Agent, der Maus/Tastatur/Browser bedient, darf nicht die
echte Maschine gefährden.

**Was es tut:** Computer-Use-Aufgaben laufen in **isolierten Runnern**
(rootless Podman/bwrap). Die Reihenfolge ist fest: **API > DOM > Vision** —
erst die stabilste, günstigste Kontrolle, Vision nur als Eskalation.

**Wo:** CLI: `godsvoice cu …` ·
Desktop: **noch kein eigener Tab** (siehe Roadmap).

**Belegt:** CU-Isolationstests (Sandbox-Kind ohne Master-Key — Canary-Test);
Runner-Start live verifiziert.

---

## 7. Treasury — Testgeld, Handles, Grants

**Das Problem:** Ein Agent, der „Geld verwenden darf", darf nie echte Karten-,
Bank- oder Secret-Daten sehen.

**Was es tut:** Eine **reine Mechanik** für staged money: der Agent kennt seine
**Allowance** (wie viel er verwenden darf) und stellt **Spend-Requests**, die
standardmäßig ein Mensch freigibt. Es gibt **keinen echten Zahlungspfad im Code** —
alles Testgeld.

**Mechanik:**
- **Vault:** Secrets liegen Fernet-verschlüsselt (Schlüssel = scrypt aus dem
  Master-Secret, nie gespeichert). Der Agent sieht nur **Handles** (`secret://name`).
- **Grants + TOTP:** Freigabe eines Handles ist an einen Grant gebunden
  (RFC-6238-TOTP); Auto-Approve nur mit verifizierbarem Intent (Stufen 1/2);
  Stufe 0 heißt: Mensch bestätigt zuerst.
- Härtung: Beträge werden auf Endlichkeit/Positivität validiert (NaN/∞/≤0 →
  denied), Dateien 0600 / Verzeichnisse 0700, `flock` gegen parallele Prozesse,
  Scrub-Varianten (`[REDACTED:treasury]`) auch URL-encodiert.
- Der Master-Key erreicht **nie** das Sandbox-Kind (Canary-getestet).

**Wo:** CLI: `godsvoice vault …`, `godsvoice spend request|approve|list|allowance` ·
Desktop: Panel → **Treasury** (Spends, Grants, Allowance — read-only, nie Vault-Material).

**Belegt:** 32 Treasury-Hardening-Tests; Panel-Payload-Tests (keine Secrets,
kein Ciphertext in der API); Live-Allowance im Panel.

---

## 8. Safety & Proof — eval, scan, doctor

- **`godsvoice eval`** — deterministische Eval-Episoden (CI-able, 15/15 grün);
  Release-Pipeline prüft damit messbar statt nach Gefühl.
- **`godsvoice scan`** — Secret-Scanner für Dateien/Stdin (exit 1 bei Fund);
  läuft als Gate in Suiten **und** auf Doku.
- **`godsvoice doctor`** — 10 Release-Health-Checks (Deployment, Prompts,
  Runtime, optionale Tools); live exit 0.
- **CI**: ruff → pytest (351) → bandit (medium+ = 0) bei jedem Push; Panel-Load-
  Smoke (35 Checks) läuft in der CI.

---

## 9. Plugin & Hooks — der Klebstoff zur Engine

Das Skaldir Agent-Plugin hängt sich über **10 Hooks** in die Hermes-Engine
(Tool-Results, LLM-Output, Pre-Verify, Sessions u. a.), spiegelt Guard-/
Pause-/Skill-/file-edit-Ereignisse in den Event-Store und ist **robust by
contract**: **kein Hook wirft je** (per Test `test_plugin_robustness.py`
abgesichert) — ein defektes Feature darf den Agenten nie lahmlegen.

---

## 10. Desktop-App (nativ) — Skaldir Agent als eigenes Programm

**Was es ist:** Ein **eigener Build** der Desktop-App aus dem Hermes-Quellbaum
mit eingebackenem Skaldir Agent-Overlay — dieselbe Designsprache, eigenes Gesicht.

**Was eingebacken ist:**
- **Panel** als gebündeltes Plugin (`src/plugins/godsv/`) — **ab Boot aktiv:
  kein Installieren, kein „Reload plugins", keine Settings-Schritte.**
- **Gold-Theme `gods-voice`** (Logo-Gold `#f5be36` auf warmem Charcoal, light+dark)
  als App-Default; daneben wählbar in Appearance.
- **Splash**: Ohr-Logo über „SKALDIR AGENT" (statt „HERMES AGENT").
- **Identität**: App-Name „Skaldir Agent", `appId dev.godsvoice.app`, Binary
  `gods-voice`, Fenster-/HUD-/Update-Titel, eigenes Icon.
- **Nous-Portal-Wortlaut entfernt** („Sign in to Nous Portal" etc. neutralisiert,
  nous-girl-Asset raus) — Links bleiben funktional.

**Panel-Tabs (alle read-only, live aus dem Runtime-Verzeichnis):**

| Tab | Zeigt |
|---|---|
| Live | Pausen-KPIs, Guard-/Claim-Zähler, Ereignis-Typen, letzte Events, Allowance |
| Pausen | Redirect-Kette + Pause-Historie (Session, Status, Versuche, TTL) |
| Memory | Incidents + Recherche-Notizen |
| Skills | Skill-Kandidaten mit Lifecycle + Nutzung |
| Treasury | Spend-Requests, Grants (`secret://`-Handles), Testgeld-Hinweis |
| System | Runtime-Pfad, Snapshot-Zeit, Grenzen, Engine-Hinweis („Hermes") |

**Instanz-Isolation & Koexistenz:** Die GV-App hat eigene Electron-userData
(eigener Single-Instance-Lock), eigenen App-Namen und Deep-Link (`hermes://open/
gods-voice`). Sie teilt Sessions/Runtime mit Hermes (gleiches `HERMES_HOME`),
**kann aber nie ein Hermes-Fenster fokussieren oder umleiten** — beide laufen
konfliktfrei nebeneinander.

**Update-Sicherheit:** Build = Hardlink-Spiegel des Quellbaums + **24 exakt
verankerte Patches** (`app-overlay/patch_app.py`). Verschiebt ein Hermes-Update
eine Zeile, bricht der Build **laut** ab statt halb-gebrandet zu shippen. Der
Quellbaum kann nie korrumpiert werden (atomare Writes brechen Hardlinks —
testgesichert). Neubau: `godsvoice desktop build-app` (~75 s).

**Icon & Window-Match:** `StartupWMClass` folgt dem App-Namen (GNOME findet das
Icon), Icon-Cache wird beim Install aktualisiert.

---

## 11. CLI-Tool — die Kommando-Oberfläche

- **Welcome-Screen** im Claude-Code-Stil: dein Logo (ANSI, eigene Farben),
  Titel/Subtitle, Sektionen „Loslegen"/„Erkunden", Beispiele; respektiert
  `NO_COLOR`; Version dynamisch aus den Paket-Metadaten.
- **19 Bereiche:** `init`, `pact-show`, `ledger-show`, `demo`, `eval`, `scan`,
  `prompts`, `memory-stats`, `memory-search`, `nightshift`, `metrics`, `skills`,
  `tools`, `mcp`, `cu`, `vault`, `spend`, `desktop`, `doctor`.
- **`desktop install|status|start|uninstall|build-app`** — Launcher (.desktop +
  Icons in hicolor), Status (CLI, Plugin, Panel, Skin, App-Build, Instanz),
  Start (fokussiert die laufende GV-App ins Panel), Build der nativen App.
- **`doctor`** — 10 Checks, exit 0 heißt release-gesund.
- Der Installer **fasst das globale Hermes-Theme nie an** (Skin ist Opt-in:
  `hermes config set display.skin gods-voice`).

---

## 12. Konfiguration (Umgebungsvariablen)

| Variable | Default | Bedeutung |
|---|---|---|
| `GODSV_GUARD` | `1` | Completion-Guard-Kanäle an/aus |
| `GODSV_PAUSE` | `1` | Live-Lenkung (Pausen) an/aus |
| `GODSV_PAUSE_WAIT` | `18` | Hold-Fenster (Sekunden) für den Redirect |
| `GODSV_MODE` | `whisper` | `shadow` (nur beobachten), `whisper` (lenken), `off` |
| `GODSV_LLM` / `GODSV_TRIAGE` | `1` | LLM-Judge bzw. Triage-Zusatzkanäle |
| `GODSV_VAULT_KEY` | — | Master-Secret für den Vault (nie im Klartext ablegen) |
| `GODSV_TRACE_FILE` | — | Trace-Ziel für Belege |

Die Desktop-App läuft mit `HERMES_DESKTOP_USER_DATA_DIR` (eigene Instanz) und
`HERMES_DESKTOP_APP_NAME` (eigener Name) — der Launcher setzt beides automatisch.

---

## 13. Testmatrix & Belege

| Bereich | Womit belegt |
|---|---|
| Gesamtsystem | **351 pytest-Tests**, bandit medium+ = 0, ruff clean, CI grün (Push-Gate) |
| Live-Lenkung | E2E-Lauf: Pause→Research→Redirect→Resume, 27,4 s, exactly-once |
| Guards | Live-Smoke (Footer im finalen Text), Unit-Tests, Regressionsfälle |
| Panel (Desktop) | Node-Load-Smoke **35 Checks** (alle 4 Contributions, jeder Tab, echte Klick-Simulation), FastAPI-Payload-Tests |
| App-Build | Overlay-Tests: Anker müssen exakt treffen; Hardlink-Sicherheit (Quellbaum unveränderbar); Binär-Erkennung |
| Native App live | CDP-DOM-Probe: `title="God's Voice"`, Gold `#f5be36`, 6 Tabs, Splash „GOD'S VOICE"+Logo |
| Isolierter Bau | Live: Mirror+Overlay+Pack in ~75 s, Deploy nach `godsv/app/` |
| Treasury | 32 Hardening-Tests (Perms, flock, Scrub, NaN, Grant-Pflicht, Canary) |
| CLI | Hilfe-Smoke über alle Subcommands, doctor exit 0, Welcome-Rendering |

---

## 16. Voice-Context: bounded, frisch, instrumentiert (v1.3.1)

Jede Voice-Entscheidung baut ihren Kontext **neu** aus den Stores (Roh-Events
bleiben Source of Truth) — keine wachsende Voice-Conversation, keine
„Summary-of-Summary"-Kette; ein Neustart rekonstruiert denselben Kontext.
Es gibt zwei Profile: **Watcher** (≤ 2500 Zeichen, 10 Steps — klein und
billig) und **Investigator** (≤ 4000 Zeichen, 24 Steps — frisches, größeres
Fenster pro Untersuchung). Jede Sektion hat Quotas; Kürzungen stehen explizit
als `…[gekürzt]` im Text. **Instrumentiert**: `stats()` trennt **MEASURED**
(Zeichen/Sektionen/Truncations) von **ESTIMATED** (Tokens ≈ chars/4, gelabelt);
der Research-Run schreibt `voice_context_built` in den Event-Store (sichtbar
im Panel-Stream). Das Investigator-Ergebnis folgt dem Schema
`problem / evidence / why_current_direction_is_bad / direction / avoid /
confidence` (Redirect-Prompt).

## 17. Model-Routing (v1.3.1, Beweis)

`tests/test_model_routing.py` belegt ohne echten Provider-Call: Rollen aus
`~/.hermes/godsv/models.yaml` kommen in den Side-LLM-Kwargs an — zwei
unterschiedliche Rollen routen auf unterschiedliche Provider/Modelle, das
Tier-Mapping (small→watcher, mid→investigator) greift, ohne Rollen wird kein
Override angefordert, ein geschlossenes Capability-Gate führt **fail-open**
zum Retry ohne Override, und Rollen-Änderungen wirken auf den nächsten Call.
End-to-End-Persistenz: Panel → Speichern → Datei → nächster Call (kein Neustart
nötig; die Datei wird pro Call gelesen).

## 18. Capability-Gap-Flow: fehlt ein Werkzeug, baut der Agent es selbst (v1.3.2)

```text
Agent meldet Capability (godsvoice tools request …)
↓ Plugin erkennt den Request am Tool-Call → capability_gap-Signal
↓ PAUSE (bestehende Voice-Pipeline inkl. Kostenmodell-Gate)
↓ ToolBuilder als Researcher: Quellcode → Gates (bwrap) → Registrierung
↓ Redirect: tool_created {Name, how_to_use, Constraints}  ← nie das Transkript
↓ RESUME — der Agent nutzt das Tool im selben Task
```

- `godsvoice tools request --capability … --source … --tests …` (record) und
  `godsvoice tools fulfill [id]` (führt die Pipeline sofort aus, auch ohne
  laufende Session).
- Ein fehlgeschlagener Build liefert trotzdem einen Redirect (Grund + nächster
  Schritt) statt stillem Pause-Ende.
- Im Tools-Tab als **Capability-Request-Karte** sichtbar (seit v1.3.3): Status,
  Kind, Secret-Handles, Build-Stufen, klickbares Ergebnis-Tool.
- Bewiesen: `tests/test_capability_flow.py` (echte Pause + Build im
  Research-Thread + `pause_delivered` mit Tool-Namen), Live-CLI
  (Build → `ready` → Wrapper-Ergebnis `{"converted": 270.0}`) und echte App
  (Karte rendert mit echtem Request, kein Klartext im DOM).

## 20. Strict Completion Supervisor (v1.4.0) — „fertig" entscheidet die Voice

**Das Problem:** Ein Agent, der sich selbst für fertig erklärt, beendet Arbeit,
die noch nicht fertig ist. Genau das darf nicht mehr passieren.

**Der Mechanismus (Mandatory Completion Loop):**

```text
Main Agent arbeitet
  → REQUEST_COMPLETION (mehr darf er nicht)
  → Voice verifiziert gegen echte Fakten (Gates)
  → INCOMPLETE: Redirect → Main Agent arbeitet automatisch weiter (kein User-„continue")
  → … bis VERIFIED_COMPLETE oder ein echter Endzustand
```

**State Machine:** `WORKING → COMPLETION_REQUESTED → VERIFYING →`
`VERIFIED_COMPLETE | INCOMPLETE(→REDIRECT→WORKING)`; Endzustände außerdem
`BLOCKED_USER`, `BLOCKED_EXTERNAL`, `FAILED_UNRECOVERABLE`, `USER_CANCELLED`,
`SHIPPED_WITH_USER_OVERRIDE`. **`INCOMPLETE` ist kein Endzustand.**

**Gates (alle über echte Fakten, nie über Behauptungen):** evidence (nur wirklich
ausgeführte Checks, frisch nach der letzten Änderung), tests (Code geändert ohne
Testlauf → BLOCKER), build, **integration** (neues Modul, das niemand importiert,
endpoint ohne Aufrufer → BLOCKER), **fake-completion** (TODO/placeholder/
skipped-tests im Diff), **security** (Secret im Diff), cleanup, **definition-of-done**
(blocking Kriterien brauchen ihren Nachweis; Nice-to-haves nur MINOR — kein
künstlicher Endlos-Loop).

**Antwort auf wiederholte Rejections (§31):** gleicher Blocker-Fingerprint erneut →
`escalate` → Strategiewechsel/Research statt Blind-Retry. Nie „4× fehlgeschlagen,
also fertig".

**Wo:** `src/godsv/completion.py` (Kern), Plugin-Hook `pre_verify` (jeder Versuch,
whisper-Modus steuert; shadow beobachtet nur), Panel-Tab **Completion** (Goal +
Requirement-Checkliste ✓/✗, Produkt-Status-Grid, Verify now / Begin goal /
User-Override, Timeline, Metrics), CLI `godsvoice completion status|history|begin|override`.

**Belegt:** `test_completion.py` (22) + `test_completion_loop_e2e.py` (13) —
inkl. Mandatory Full-Loop (2× Reject → automatisches Weiterarbeiten → VERIFIED),
§41-Matrix, Crash-Safety, §44-Invarianten; 6 Plugin-Integrationstests.

## 21. Capability-Mining (v1.4.0, Add-on) — wiederkehrende Arbeit wird Werkzeug

Erkennt über Sessions hinweg wiederkehrende Capabilities — wiederholte
Capability-Requests und wiederkehrende Kommandos/Verification-Flows — und legt
**Kandidaten** an: `capability, occurrences, sessions, current_workaround,
suggested_type (skill | cli | mcp | testing_tool), expected_benefit,
required_secrets`.

- **Typwahl:** Verification-/Test-Flows → **testing_tool**, API/Provider-Arbeit →
  **mcp**, kleine deterministische Funktion → **cli**, Prozeduren → **skill**
  (läuft über das bestehende Skill-Mining, nicht als Tool).
- **Promotion:** `godsvoice tools promote <id>` erzeugt einen Capability-Request in
  der **bestehenden** Gates-Pipeline — kein zweites System, kein Tool-Sprawl.
- **Testing-Tools brauchen Negativtests:** `validate_testing_tool` fährt das Tool
  gegen ein funktionierendes **und** ein absichtlich kaputtes Ziel — ein Tester,
  der am kaputten Ziel „PASS" meldet, ist `invalid` und darf nie als
  Completion-Evidenz gelten.
- **CLI:** `godsvoice tools mine | candidates | promote <id>`.

## 19. Roadmap — was noch fehlt

1. ~~Panel-Tabs für Computer Use und Eval-/Metriken-Detail~~ — seit v1.3.4
   gebaut (CU: Runner/Policy/Demo/Verlauf; Evals: Episoden/Run/§20-Metriken).
   Navigation ist damit komplett (Spec §22).
2. **CU Vision/Screenshot-Runner** (aktuell API > DOM; Vision-Eskalation folgt).
3. **Icon**: technisch gefixt (WM-Class-Match) — visuelle Abnahme in GNOME offen.
4. **iPad-Ansicht** für Freunde (außerhalb des Fedora-Releases).
5. ~~Capability-Request-Karte~~ — seit v1.3.3 im Tools-Tab (Status, Handles,
   Build-Stufen, Ergebnis-Tool). ~~„Test credential safely"~~ — seit v1.3.6
   (Broker-Selftest: Entschlüsselung + isolierte Injection, kein Netzcall).
6. By design: Administrative Panel-Aktionen (Modelle, Tool Test/Enable/Grant,
   Credentials) sind erlaubt — aber nur über dedizierte Endpunkte mit
   Validierung, Confirmation, Audit-Event und ohne Secret-Werte in der UI;
   Pausieren/Redirect/Spend-Entscheidungen laufen nie aus der UI.
