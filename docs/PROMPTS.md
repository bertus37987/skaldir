# PROMPTS — System-Prompts als Artefakte

Prompts sind bei Skaldir Agent **Daten, keine Code-Strings**: versionierte Markdown-Dateien mit
YAML-Frontmatter unter `prompts/`. Der `PromptRegistry`-Loader (`src/godsv/prompts.py`) liest
je ID die höchste Version.

## Inventar (v1)

| ID | Zweck | Tier | Input → Output |
|---|---|---|---|
| `main_agent_steering` | Kontext-Block für den Hauptagenten: Umgang mit `[voice]`-Hinweisen, Evidenz-/Unsicherheits-/Scope-Regeln | main | Pact → Kontextblock |
| `voice_triage` | Kleines Modell: Ist der Agent hängengeblieben? Kategorie + Suchqueries | small | Event-Digest + Pact → JSON |
| `voice_synthesis` | Genau EINEN belegten Hinweis formulieren (oder schweigen) | mid | Trigger + Evidenz-Items → JSON |
| `same_problem_judge` | Ist das aktuelle Problem dieselbe Grundursache wie ein gespeicherter Incident? | small | Problem + Kandidaten → JSON |
| `incident_extract` | Aus Episode einen dauerhaften Incident-Datensatz extrahieren (nur wenn verifiziert) | mid | Episoden-Summary → JSON |
| `skill_draft` | SKILL.md-Entwurf aus beobachteter Praxis (≥3×, ≥2 Sessions) | mid | Muster-Evidenz → Markdown |
| `tool_spec_draft` | Tool-Spec-Entwurf (compose-first) aus Capability-Need + bestehender Suite | mid | Need + Suite → JSON |
| `cu_step_plan` | Computer-Use-Schritt als geordnete Versuche (API>DOM>Vision) mit Verifikationspflicht | mid | Goal + Beobachtung → JSON |
| `spend_intent` | Verifizierbarer Spend-Intent (Betrag, Merchant, Zweck) für Owner-Approval | small | Goal + Whitelist + Budget → JSON |
| `completion_check` | Zweitmeinung zu unbelegten Erfolgs-/Fertig-Aussagen (additiv, fail-open) | small | Finaltext + Evidenz-Digest → JSON |
| `voice_redirect` | Verdichteter Redirect nach Pause-Recherche (Problem/Evidenz/Richtung/vermeiden/Unsicherheit) | small | Problem + Pakt + Findings → JSON |
| `slop_check` | Checkpoint-Review: Slop, Scope, fehlende Verifikation | mid | Diff + Pact → JSON |
| `research_note` | Fund in atomare Research-Note mit Provenienz kuratieren | small | Rohfund + Quellenmeta → JSON |

## Policy

1. **Dateinamen:** `<id>.v<N>.md`. Eine publizierte Version ist **unveränderlich** — Änderungen
   erzeugen `v<N+1>`. Der Loader nimmt automatisch die höchste Version; keine Registrierung nötig.
2. **Frontmatter-Pflichtfelder:** `id`, `version`, `purpose`, `model_tier`, `inputs`, `output`.
   Tests erzwingen Konsistenz (Dateiname-Version == Frontmatter-Version, `purpose` vorhanden).
3. **Sprache:** Prompts auf Englisch (Modell-Verhalten), Dokumentation auf Deutsch.
4. **Security:** Prompts enthalten **nie** Secrets; externe Inhalte erreichen Prompts nur über
   `spotlight()`-Wrapper (untrusted). Prompt-Dateien werden wie Code reviewt (PR, Änderungsgrund
   im Frontmatter-`purpose` oder Commit).
5. **Modell-Tiers:** `small` = Triage/Judge/Extraktion (billig, darf nie allein Entscheidungen
   über Geld/Skills treffen), `mid` = Synthese/Reviews, `main` = der Hauptagent-Kontext.
6. **Tests:** `tests/test_prompts.py` prüft alle REQUIRED_IDS, Parsing und Versionierung.

## Bezug im Code

```python
from godsv.prompts import PromptRegistry
prompt = PromptRegistry().get("voice_triage")   # -> Prompt(id, version, meta, body, path)
```

Triage/Synthese/Judge laufen live über `ctx.llm` (V1); `tool_spec_draft` speist den
Tool-Suite-Kern (V1.5).
