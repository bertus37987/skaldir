# Verifikationsbericht v1.3 — Skaldir Agent

Abschluss-Dokument der Core-Runde (Spec §1–§26): Implementierungsübersicht,
Architekturänderungen, UI-Flows, Tests, echte E2E-Beweise, Secret-Leak-Audit
und bekannte Grenzen. Stand: **v1.3.6**, HEAD `7f249dd`, CI grün.

## 1. Implementierungsübersicht

| Feature (Spec) | Ort | Beweis |
|---|---|---|
| Smart Voice: erweiterte Signale (drift, slop, unverified assumption, ignored evidence, constraint forgetting, wrong tool, wrong tool, fehlende Verifikation, Diff-Churn, Halluzinations-Klassen) | `src/godsv/monitor.py`, `voice.py`, `pause.py` | Monitor-Tests, Eval-Episoden, `test_pause_hard.py` |
| Kostenmodell vor Intervention („expected cost of continuing > cost of intervention") | `voice.assess_intervention`, MEASURED/ESTIMATED getrennt | Pausen-Tests |
| Bounded/frischer Voice-Context (§2) | `src/godsv/context.py` (Rollen-Profile, Quotas, `stats()`) | `test_context_budget.py` (8) |
| Watcher/Investigator-Trennung (§3) | Rollen-Profile + Investigator-Schema (problem/evidence/why_bad/direction/avoid/confidence) | `test_context_budget.py`, Redirect-Prompts |
| Model Selection (§4/§5) | `src/godsv/modelroles.py` (nur Namen in `$HERMES_HOME/godsv/models.yaml`), Hermes-Registry/`ctx.llm`-Override | `test_model_routing.py` (6), Models-Tab |
| Autonomer Tool Builder (§6–§9) | `src/godsv/toolbuilder.py` (Manifest, Gates in bwrap, Registrierung) | `test_toolbuilder.py` (13), Live-CLI |
| Capability-Gap-Flow: Pause → Build → Redirect → Resume (§17/§18) | `src/godsv/capability.py`, Plugin | `test_capability_flow.py` (9), Live-CLI |
| Treasury: tool-gebundene Grants, fail-closed | `src/godsv/treasury.py` | `test_panel_actions.py`, `test_treasury_hardening.py` (10) |
| Credential-Selftest (§16) | `Broker.selftest` (Entschlüsselung + isolierte Injection, kein Netz) | 3 Panel-Tests |
| Usage-Ledger (§15) | `src/godsv/toolusage.py`, MCP- + CLI-Wrapper | MCP-Roundtrip-Test, 2 Panel-Tests |
| MCP-Export (§9) | `src/godsv/mcpserver.py` (approved-only) | `test_mcp_server.py` (5), `test_mcp_stdio_e2e.py` (1) |
| Panel: 10 Tabs (§22) | `desktop/plugin.js`, `dashboard/plugin_api.py` | Node-Smoke (62 Checks), echte App (24 Checks) |

## 2. Architekturänderungen

- **Keine Parallelarchitektur:** Der Tool-Builder nutzt `ToolSpec`/`ToolStore` +
  die bestehenden Gates; Model-Rollen nutzen den capability-gated `ctx.llm`-Pfad;
  der Capability-Flow läuft in der **bestehenden** Pause-Pipeline.
- **Neue Module:** `modelroles.py`, `toolbuilder.py`, `capability.py`,
  `toolusage.py` (jeweils kompakt, mit Tests).
- **Neue Event-Kinds:** `voice_context_built`, `capability_requested`,
  `treasury_selftest`.
- **Bugfix:** `gates._run` löst `workdir` absolut auf (relative Pfade brachen die
  bwrap-Sandbox); betrifft Gates, Broker und Selftest.
- **Panel-Mutationen (§23):** nur über dedizierte Endpunkte mit Origin-Guard,
  Validierung, Confirmation (riskante Aktionen) und Audit-Events; nie
  Secret-Werte in Antworten.

## 3. Screens / UI-Flows

Navigation (vollständig): **Live · Models · Pausen · Memory · Skills · Tools ·
Treasury · Computer Use · Evals · System**.

- **Models:** 5 Rollen (Main/Watcher/Investigator/Judge/Fallback), Provider- und
  Modell-Dropdowns aus der echten Hermes-Registry (5 Provider / 113 Modelle),
  „Test model" (auth/response/tool-call/Latenz, nie Keys), persistente Rollen.
- **Tools:** Capability Requests (Karten mit Status, Handles, Build-Stufen,
  Ergebnis-Tool), Installed (inkl. Aufrufe), Build Queue, MCP-Server, Drilldown
  ins Tool-Detail (Manifest, Gates, Grants, **Nutzung & Fehler**, Aktionen).
- **Treasury:** Credentials anlegen/entfernen/Grant/Revoke, **„testen"**
  (sicherer Selftest), nie Secret-Werte; keine „Reveal"-Funktion.
- **Computer Use:** Runner-Isolation (live geprobt), Policy (API > DOM > Vision),
  deterministischer Dry-Run mit echtem Executor, Verlauf.
- **Evals:** Episoden-Katalog, „Eval ausführen" (reale Suite), §20-Token-Metriken
  MEASURED vs. ESTIMATED (klar getrennt).

## 4. Tests (neu in dieser Runde)

| Datei | Tests | Fokus |
|---|---|---|
| `tests/test_context_budget.py` | 8 | bounded Context, Rollen-Trennung, Freshness |
| `tests/test_model_routing.py` | 6 | Rollen → echte Call-Kwargs, Tier-Mapping, fail-open, Persistenz |
| `tests/test_capability_flow.py` | 9 | Signal → Pause → Build → Redirect; Fehlschlag ehrlich; Dispatch |
| `tests/test_toolbuilder.py` | 13 | Manifest, Gates, Wrapper-Ausführung, `secret://`-Handles |
| `tests/test_pause_hard.py` | 12 | Crash-Resume, Lease-Ablauf, Recovery, exactly-once |
| `tests/test_mcp_server.py` | +2 | Builder-Tool über MCP, Usage-Ledger-Eintrag |
| `tests/test_mcp_stdio_e2e.py` | 1 | echter stdio-Prozess: start → discover → filter → invoke → restart |
| `tests/test_panel_actions.py` | 20 | Models/Roles, Tools/Aktionen, Requests, Usage, **Selftest** (Redaktion, deny, 400) |

Gesamtstand: **421 Tests grün** (pytest), **62 Node-Smoke-Checks**, bandit 0,
ruff 0. CI: pytest + ruff + bandit auf Python 3.12.

## 5. Echte E2E-Beweise

- **Model-Routing:** `test_model_routing.py` zeigt ohne Provider-Call, dass zwei
  Rollen auf unterschiedliche Provider/Modelle geroutet werden, das
  Tier-Mapping greift, ein geschlossenes Gate fail-open zum Retry ohne Override
  führt und Rollen-Änderungen sofort wirken. UI: Models-Tab + „Test model".
- **Tool-Creation (CLI + MCP):** Live-CLI im echten Home: `tools request` →
  `tools fulfill` → alle Gates grün → Redirect `tool_created` → Wrapper-Aufruf
  liefert `{"converted": 270.0}`. MCP: Builder-Tool über `python -m godsv mcp
  serve` als separater Prozess entdeckt (approved sichtbar, drafts gefiltert),
  invokt (`{"doubled": 10}`) und nach Prozess-Neustart weiter registriert.
  Pause-Flow: Signal → echte `VoicePause` → Build im Research-Thread →
  `pause_delivered` mit Tool-Namen im Redirect.
- **Treasury/Secret-Isolation:** Klartext-Secret in keiner Panel-Antwort; das
  Ledger redigiert Fehlertexte; der Selftest beweist die Injection in den
  isolierten Subprozess, ohne das Secret zurückzugeben.
- **Pause/Resume-Crash (§21):** Voice-Kill → Watchdog-Abandon; Research-Hänger →
  Lease-Ablauf; kompletter Runtime-Neustart → Recovery; 8 parallele Resume-
  Events → **genau ein** Resume.
- **UI (echte gebaute Electron-App):** 24/24 Checks — 10 Tabs, Models-Rollen,
  Tool-Detail mit Usage-Ledger, Capability-Requests-Karte, CU-Dry-Run-Klick,
  Eval-Run-Klick, Credential-Selftest-Klick, Fehler-/Leerzustände,
  DOM-Secret-Scan 0.

## 6. Secret-Leak-Audit

- **DOM (echte App):** keine Treffer für `sk-…`, `tok-…`, `GODSV_VAULT_KEY=…`,
  `secret://…?token=…`.
- **Panel-Antworten/Payloads:** nur Handles (`secret://…`), nie Werte —
  durch Tests erzwungen (`Klartext-Secret in keiner Payload`).
- **Runtime-Datenbanken/Dateien:** kein Klartext in Event-Store, Pauses, Vault
  (verschlüsselt), Treasury-State; Usage-Ledger redigiert Fehlertexte über den
  Secret-Scanner (`secretscan.redact`).
- **Subprozesse:** Secrets nur im Env des isolierten Kindprozesses; Ausgaben
  werden vor Rückgabe gescrubbt.

## 7. Bekannte Grenzen (ehrlich)

1. **Echter Provider-API-Smoke** (Live-Call gegen einen Provider) läuft nicht in
   CI — by design (Kosten/Secrets); bewiesen ist der Mock-/Gate-Pfad + der
   Registry-Routing-Beweis.
2. **Computer-Use Vision-Capture:** Kanal-Reihenfolge und Policy sind umgesetzt
   (API > DOM > Vision, Dry-Run über den echten Executor); ein **Screenshot-
   Runner** für echte Bildschirmaufnahmen auf Wayland ist offen.
3. **Icon-Abnahme in GNOME** durch den Nutzer steht aus (technisch: WM-Class-Match
   gefixt).
4. **iPad-Ansicht** für Freunde ist außerhalb des Fedora-Releases (Roadmap).
5. **Tokenzahlen:** Main-Agent-Tokens hält Hermes; Skaldir Agent zeigt nur
   MEASURED-Zählungen plus klar gelabelte ESTIMATES (≈ Zeichen/4).

## 8. Stand

- Versionen: `v1.3.0` … `v1.3.6` (alle gepusht, CI jeweils grün).
- HEAD: `7f249dd` — Arbeitsbaum clean.
- Metriken: 421 Tests grün · 62 Panel-Smoke-Checks · 24 UI-E2E-Checks in der
  echten Electron-App · bandit 0 · ruff 0.
