# TOOLS-V2 — Builder-Lücken §1.7 + §1.8 (Block B3)

**Auftrag:** Masterprompt §1.7 (Compose-before-create im ECHTEN Builder) + §1.8
(`draft_tool_spec` an den vorhandenen konfigurierten Modellclient + begrenztes
Build-Budget). Datei-Eigentum: `src/godsv/toolbuilder.py`, `src/godsv/tools.py`,
`tests/test_toolbuilder.py`, `tests/test_tools_store.py`.

Statusbegriffe wie im Project-Standard: **exists · wired · tested · live-verified**;
Restgrenzen am Ende.

## 1. Compose-before-create im echten Builder (§1.7)

**exists** — `ToolBuilder.compose_check` (`src/godsv/toolbuilder.py:249-289`) fragt
vor jedem Schreiben den Bestand ab; `tools.compose_candidates_scored`
(`src/godsv/tools.py:227-260`) liefert dafür `(Überlappung, ToolSpec)`-Paare,
`tools.compose_candidates` (`src/godsv/tools.py:262-279`) bleibt der
Spec-View darüber (Default `min_score=1` = altes Verhalten).

**wired** — im echten `build()`-Pfad, direkt nach `search_before_build` und **vor**
`generated_tools/<name>`/Manifest/Gates:

- `compose = self.compose_check(...)` + Record-Stage `compose_check`
  (`src/godsv/toolbuilder.py:360-361`), `build()` kennt beide Schwellen über
  `_COMPOSE_REFUSE_OVERLAP = 3` / `_COMPOSE_HINT_OVERLAP = 2`
  (`src/godsv/toolbuilder.py:86-87`).
- **refuse**: Ergebnis trägt `error`, `compose_check`, `compose_recommended` und
  `redirect = ToolBuilder.redirect_refusal(...)`
  (`src/godsv/toolbuilder.py:362-378`); `redirect_refusal`
  (`src/godsv/toolbuilder.py:714-729`) ist der winzige `tool_creation_refused`-Notiz
  für den Main-Agent (capability, reason, recommendation, candidates).
- **hint**: nur der Record (kein Ergebnis-Feld, keine Blockade)
  — der Builder läuft weiter.
- Das Build-Record-Feld `compose_check` ist im Queue-Aggregat sichtbar
  (kopierte Keys `src/godsv/toolbuilder.py:236-241`), je Build:
  `{"decision": "refuse"|"hint"|"clear", "threshold": 3, "hint_threshold": 2,
  "candidates": [{"tool", "overlap", "surface", "kind"}...]}`.

### Entscheidungslogik und Schwellen

| Überlappung | Entscheidung | Wirkung |
|---|---|---|
| ≥ 3 Terme mit einem `approved`/`scanned`-Tool | `refuse` | Neubau verweigert, nichts geschrieben, Empfehlung im Ergebnis + Redirect |
| genau ≥ 2 Terme | `hint` | nur Record-Hinweis, Bau läuft normal |
| < 2 Terme | `clear` | kein Hinweis |

Term-Zählung = Wörter ≥ 3 Zeichen (Regex `[A-Za-zÄÖÜäöüß_]{3,}`,
`src/godsv/tools.py:74-75`) über name+title+description+tags des Kandidaten,
**bereinigt um das Boilerplate-Vokabular aus B2**
(`_BOILERPLATE`, Import `src/godsv/toolbuilder.py:264`). Das ist bewusst dieselbe
Konservativität wie `search_before_build` (`_BUILD_REFUSE_OVERLAP = 3`,
`src/godsv/capability_mining.py:1334`): generische Wörter wie
„inspect/verify/state/screenshot" sind **kein** Duplikat-Beweis und zählen nicht
mit.

### Kein False-Block

Drei Schutzregeln sind getestet:

1. **Boilerplate zählt nicht** — „Inspect the tool 1: open it, screenshot it,
   verify the state" blockiert nicht gegen einen Bruder „tool_0" mit identischer
   Formulierung (`test_generic_wording_alone_never_blocks_a_batch_build`,
   `tests/test_toolbuilder.py:398`). Genau dieser Fall fiel zuerst als Regression
   in `tests/test_capability_learning.py::TestLiveInsertion::test_hot_set_is_capped_at_three_new_capabilities`
   auf — ohne die Boilerplate-Bereinigung hätte das Compose-Gate die
   Serien-Builds „tool_0…tool_4" verweigert (rot verifiziert, dann behoben;
   Datei gehört B2, nicht B3).
