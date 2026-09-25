# COMPUTER_USE_SUBTOOLS.md — visuelle Subtools dieses Repos (§55/§114)

Stand: 16.09.2026, HEAD `b7a287a` (Branch `godsv-supervisor-hardening`), Arbeitsbaum.
Pflichtartefakt nach ABNAHME §114 („`COMPUTER_USE_SUBTOOLS.md`/Manifest für reale
visuelle Subtools"), Feldsatz nach §55, Pflichtfälle nach §56, E2E-Ziel nach §15.

## Die ehrliche Antwort zuerst

**Es gibt in diesem Repo aktuell KEIN wiederverwendbares visuelles Subtool.** Kein
einziger existierender Code erfüllt den §55-Feldsatz; es gibt weder eine
`capability_id` noch eine `version` für einen GUI-Ablauf, weder `anchors` noch
`expected_transitions`, weder `reobserve_policy` noch `system2_escalation`.
Geprüft mit:

```bash
$ cd ~/projects/gods-voice
$ grep -rn "capability_id\|reobserve\|expected_transitions\|start_conditions\|completion_conditions\|failure_conditions\|allowed_actions\|supported_app_versions\|verification_contract\|system2_escalation" \
      --include=*.py --include=*.js --include=*.mjs --include=*.json src/ integrations/ tools/ audit/ tests/
src/godsv/discovery.py:1043:        "capability_id": row["name"],      # Tool-/Capability-Discovery, KEIN visuelles Subtool
$ grep -rn "subtool" src/ integrations/ tools/            # 0 Treffer
$ grep -rn "GUIState\|gui_state" src/godsv/*.py           # 0 Treffer
```

Was es gibt, sind **Runner** (isolierte Ausführungsumgebungen) und ein
**Attempt-/Verifikations-Loop** — beides Bausteine, aus denen ein Subtool gebaut
werden müsste, aber selbst kein Subtool. Deshalb nennt die Tabelle unten je
Artefakt, welche §55-Felder es berührt und welche fehlen.

## Artefakt-Inventar (real, mit Datei:Zeile)

| Artefakt | Ort | Was es ist |
|---|---|---|
| `Runner` (Protocol) | `src/godsv/computer_use.py:118` | Vertrag `isolation`, `is_isolated`, `act`, `observe`, `close` |
| `DryRunRunner` | `src/godsv/computer_use.py:148` | deterministischer Skript-Runner, keine Seiteneffekte (Tests/Demo) |
| `LocalSandboxRunner` | `src/godsv/computer_use.py:176` | bwrap-Sandbox (Netz aus, root read-only) |
| `ContainerRunner` | `src/godsv/computer_use.py:215` | rootless-podman, GUI-Pfad über `localhost/godsv-cu-x11` (Xvfb + xdotool + xmessage) |
| `CUPolicy` | `src/godsv/computer_use.py:99` | `allowed_kinds`, `max_actions`, `max_fallbacks`, `require_isolation`, `dry_run` |
| `Action` / `Verification` / `Attempt` | `src/godsv/computer_use.py:55/63/73` | Aktions- und Erwartungsvertrag |
| `order_attempts` | `src/godsv/computer_use.py:92` | stabile Kanalreihenfolge API > DOM > Vision |
| `CUExecutor.run` | `src/godsv/computer_use.py:354` | Führt Versuche aus, prüft nach jeder Aktion (`_verify_attempt` L450), schreibt `cu_*`-Events (Audit-Trail) |
| `plan_cu_attempts` | `src/godsv/computer_use.py:480` | **LLM**-Entwurf der Versuche eines Schritts (max 4), Endpunkt = ein Step, keine Versionierung |
| `runner_availability` | `src/godsv/computer_use.py:461` | Host-Probe, welche Isolationsstufen nutzbar sind |
| `system_one_action_gate` | `src/godsv/computer_use.py:555` | seit 16.09. (Parallel-Worker, uncommitted): **advisory** System-One-Vorlesung; gibt die `allowed_kinds` UNVERÄNDERT zurück, `None` bei Flags aus |
| `cu_payload` / `POST /cu/demo` | `integrations/hermes/godsv_plugin/dashboard/plugin_api.py:840` / `:1878` | Panel-Sicht: read-only Payload + Dry-Run-Demo („no real clicks") |

## §55-Feldmatrix — je Artefakt, ehrlich

Legende: ✅ vorhanden · 🟡 teilweise/anders benannt · ⬜ fehlt.

| §55-Feld | `DryRunRunner` | `LocalSandboxRunner` | `ContainerRunner` | `plan_cu_attempts` | **Subtool-Vertrag** |
|---|---|---|---|---|---|
| `capability_id` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `version` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `supported_app_versions` | ⬜ | ⬜ | ⬜ (Image `localhost/godsv-cu-x11`, kein App-/Versionsbezug) | ⬜ | ⬜ |
| `locale`-Annahmen | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `start_conditions` | ⬜ | 🟡 implizit „bwrap verfügbar" (`gates._sandbox_available`) | 🟡 `available()`/`start()` L244/L252 (Image/daemon), keine Zustands-Vorbedingungen der Ziel-App | ⬜ | ⬜ |
| `anchors` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `allowed_actions` | 🟡 `CUPolicy.allowed_kinds` (global, nicht subtool-spezifisch) | 🟡 dito | 🟡 dito | 🟡 die LLM-Vorschläge sind nicht vorab erlaubt, `_check_policy` filtert global | ⬜ |
| `expected_transitions` | ⬜ | ⬜ | ⬜ | 🟡 `Verification.expect_stdout_contains`/`expect_observe_contains` pro Versuch — keine Übergangsbeschreibung | ⬜ |
| `completion_conditions` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `failure_conditions` | ⬜ | ⬜ | ✅ strukturierte Fehlerpfade (`available`/`start`/`_default_argv` L288, `act` L306) | ⬜ | ⬜ |
| `reobserve_policy` | ⬜ | ⬜ | ⬜ (`observe()` L322 existiert, hat aber keine Policy) | ⬜ | ⬜ |
| `system2_escalation` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| `verification_contract` | ⬜ | ⬜ | ⬜ | 🟡 „jeder Versuch braucht eine Erwartung" (`_verify_attempt` L450, `Verification.is_empty` L68) | ⬜ |
| §55 optional `system_one_prechecks` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ (`system_one_action_gate` L555 ist advisory und erlaubt keine Fragenauswahl je Schritt) |
| §55 optional `system_one_postchecks` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| §55 optional `system_one_recovery_questions` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

**Kein Feld ist für irgendein Artefakt vollständig.** Damit ist §15 (E2E-07
„Visuelles Subtool") und §95 (JEV-17) nicht erfüllt.

## Was real fehlt (kleinstes fehlendes Stück, §114-Form)

Das kleinste fehlende Stück ist **nicht** „mehr GUI-Code", sondern **der Vertrag
selbst plus ein registrierbarer, versionierter Träger**:

1. Ein `VisualSubtool`-Datentyp mit exakt den §55-Feldern (frozen dataclass) und
   eine Registry `capability_id → (version, subtool)` — analog zum question
   registry-Muster aus §39. Ohne diesen Träger kann kein Ablauf „versioniert
   ausgedrückt" werden, und §56s Pflichtfall „Subtoolversion widerrufen" hat kein
   Ziel.
2. **Ein** konkreter Ablauf, der real beobachtet wurde (ein einziger erfolgreicher
   GUI-Ablauf im Xvfb-Container), mit echten `anchors`, `expected_transitions`
   und einem Endartefakt-Check — §15 verlangt genau das.
3. Die Verzweigung im Ausführungspfad: Anchor ungültig / Layout abweichend →
   `reobserve` oder `escalate_system2`, **niemals** Blindklick (§56: „Blindes
   Weiterklicken nach verlorenem Anchor ist FAIL").

Erst wenn (1)–(3) existieren und gegen §56s 14 Pflichtfälle gefahren sind, darf
diese Datei ein Subtool als existent ausweisen.

## Was zusätzlich fehlt und für JEV gebraucht wird

- **Kein GUIState-Vertrag**: `grep -rn "GUIState\|gui_state" src/godsv/*.py` → 0
  Treffer. §52/§93 (JEV-15) verlangt einen strukturierten, versionierten State;
  ohne ihn haben die §54-Fragen (Precondition/Postcondition) keinen State zum
  Auswerten.
- **Kein Bildwahrnehmungs-Pfad** in diesem Repo: die Kanalreihenfolge ist
  API > DOM > Vision (`computer_use.py:44`), und der einzige reale GUI-Runner ist
  ein xdotool-Container ohne Vision-Modell. §3 des Vertrags hält fest, dass Jev
  selbst **keine** Bildwahrnehmung ersetzt.
- **Der Container-GUI-Pfad ist auf diesem Host nicht fahrbar**: `godsvoice cu check`
  meldet `container GUI (godsv-cu-x11)` als „Image fehlt" (UX-Wave, reproduziert im
  REVIEW; hier nicht erneut gefahren, siehe „Nicht gefahren" unten).

## Nicht gefahren (ehrlich, mit nächster Aktion)

| Was | Warum nicht | Kleinste nächste Aktion |
|---|---|---|
| `godsvoice cu check` in dieser Session | Zeitbudget dieses Workers; der Befund ist aus dem UX-Review übernommen, nicht neu erhoben | `godsvoice cu check` laufen lassen und `runner_availability()`-Ausgabe an diese Datei hängen |
| Ein echter GUI-Ablauf im Container | Image `localhost/godsv-cu-x11` fehlt; ohne es kein echter GUI-Schritt | Image bauen (`docker/`-Rezept prüfen) oder Panel-Gate „runner not connected" ehrlich anzeigen (P2-Finding UX16-03) |
| §56-Pflichtfälle | ohne Subtool kein Prüfobjekt | nach (1)–(3) oben die 14 Fälle als Pin-Tests anlegen |