2. **Eigener Name wird übersprungen** — ein Rebuild von `X` ist ein Update, kein
   Duplikat (`compose_check`, `src/godsv/toolbuilder.py:275`); ein echtes
   Duplikat von `X` fängt zusätzlich bereits `search_before_build`.
3. **Echte neue Fähigkeit bleibt grün** — unverwandte Tools blocken nie
   (`test_genuinely_new_capability_is_not_blocked`,
   `tests/test_toolbuilder.py:376`).


## 2. `draft_tool_spec` am vorhandenen Modellclient + Budget (§1.8)

**exists** — `tools.draft_tool_spec_result` (`src/godsv/tools.py:339-398`) +
`ToolDraft` (`src/godsv/tools.py:316-332`): der Draft läuft über den
**injizierten** `LLMClient` und den **injizierten** `BudgetTracker` den
vorhandenen Prompt `tool_spec_draft` (REQUIRED, `src/godsv/prompts.py:27`,
`model_tier: mid`, `prompts/tool_spec_draft.v1.md:5`) — es entsteht **keine**
zweite Provider-Konfiguration und kein Fallback-Client. `draft_tool_spec`
(`src/godsv/tools.py:401-417`) bleibt als dünne Seam mit altem Vertrag
(`ToolSpec | None`, kein Raise) erhalten.

**wired** — `ToolBuilder.__init__` nimmt `llm=`, `budget=`, `prompts=`
(`src/godsv/toolbuilder.py:171-196`); `build()` ruft bei fehlender Quelle
`_draft_without_source` (`src/godsv/toolbuilder.py:391-395` →
`src/godsv/toolbuilder.py:634-712`). Der Draft bekommt die realen
Bestandsnamen als Kontext (`name — description`, `approved`, max. 20,
`src/godsv/toolbuilder.py:662-667`) — Compose-vor-Neubau ist damit auch
Prompt-seitig gefüttert.

### Blocker-Semantik (kein erfundenes Grün)

| Zustand | `result["blocked"]` | wann | Wirkung |
|---|---|---|---|
| keine Quelle **und** kein Client | `no_source_no_client` | `_draft_without_source` ohne `self.llm` (`src/godsv/toolbuilder.py:644-654`) | präziser Fehler („no source supplied … no model client is configured for a draft"), keine Datei, kein Eintrag |
| Budget erschöpft | `draft_budget` | `BudgetExceeded` aus `run_prompt` → `ToolDraft.kind="budget"` (`src/godsv/tools.py:361-363`) | vollständiger Fehlerzustand: kein Tool-Verzeichnis, kein Manifest, kein Register-Eintrag, kein Teilergebnis |
| Client-/Transportfehler | `draft_llm` | `ToolDraft.kind="llm"` | idem, Fehlertext trägt Ausnahme-Typ |
| Draft unbrauchbar | `draft_invalid` | Name/JSON ungültig → `ToolDraft.kind="invalid"` | idem |
| Draft ok | — (kein `blocked`) | gültige `ToolSpec` | Ergebnis trägt `draft` (+ `draft_problems` bei Kompositionsfehlern), Record-Stage `drafted`; **kein** Register-Eintrag: ein Draft ist ein Vorschlag, kein Build |

Wichtig: mit **vollständiger Quelle** (`source_code`/`source_path`) läuft der
Builder exakt wie vorher — **ohne jeden Modellzugang**; der Client ist nur für
den sonst tödlichen „no source"-Zweig nötig.

### Budget

- Default: `DRAFT_BUDGET_LIMITS = {"small": 4000, "mid": 4000}`
  (`src/godsv/toolbuilder.py:92`) — je Build **frisch** konstruiert
  (`src/godsv/toolbuilder.py:656-660`), damit ein Build nicht unbegrenzt
  Schätz-Tokens (chars/4, `src/godsv/llm.py:66-87`) verbrennen kann. Der
  `mid`-Wert deckt den deklarierten `model_tier` des Prompts ab, `small`
  dieselbe Schranke bei Tier-Override.
- Injiziert: `ToolBuilder(budget=BudgetTracker(limits=...))` gilt as-is
  (geteiltes Session-Budget); der zuletzt benutzte Tracker steht in
  `builder.last_draft_budget` (`src/godsv/toolbuilder.py:196`, gesetzt `:661`),
  und jedes Draft-Record trägt den Nutzungsstand (`budget=...`).

### Client-Quelle (nichts Zweites gebaut)

Der einzige heute konfigurierte Client bleibt `ContextLLM` im Plugin
(`integrations/hermes/godsv_plugin/__init__.py:183-249`, gebaut aus Host-Side-LLM
`ctx.llm` nur bei `GODSV_LLM=1`, Injektion `:1825`) — **gelesen, nicht
editiert** (Datei gehört B5). Der Kern ruft ihn nie selbst auf, sondern nimmt
ihn als `LLMClient`. Für Tests/Demos: `ScriptedLLM`/`CallableLLM`
(`src/godsv/llm.py:39-63`). `src/godsv/modelroles.py` bleibt Namensspeicher für
Rollen (provider/model ohne Secrets) und `tools/gv_eval_longhorizon.py` bleibt
deterministisch ohne Modell — beide unverändert.


## 3. Tests

Kommandos wie im Plan (`.venv/bin/python -m pytest <dateien> -p no:randomly`,
pyproject setzt `-q` — kein zweites `-q`).

### rot-first

1. **Konstruktions-Rot:** `.venv/bin/python -m pytest tests/test_tools_store.py
   tests/test_toolbuilder.py -p no:randomly`
   → `ImportError: cannot import name 'compose_candidates_scored' from 'godsv.tools'`,
   `1 error in 0.51s`, `PYTEST_RC=2` (vor der Implementierung).
2. **Anker-Rot (nicht via `git stash`):** `_COMPOSE_REFUSE_OVERLAP` temporär auf 99
   → `FAILED …test_three_term_overlap_refuses_the_build_before_writing`,
   `1 failed, 21 deselected` („assert True is False"), danach Datei restauriert
   (`grep` beweist `_COMPOSE_REFUSE_OVERLAP = 3`).

### grün (gemessene Zahlen)

| Lauf | Ergebnis |
|---|---|
| `tests/test_tools_store.py tests/test_toolbuilder.py` (Endstand) | **44 passed in 4.26s**, RC=0 |
| `… + tests/test_capability_learning.py tests/test_capability_flow.py tests/test_tool_scopes.py tests/test_mcp_scope.py` | **120 passed in 12.29s**, RC=0 |
| `tests/test_capability_learning.py tests/test_toolbuilder.py tests/test_tools_store.py` (nach Boilerplate-Fix) | **76 passed in 10.21s**, RC=0 |
| Volle Suite (alle Dateien), Log `.godsv/b3/fullsuite.log` | `3 failed, 1229 passed, 1 warning in 90.78s`, `SUITE_RC=1` — **alle** FAILs in `tests/test_trace_mining.py` (B2-WIP, Datei-mtime während des Laufs; 0 Treffer für `toolbuilder\|godsv\.tools` in der Datei), **keine** in B3-Dateien |
| `ruff check src/godsv/toolbuilder.py src/godsv/tools.py tests/test_toolbuilder.py tests/test_tools_store.py` | `All checks passed!` |

### neue Tests (je Datei)

`tests/test_toolbuilder.py` (23 gesammelte Tests), neu:

- `TestComposeBeforeCreateInBuilder` (`:307`):
  `test_three_term_overlap_refuses_the_build_before_writing` (`:308`, Verweigerung
  + Redirect + Record + **nichts geschrieben**: kein Verzeichnis, kein
  Register-Eintrag, `store.count()==1`),
  `test_two_term_overlap_only_hints_in_the_record` (`:350`, Bau läuft,
  `compose_check` nur im Record, `overlap == 2`),
  `test_genuinely_new_capability_is_not_blocked` (`:373`),
  `test_generic_wording_alone_never_blocks_a_batch_build` (`:398`).
- `TestDraftWiring` (`:432`):
  `test_missing_source_without_client_is_a_precise_blocker` (`:433`),
  `test_client_drafts_a_valid_spec_and_registers_nothing` (`:445`, `draft.kind ==
  "composition"`, `store.count()==0`, Stage `drafted`),
  `test_draft_prompt_carries_the_existing_suite` (`:468`, Prompt-ID
  `tool_spec_draft`, Tier `mid`, Bestand im Prompt),
  `test_budget_exhaustion_is_a_clear_error_without_a_half_build` (`:493`,
  `blocked == "draft_budget"`, nichts registriert, kein Verzeichnis),
  `test_builder_uses_a_bounded_default_draft_budget` (`:514`).

`tests/test_tools_store.py` (21 gesammelte Tests), neu:

- `TestCompose::test_scored_candidates_expose_the_overlap` (`:56`) und
  `TestCompose::test_default_min_score_keeps_single_term_matches` (`:78`)
  (`min_score`/Default-Semantik belegt).
- `TestDraftResult` (`:225`): Budget-Abbruch wird gemeldet statt geschluckt
  (`:228`, inkl. Legacy-Seam `draft_tool_spec` → `None`),
  Happy-Path mit Spec + Usage (`:249`), ungültiger Name (`:259`),
  Client-Fehler (`:267`).


## 4. Restgrenzen

Alle Punkte sind **bewusst offen** und werden nicht als erledigt behauptet:

1. **CLI-Surface fehlt (Welle 2)** — es gibt kein `godsvoice tools draft` und
   keine `--llm/--budget`-Flags: `cli.py` gehört B4 (Welle 1) bzw. der
   Welle-2-Verdrahtung. Heute ist der Draft nur als Python-API erreichbar
   (`ToolBuilder(runtime, llm=…, budget=…)`), genau wie im Auftrag als Restgrenze
   vorgesehen.
2. **Kein Produktionsaufrufer injiziert heute einen Client** — `capability.py:155`
   (B1), `cli.py:687`, `plugin_api.py:733/749/1050` konstruieren den Builder
   ohne `llm`; der präzise Blocker `no_source_no_client` ist damit der reale
   Zustand, kein Grün. Die Injektion des vorhandenen `ContextLLM`
   (`integrations/hermes/godsv_plugin/__init__.py:242-249`) ist ein
   Welle-2-/B5-Schritt (Plugin-Datei ist fremdes Eigentum) — dort muss der Client
   aus der Session an den Builder gereicht werden; **keine** zweite
   Provider-Konfiguration entsteht.
3. **Kein Auto-Build von Drafts** — mit Client entsteht ein Vorschlag
   (`result["draft"]`, Record-Stage `drafted`), **kein** Register-Eintrag und
   keine `generated_tools`-Datei: die Propose-first-Disziplin bleibt gewahrt
   (`tools propose`/Approval-Pfad).
4. **Lexikalisches Matching (wie im ganzen Haus)** — Term-Überlappung, keine
   Embeddings/Semantik. Der Compose-Check liest nur `tools.db`
   (`approved`/`scanned`); Manifest-/Skill-/MCP-Flächen deckt weiterhin
   `search_before_build` ab. Beide Schwellen sind bewusst konservativ
   (3 = Refusal, 2 = Hinweis), der Boilerplate-Filter ist von
   `capability_mining._BOILERPLATE` geborgt (privater Name; Änderungen dort
   wirken bewusst mit).
5. **Nicht live-verified** — der Draft lief nie gegen ein echtes Modell (nur
   `ScriptedLLM`/`CallableLLM`); ein Host-Roundtrip (Plugin → `ContextLLM` →
   Builder → Draft im Panel/Build-Record) ist **nicht** bewiesen und gehört zur
   Welle-2-Abnahme (Punkt 11/12 der Abnahme-Matrix).
6. **Budget ist eine Schranke, keine Buchhaltung** — `chars/4`-Schätzung
   (`src/godsv/llm.py:66-67`); der Standardwert 4000/Tier begrenzt genau **einen**
   Draft je Build. Ein geteiltes Session-Budget ist injizierbar, aber niemand
   setzt es heute.

## 5. Dateien und Commits

- Code: `src/godsv/toolbuilder.py`, `src/godsv/tools.py`.
- Tests: `tests/test_toolbuilder.py`, `tests/test_tools_store.py`.
- Doc: diese Datei.
- Commit: `97b37f7` `feat(gap7+8): Compose-before-create im Builder + Draft am
  vorhandenen Modellclient` (nur die vier Eigentums-Dateien).

