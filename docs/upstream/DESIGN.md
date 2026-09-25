# SKALDIR AGENT — Design-Dokument
*(ehem. Arbeitstitel „Metis")*

**Ein selbst-lernender Agent-Harness mit Live-Lenkung: In-Task-Intervention, Problemmemory, automatisch geminte Skills & Tools.**

Version 0.1 (Basisebene) · 11.09.2026 · **Feature-Master: `CORE-PLAN.md` (konsolidierte Kern-Features)** · Ausbaustufe-2-Details: `DESIGN-2-GODS-VOICE.md`
Grundlage: eigene Recherche vom 11.09.2026 (alle Quellen verifiziert, siehe `SOURCES.md`).

---

## 0. Executive Summary

**Kern-Idee in einem Satz:** Ein Harness, dessen Hauptagent nicht nur Tools und Kontext besitzt, sondern einen **zweiten Agenten („Mentor")**, der die Trajektorie live mitliest, beim Feststecken **quellenbelegte Lösungshinweise mitten in die laufende Arbeit einspeist** und aus jeder Sitzung strukturiert lernt (Incidents, Research-Notes, Skills, Tools).

**Warum das eine echte Lücke ist:** Die vier Referenz-Harnesse (Hermes Agent, OpenClaw 2.0, DeepSeek Harness, Claude Code) sind stark bei Tool-Ökosystem, Sessions und Transparenz. Ihre Lern- und Memory-Mechanismen greifen aber fast ausschließlich **vor** der Session (Kontextinjektion: CLAUDE.md, MEMORY.md, Skills, Regeln) oder **nach** der Session (OpenClaws Skill-Workshop & „Dreaming", Hermes Curator). **Mitten in der Arbeit passiert nichts** — obwohl genau dort die teuren Fehlschleifen entstehen (wiederholte Fehler, Endlos-Retries, Test-Churn). Letta's Sleep-Time-Agents sind der nächste Verwandte, arbeiten aber im Idle-Modus am Memory, nicht am aktuellen Problem.

**Wett-Thesen (das Projekt steht und fällt mit ihnen):**
1. **Live-Intervention mit Evidenz schlägt Pre-Turn-Injektion**, wenn das Problem mitten in einer langen Trajektorie auftritt — mitgegebenes Anfangswissen ist dann verdünnt oder passt nicht zur konkreten Fehlersituation.
2. **Die messbare Superkraft ist die Lernkurve über Sessions** („jede gelöste Fehlerklasse nur einmal"), nicht ein einzelner One-Shot-Benchmark-Score. Diese Metrik etablieren wir als neue Messgröße (vgl. LongMemEval-V2, „recurring failure modes").
3. **Interop schlägt Insellösung:** Skill-Format (agentskills.io/SKILL.md) und Tool-Protokoll (MCP) werden unterstützt — unser Gehirn soll auch in fremden Harnessen laufen.

**Nicht-Ziele v0:** kein Cloud-Dienst, keine eigene Modell-Familie, kein Fork bestehender Harnesse, keine GUI vor Meilenstein M5. Erst ein schlanker, harter Kern — die Intelligenz steckt in Memory und Mentor, nicht in 40 Tools (Lehre von mini-SWE-agent: einfacher Loop + starkes Modell schlägt Tool-Zoo).

---

## 1. Was wir von den Referenz-Harnessen übernehmen (und was wir schlagen)

Alle Angaben aus den offiziellen Doku-Seiten vom 11.09.2026 (Links in `SOURCES.md`).

| Harness | Architektur-Kern | Memory | Skills/Tools | Übernehmen wir | Schlagen wir |
|---|---|---|---|---|---|
| **Hermes Agent** | CLI/TUI/Desktop/Gateway aus einem Kern; Provider-agnostisch; Plugins; Delegation; Cron | MEMORY.md (2.200 Zeichen) + USER.md (1.375), als **frozen snapshot** in jeden Session-Start injiziert; `session_search` (SQLite+FTS5) über alle Sessions; austauschbare Memory-Provider (Mem0, Honcho, …) | Progressive Disclosure (Index → Volltext → Referenzdateien), `skill_manage`, `/learn`, Curator (Nutzung, Staleness, Archiv) | Bounded-Memory-Disziplin; Skill-Format (agentskills.io-kompatibel); Progressive Disclosure; Curator-Idee für Skill-Rotation | Hermes injiziert Memory nur **vor** der Session (Snapshot-Konstanz ist gewollt, um Prompt-Cache zu halten) → wir ergänzen den **Mid-Task-Kanal**; incident-basiertes Problemmemory fehlt |
| **OpenClaw 2.0** (= v2026.8.1, 30.08.2026; MIT, Foundation; TS/Swift) | Ein langlebiger **Gateway-Daemon** + typed WebSocket-Protokoll + Clients/Nodes; SQLite-Transkripte; 16.977 PRs in 2.0 | Built-in Memory Core: Cross-Conversation-Recall (default an für Personal), **provenienz-gated**, „**Dreaming**" (Hintergrund-Konsolidierung mit Dream-Diaries), `memory forget` mit Provenienz-Purge, Memory Wiki | **Skill Workshop**: Proposals (PROPOSAL.md, Scanner inkl. Injection-Check, Grader, Rollback), auto/propose/off, **post-run** Self-Learning, Weekly Collection Review, Same-Turn-Repair genutzter Skills; ClawHub-Marktplatz | Daemon+Protokoll-first (billiger Desktop-Wechsel!); Provenienz-Purge; Governed-Proposal-Muster (unser Skill-Mining braucht dieselbe Bühne: Scanner, Diff, Approve, Quarantäne) | OpenClaws Lernen ist **post-run/idle** (Skill-Vorschläge aus vergangener Arbeit, Dreaming-Sweeps) → unser Mentor arbeitet **in-task**; Skills werden beobachtungs- und incident-getrieben gemintet; **Tool-Mining fehlt dort komplett** |
| **DeepSeek Harness** (Dev Preview 08/2026, MIT) | „**Agent = Model + Harness**"; Plugin-Kernel (Cordis): models, tools, skills, sessions, sandboxes, storage, loops, scheduling, UI — alles steckbar; 4 Laufmodi (Standard/Code/Minimal/Creator) | Alles läuft auf einem **append-only Session-Log** (auch jede Kontext-Injektion); Trajectory-View; resume/fork/search/replay auf demselben Event-Stream | Alles Plugin; Community-Ökosystem (`dsh-plugin`-Topic); Minimal-Modus = 2 Tools für Benchmarking | **Event-Sourcing als Fundament** (Replay, Fork, Audits, Post-Mortems); Plugin-Kernel-Prinzip; Tracebarkeit als Feature verkaufen | DSH macht den Stream **sichtbar** — wir **minen** ihn aktiv (Mentor + Lern-Pipeline). Kein Lern-Loop, kein Problemmemory |
| **Claude Code** (Anthropic) | Harness um Claude; Hooks, Subagents, Plugins; Managed Policy | CLAUDE.md-Hierarchie (Managed→User→Projekt→Lokal, Imports, `.claude/rules/` path-scoped), **Auto-Memory** (erste 200 Zeilen/25 KB, pro Repo, von Claude selbst geschrieben; Subagents mit eigenem Memory) | Skills, Subagents, Hooks (PreToolUse etc.), Plugins; `/learn`-Verwandschaft über Ökosystem | **Path-scoped Rules** (Kontext nur laden, wenn relevant) als direktes Vorbild für unser Retrieval; Auto-Memory-Konzept (Korrekturen → Regeln) | Auto-Memory ist **unstrukturiert und bounded** (Abschneiden bei 200 Zeilen!), kein semantisches Retrieval, kein Problemmatching, kein Hintergrund-Lernen |
| **Bonus: mini-SWE-agent** (Princeton/Stanford, >74 % SWE-bench verified) | Radikal minimal: ~100 Zeilen, **nur bash**, lineare History, zustandslose Subprozesse | — | — | Beweis: **Scaffold-Komplexität ist kein Qualitätsfaktor** → unser Differenzierer ist Lernen/Memory, nicht Tool-Zoo | Es lernt nichts (baseline-fähig, gut als Kontrollgruppe) |
| **Bonus: OpenHands** (arXiv 2407.16741) | Event-Stream-Architektur (jede Aktion/Observation ist ein Event) | — | — | Bestätigt Event-Bus-Modell für unsere Multi-Prozess-Architektur | — |

**Fazit der Landschaftsanalyse:** Das Rennen um bessere Prompts und mehr Tools ist gelaufen. Die nächste Stufe ist **Lernen über Zeit**. Dort hat OpenClaw die Nase vorn — aber nur für Skills und nur post-run. Niemand (in dieser Recherche) verbindet: Live-Überwachung + evidengestützte Intervention + Problemmemory mit Fehler-Signaturen + Tool-Synthese + Harness-übergreifenden Export.

---

## 2. Forschungsstand (was es schon gibt, was fehlt)

**Memory-Architekturen (Stand 2026):**
- *MemGPT/Letta* (arXiv 2310.08560): virtuelle Kontextverwaltung, Memory-Blocks. *Sleep-Time-Compute* (2504.13171): ein zweiter Agent bearbeitet im Idle das Memory asynchron — wichtigstes Vorbild für unseren „Night Shift", aber **auf Memory-Pflege beschränkt, nicht auf Problemlösungs-Wissen**.
- *Mem0* (2504.19413), *Zep* (2501.13956, temporaler Wissensgraph), *A-MEM* (2502.12110, Zettelkasten-artige atomare Notizen), *D-MEM* (2603.14597, Dopamin-Gating: nur „wichtige" Erfahrungen werden persistiert — Vorbild für unser Importance-Gating).
- Surveys: „From Storage to Experience" (2605.06716); „Agent Memory in the Second Half" (2602.06052); „Is Agent Memory a Database?" (2605.26252, Kritik an Memory-als-Storage ohne Provenienz).

**Lernen aus eigenen Trajektorien:**
- *Reflexion* (2303.11366), *Voyager* (2305.16291, Skill-Library wächst autonom — Urvater des Skill-Minings), *Agent Workflow Memory* (2409.07429).
- *ReasoningBank* (2509.25140): distills Reasoning-Strategien aus Trajektorien in eine verifizierte Memory — derzeit der engste akademische Verwandte unseres Incident-Stores.
- SWE-spezifisch: *ExpeRepair* (2506.10484, Dual-Memory für Repo-Repair), *SWE-Exp* (2507.23361, „memoryless explorer" als Problem), *EET* (2601.05777, erfahrungsbasiertes Früh-Abbrechen — Kostenhebel), *MemGovern* (2601.06789, lernt aus gelösten GitHub-Issues), *SWE-MeM* (2606.28434), *ReVeal* (2506.11442, Selbst-Verifikation vor dem Lernen).

**Verifikation (Fundament für Hint-Qualität):**
- *CRITIC* (2305.11738, tool-gestütztes Kritisieren), *Self-Refine* (2303.17651) — und die entscheidende Warnung: **„LLMs Cannot Self-Correct Reasoning Yet"** (2310.01798). Interne Selbstkorrektur ohne externe Evidenz funktioniert schlecht. → Der Mentor darf **nicht** „nochmal nachdenken" einfordern, sondern muss **externe Belege** (Incident aus früherer Session, Doku, Research-Note) liefern. Das ist das Design-Kriterium.

**Prozess-Supervision / Live-Intervention:** existiert als Forschungsidee (Critic-Modelle, Critical-Step-Perception 2608.01867), aber **in keinem der vier Harnesse produktiv**. Das ist die offene Flanke.

**Sicherheit:** Indirekte Prompt-Injection über Tool-Results/Webinhalte ist der Hauptangriffsvektor genau unseres Features (wir injizieren Fremdinhalt mitten in den Loop). Gegenmaßnahmen aus der Literatur: Spotlighting (2403.14720), Tool-Result-Parsing (2601.04795), System-Level-Defenses (2603.30016). OpenClaw zeigt den Industriestandard: Skill-Proposals werden von **Scannern** auf Injection geprüft, kritische Befunde blocken das Apply.

**Benchmarks für den Beweis:** SWE-bench Verified; Terminal-Bench 2.0 + Long-Horizon-Terminal-Bench (2607.08964); ProgramBench; „Do Agent Optimizers Compound?" (2607.14004) zeigt, wie man **Continual Learning** sauber evaluiert; LongMemEval (2410.10813) + **LongMemEval-V2** (2605.12493): Agent-Memory-Benchmark, der u. a. **recall von „recurring failure modes"** misst — die Benchmark-Richtung validiert unsere Kernmetrik. Hack-Verifiable Terminal Bench (2608.22103) als Warnung vor Reward-Hacking.

---

## 3. Feature-Spezifikation

### 3.1 Der Mentor (Kern-Innovation)

**Rolle:** Separater Prozess, liest den Event-Stream, erkennt Problemsituationen, recherchiert in den eigenen Wissensspeichern, liefert **einen** knappen, belegten Hinweis — oder schweigt. Der Mentor editiert nie Code und erteilt keine Befehle; er ist Ratgeber mit Quellenangabe, der Hauptagent bleibt souverän.

#### 3.1.1 Trigger-Policy (zweistufig, Precision-First)

```
Jeder Step schiebt ein Event in den Bus. Mentor wertet STUFE 1 lokal/deterministisch aus:
  error_burst     gleicher Fehler-Fingerprint ≥ 2× in ≤ 6 Steps
  loop            identischer Tool-Call (Name+Args-Hash) ≥ 3×
                  ODER Kosinus-Ähnlichkeit der letzten 4 Step-Summaries vs. der 4 davor > 0.92
  edit_churn      ≥ 4 Edits ohne Netto-Diff-Fortschritt an denselben Dateien
  test_churn      gleicher Test wiederholt rot, ohne dass sich relevante Dateien änderten
  budget_burn     > 40 % Task-Budget verbraucht und kein Fortschrittssignal (kein grüner Test,
                  kein abgeschlossener Subtask) in den letzten N Steps
  uncertainty     gehäufte Unsicherheits-Marker im Reasoning ohne Tool-Fortschritt
  user_signal     Nutzer korrigiert wiederholt („nein, so nicht")

Löst ein Signal aus → STUFE 2 (nur dann, Budget: max. K pro Task):
  Triage mit kleinem/Modell: Problemklasse? Confidence? Suchqueries erzeugen?
  → Retrieval-Fan-out → Synthese (stärkeres Modell) → Hint-Budget-Check → Zustellung oder Schweigen.
```

**Warum zwei Stufen:** Stufe 1 kostet nichts und läuft immer; Stufe 2 (LLM) läuft selten. Ziel-Budget: **≤ 15 % Token-Overhead** bezogen auf den Hauptlauf (Zieloptimierung < 10 %), **max. 5 Hints pro Task** (Default), **max. 1 Hint pro Problem-Signatur und Session** (nie zweimal dasselbe), Mindestabstand zwischen Hints.

**Precision schlägt Recall:** Ein falscher Hinweis kostet Kontext, Geld und Vertrauen (vgl. 2310.01798). Anfangs lieber schweigen. Die Precision wird gemessen (siehe 3.1.4) und die Schwellen werden datengetrieben nachgezogen.

#### 3.1.2 Retrieval-Fan-out

Bei jedem Triage-Fire sucht der Mentor **parallel** in fünf Quellen, alle Ergebnisse werden normalisiert und gerankt:

1. **Incident-Store** (L1): exakter Fingerprint-Match → sofort; sonst ANN über Problem-Embeddings (Top-k), danach „gleiches Problem?"-Judge (kleines Modell, Ja/Nein + Begründung).
2. **Projektwissen** (L2): Konventionen, Architektur-Fakten, bekannte Fallstricke des Repos.
3. **Docs-Index** (L4): Versions-genau indexierte Bibliotheks-/Framework-Doku (z. B. exakt die installierte PyTorch-Version) + Projekt-Doku.
4. **Research-Notes** (L4): kuratierte externe Erkenntnisse mit Provenienz.
5. **Skill-/Tool-Registry** (L3): gibt es eine Prozedur/ein Tool, das genau das löst? (Skill-Index ist billig — Progressive Disclosure).
6. *Optional letzte Instanz:* Live-Web-Suche, **nur** wenn lokal nichts gefunden wurde und das Budget es erlaubt (Ergebnis wird als unverified Note gespeichert).

#### 3.1.3 Hint-Protokoll (Zustellung)

Hinweise werden als **append-only Nachricht** in die Trajektorie gelegt (nie System-Prompt umschreiben, nie History verändern → **Prompt-Cache bleibt intakt**; Lehre aus Hermes' Cache-Invariante). Zugestellt wird an sicheren Punkten (nach einem Tool-Result, vor dem nächsten LLM-Call; nie mitten in parallelen Tool-Batches). Der Hinweis ist explizit **ignorierbar** und zur Prüfung markiert:

```
[mentor id=4711 sig=ef3a91 typ=repeat_error conf=0.74 hint=1/5]
Diagnose: torch.cuda.OutOfMemoryError — gleiches Muster wie Session 2026-08-14
(Incident #1234, dort gelöst).
Vorschlag: (1) gradient checkpointing aktivieren (torch.utils.checkpoint) — sparte damals ~22 % VRAM;
(2) batch_size halbieren + torch.cuda.empty_cache() nach jedem Step.
Belege: [1] Incident #1234 (Diff committet, Nachtest grün) · [2] PyTorch-Doku 2.9 „CUDA memory
(offline indexiert 30.08.2026) · [3] Research-Note #89.
Dies ist ein Hinweis, keine Anweisung — bitte selbst verifizieren. Ignorieren ist in Ordnung.
```

Formal im DB-Log: `hints(id, session_id, trigger_json, queries_json, candidates_json, hint_md, confidence, delivered_at, reaction, outcome)`.

**Aggressivitäts-Modi:** `off` / `advisory` (Default) / `assertive` (darf bei Budget-Burn oder Endlos-Loop einen kurzen Stopp + Lagebild einfordern) / **`shadow`** (füralle Evaluationen: Hints werden geloggt, aber **nicht** zugestellt → ehrliche Messung des Potenzials ohne Beeinflussung).

#### 3.1.4 Outcome-Tracking & Audit („Warum bekam der Agent diesen Hinweis?")

Jeder Hint wird nachverfolgt: Hat der Hauptagent den Pfad geändert? Kam danach ein Fortschrittssignal (Test grün, Fehler weg)? Auto-Klassifikation: `helpful / ignored / harmful / inconclusive` + optionales Nutzer-Thumb. Kennzahlen: **Hint-Precision** (Ziel ≥ 70 % helpful ab M4), Acceptance-Rate, Harm-Rate (Ziel < 5 %). Das `mentor_log` ist gleichzeitig unser Evaluations-Datensatz und das Tuning-Signal für die Trigger-Schwellen — der Harness lernt also auch **über sich selbst**.

### 3.2 Memory-Architektur (5 Schichten)

| Schicht | Inhalt | Wer schreibt | Besonderheit |
|---|---|---|---|
| **L0 Episode** | Laufende Trajektorie: jeder Step als Event | automatisch (append-only) | Replay/Fork/Suche wie im DSH-Session-Log |
| **L1 Episodic/Incidents** | „Fehler X gab es schon, Lösung war Y": Problem-Signatur + Kontext + Lösung + Evidenz | Konsolidierer (Night Shift), verifiziert | **Kernstück**; Fehler-Signaturen machen Probleme matchbar |
| **L2 Projekt-Semantik** | Fakten, Konventionen, Architektur-Entscheidungen, Umgebungsdetails | Konsolidierer; Korrekturen aus Sessions | retrieval-fähig, nicht alles in den Prompt |
| **L3 Prozedural** | Skills (SKILL.md) + Tools (MCP) | Learning-Pipeline mit Gates | Interop-Format |
| **L4 Research** | Externe Erkenntnisse: Claim + Quelle + Datum + Versions-Scope + Trust | Research-Agent + kuratierte Funde | Provenienz-first, Konflikt-Erkennung, Expiry |

**Problem-Signatur (das Matchmaking):**
```json
{
  "kind": "error|failtest|loop|uncertainty|budget",
  "fingerprint": "sha1(normalisiert: Fehlerklasse + Top-3 Stackframes (Projektpfade gemappt) + Exitcode + Tool)",
  "embedding": [ /* Vektor der Problem-Kurzfassung */ ],
  "context": {"repo": "…", "toolchain": "python 3.12, torch 2.9+cu126", "files": ["…"]}
}
```
Match-Regel: exakter Fingerprint → sofortiger Treffer. Sonst ANN + Judge. Nie automatisch anwenden — der Hauptagent entscheidet.

**Incident-Record:**
```sql
CREATE TABLE incidents(
  id INTEGER PRIMARY KEY, sig_kind TEXT, sig_fp TEXT, sig_vec BLOB,
  repo TEXT, env_json TEXT, title TEXT, root_cause_md TEXT, resolution_md TEXT,
  evidence_json TEXT,                    -- Diffs, Testläufe, Links, Session-Refs
  status TEXT,                           -- open | solved | regressed
  occurrences INTEGER, first_seen TEXT, last_seen TEXT,
  confidence REAL,                       -- wie oft hat die Lösung nachweislich funktioniert?
  source TEXT                            -- session | user | imported
);
```

**Research-Note:**
```sql
CREATE TABLE notes(
  id INTEGER PRIMARY KEY, topic TEXT, claim_md TEXT,
  source_type TEXT,        -- docs | paper | repo | blog | forum
  source_url TEXT, source_meta TEXT,    -- Version, Autor, Publikationsdatum
  retrieved_at TEXT, valid_until TEXT,
  trust REAL,              -- 0..1
  repo_scope TEXT,         -- gilt für welches Projekt / global?
  status TEXT,             -- active | stale | contradicted | unverified
  contradicts_json TEXT    -- IDs widersprechender Notes → Konflikt wird ANGEZEIGT, nicht versteckt
);
```

**Schreib-Gates (Anti-Müll):**
- Der Mentor **liest immer, schreibt nie direkt** ins Langzeit-Memory. Er markiert Kandidaten.
- Konsolidierung schreibt L1/L2/L4 nur mit (a) Provenienz und (b) Verifikation (Lösung mind. 1× erfolgreich angewandt, Test grün, oder Nutzer bestätigt). Externe Fremdinhalte bleiben `unverified`, bis sie validiert wurden.
- **Importance-Gating** (D-MEM-Idee): Nur Muster, die in ≥ 2 Sessions nützlich waren, werden dauerhaft; Einmal-Findings verfallen (Decay).
- Dedup über Signatur-Fingerprint + Embedding-Ähnlichkeit beim Schreiben.

**„Night Shift" (Konsolidierung, offline/idle — unser Pendant zu Letta-Sleep-Time und OpenClaw-Dreaming, aber weiter):**
Aufgaben: Episoden zusammenfassen → Incidents extrahieren & verifizieren → Duplikate mergen → Research-Notes revalidieren (Expiry, Refetch, Konflikte neu bewerten) → Skill-/Tool-Kandidaten zählen → Backups/Export. Alles als auditierbare Jobs mit Diff-Report („Diese Nacht gelernt: …").

### 3.3 Skill-Mining (dein Wunsch: „Neben-Agent merkt: dieser Test ist oft nötig → Skill")

**Lebenszyklus:**
1. **Beobachten:** Muster-Zähler über Sessions (z. B. dieselbe Test-Fixture-Sequenz, dasselbe Debug-Vorgehen, dieselbe Release-Prozedur). Schwelle: ≥ 3 Vorkommen in ≥ 2 Sessions; Dedup gegen existierende Skills.
2. **Kandidat → Draft:** Konsolidierer autorisiert einen SKILL.md-Entwurf **aus der echten Trajektorie** — inklusive Pitfalls (die Fehler, die beim ersten Mal auftraten!) und Verification-Abschnitt. Format: agentskills.io-kompatibel (läuft damit auch in Claude Code/OpenClaw/Hermes).
3. **Shadow-Validierung:** Der Draft wandert in den Skill-Index als `status=shadow` — der Mentor darf ihn vorschlagen, Erfolg/Nutzung werden getrackt. Erst wenn er nachweislich hilft (≥ N erfolgreiche Anwendungen), wird er `active`.
4. **Promotion:** Default **propose** (Nutzer bestätigt); optional `auto` nach Vertrauensphase. Jede Promotion mit Diff, Quellen („abgeleitet aus Sessions A, B, C") und Rollback.
5. **Monitoring & Drift:** Nutzung, Erfolgsrate, letzte Anwendung. Drift erkannt → Reparatur-Vorschlag; lange ungenutzt/wertlos → `deprecated` + Archiv (Hermes-Curator-Prinzip).

**Ehrliche Abgrenzung:** OpenClaw 2.0 hat mit dem **Skill Workshop** schon einen sehr guten governed Lebenszyklus (Proposals, Scanner, Grader, Auto/Propose, Weekly Review) — aber **post-run**, aus vergangener Arbeit. Unser Unterschied: beobachtungsgetrieben **während und über** Sessions hinweg, incident-verknüpft („dieser Skill verhindert Fehlerklasse X"), plus dieselbe Maschinerie für **Tools** (3.4). Wir kopieren nichts — wir bauen auf denselben Ideen auf, mit anderem Trigger und breiterem Scope.

### 3.4 Tool-Mining & Tool-Discovery („dasselbe für Toolvorschläge")

**Gap-Signale:**
- Wiederkehrende Shell-/Tool-Sequenzen (N-Gramme der Tool-Calls): dasselbe 2–4-Schritt-Muster ≥ 3× in ≥ 2 Sessions.
- **Wegwerf-Skripte:** ähnliche Inline-Skripte ≥ 2× generiert und wieder gelöscht → klarer Tool-Kandidat.
- Explizite Marker: der Agent formuliert „dafür bräuchte ich eigentlich …" oder baut manuell, was ein Tool sein sollte.
- Häufige teure Checks („immer derselbe 30-Zeilen-Python-Check").

**Pipeline:**
1. Kandidat → Spec (Name, Beschreibung fürs LLM, JSON-Schema der Parameter, 2–3 Beispielaufrufe).
2. Implementierung (Python) → **Sandbox** (bubblewrap/firejail, optional Docker) → auto-generierte Tests (Happy Path + Edge Cases).
3. **Replay-Validierung:** neue Tool-Implementierung gegen historische Fälle aus dem Event-Log laufen lassen (stimmt das Ergebnis mit der damaligen Handarbeit überein?).
4. Registrierung als **MCP-Tool** (versioniert, Permission-Scope, Audit). 5. Telemetrie: Nutzung, Fehlerrate → Auto-Repair-Vorschlag oder Deprecation.
5. **Discovery zuerst:** Bevor gebaut wird, sucht der Mentor im Ökosystem (MCP-Registry, ClawHub, dsh-Plugins) — „gibt es das schon?" ist die erste Frage, Synthese die zweite. (Damit wird der Harness auch ein guter Bürger der offenen Ökosysteme.)

### 3.5 Research-Memory & Pre-Warming („Research-Ergebnisse Memory")

Zwei Mechanismen:
1. **Opportunistisch:** Alles, was der Hauptagent ohnehin recherchiert (Websuche, Doku-Funde), wird vom Mentor eingesammelt und als kuratierte Note (Claim+Quelle+Version) abgelegt — Wissen verdampft nicht mehr am Session-Ende.
2. **Proaktiv (Pre-Warming):** Der Research-Agent kennt die Projekt-Dependencies (Lockfiles, Imports) und legt **versionsgenaue Doku lokal ab** (z. B. exakt die installierte PyTorch-Version) sowie Vorab-Notes zu häufigen Problemklassen der Domäne (CUDA-OOM, autograd-Fallen, DataLoader-Deadlocks…). Das löst das **Cold-Start-Problem** des Incident-Stores ab Tag 1.

Qualitätsregeln: jede Note mit Quelle + Abrufdatum + Gültigkeit; widersprüchliche Quellen werden als **Konflikt markiert und dem Agenten gezeigt** (statt still eine zu wählen); abgelaufene Notes werden revalidiert oder verworfen.

### 3.6 Konkrete Szenarien (PyTorch-Beispielprojekt)

- **A · CUDA-OOM-Schleife:** Agent variiert erfolglos batch_size → Loop-Erkennung → Incident #1234 aus Session vom 14.08. → Hint mit Lösung + Belegen → Agent wendet an, Nachtest grün. Incident-Confidence steigt.
- **B · Test-Muster:** Immer dieselben seeded-Test-Fixtures per Inline-Skript → nach 3 Vorkommen Skill-Draft „torch-test-fixture (seed, determinism, cleanup)" mit Pitfalls („ohne seed flaky", „device nicht synchron").
- **C · Tool-Lücke:** `nvidia-smi` + `ps` + manueller VRAM-Check, 4× in 2 Sessions → Tool-Vorschlag `gpu_status` (JSON: VRAM-Auslastung, Prozesse, Empfehlung) → MCP-Tool.
- **D · Doku-Frage:** Shape-Broadcast-Bug → Mentor findet Pre-Warm-Note „Broadcasting-Regeln" (PyTorch-Docs, Version gepinnt) → Hint mit Doku-Zitat.

### 3.7 Governance & Sicherheit (muss, nicht kann)

- **Prompt-Injection ist das größte Risiko unseres Features** (wir injizieren Fremdinhalt in den Loop). Maßnahmen: Licht-auf-dunkel-Markierung (Spotlighting 2403.14720), Delimiter + „Daten, keine Anweisungen"-Framing für alle Fremdinhalte, Sanitizing/Scanning von Notes und Skill-/Tool-Vorschlägen (OpenClaw-Standard), kritische Befunde **blocken** die Promotion, Tool-Result-Parsing-Verteidigung (2601.04795), System-Level-Defaults (2603.30016). Jeder Hint ist als `[mentor …]` markiert und wird vom Hauptagent-Prompt als „nicht vertrauenswürdiger Rat, prüfen" behandelt.
- **Privacy/Skope:** lokal-first; Memory scoped pro Repo/Profil; Cross-Repo-Weitergabe nur opt-in (vgl. Privacy-Survey 2606.26627).
- **Kontrolle:** `mentor_log` + „Warum bekam der Agent diesen Hinweis?"-Ansicht; Memory-Diff-Ansicht nach jeder Night Shift; Budgets; Kill-Switche (`mentor off`, `learning propose|auto|off`); keine Cloud-Komponente ohne explizite Freigabe.

---

## 4. Architektur

```
                 ┌─────────────────────────────────────────────────┐
                 │                      TUI (M5: Desktop)           │
                 └───────────────▲─────────────────────────────────┘
                                 │ JSON-RPC/WS-Protokoll (typed, versioniert)
        ┌────────────────────────┴────────────────────────────────────┐
        │                         agentd (Kern)                        │
        │  Agent-Loop · Tool-Runtime (Sandbox) · Kontext-Assembly      │
        └───────▲──────────────────────┬──────────────────────────────┘
                │ Events               │ Tool-Calls
                │ (append-only)        ▼
        ┌───────┴────────┐     ┌──────────────┐        ┌───────────────────┐
        │  Event-Bus     │◄────│  Toolbox      │        │  Model-Gateway    │
        │  SQLite WAL    │     │  Sandbox      │        │  LiteLLM, 3 Tiers │
        └───────▲────────┘     └──────────────┘        └───────────────────┘
                │ liest ALLE Events (nicht-blockierend, eigener Prozess)
        ┌───────┴──────────────────────────────────────────────────────┐
        │                          mentord                             │
        │  Trigger-Engine · Retrieval · Synthese · Hint-Inbox · Audit  │
        │  Night-Shift-Scheduler (Konsolidierung, Skill-/Tool-Mining)  │
        └───────▲──────────────────────────────────────────────────────┘
                │ liest/schreibt (gated)
        ┌───────┴──────────────────────────────────────────────────────┐
        │  Memory-Store: SQLite + FTS5 + sqlite-vec + Blobs            │
        │  incidents · notes · skills · tools · hints · mentors · events│
        └───────────────────────────────────────────────────────────────┘
```

**Entscheidungen und Begründungen:**
- **Eigener Prozess für den Mentor** (Crash-Isolation; blockiert nie den Hauptloop; kann später in anderen Harnessen als MCP-Server laufen — siehe Wedge-Strategie).
- **Event-Sourcing** (DSH-Vorbild): alles replay-/fork-/auditierbar; der Mentor ist nur ein weiterer Subscriber; Evaluation kann komplette Sessions offline nachspielen (**Shadow-Mode** = Replay mit Mentor, ohne Einfluss).
- **Protokoll-first** (OpenClaw-Vorbild): ein typed WS/JSON-Protokoll für **alle** Frontends → TUI heute, Desktop M5 ohne Architekturumbau. Das ist die wichtigste Vorsichtsmaßnahme gegen „TUI-first, Desktop-Rewrite".
- **Prompt-Cache-Disziplin:** System-Prompt + Tool-Set bleiben ab Session-Start konstant (frozen); Memory-Snapshot beim Start wie bei Hermes; alles Neue (Retrieval-Blöcke, Mentor-Hints) kommt **append-only** in die Trajektorie. Kein Mid-Session-Rewrite → Cache bleibt heiß, Kosten niedrig.
- **Kontext-Assembly (3 Kanäle):** (1) stabiler Kern (Systemprompt, Skills-Index, Pins) — cachebar; (2) turn-scoped Retrieval (Memory, Doku, Notes) — gebudgetet (Default: ≤ 1.500 Tokens pro Turn, adaptiv); (3) append-only Verlauf.
- **Modell-Tiers:** Tier 1 klein/lokal (Trigger-Triage, Judge, Embeddings — oft 0 API-Kosten), Tier 2 mittel (Mentor-Synthese, Night Shift), Tier 3 der Hauptagent des Nutzers (frei wählbar, z. B. starkes Coding-Modell). Alles provider-agnostisch über LiteLLM.
- **Sprache/Tech:** Python 3.12+ (Kern; Begründung: PyTorch-Use-Case, LiteLLM, MCP-SDK, sqlite-vec, Textual-TUI), SQLite (WAL, FTS5, Vektorindex), Sandbox bubblewrap/firejail (Linux), Packaging via `uv`/`uvx single command`, Tests pytest. Desktop (M5): **Tauri 2** + React (leicht, gleicher Protokoll-Client) — Alternativ Electron nur, falls Plugin-Ökosystem-Parität wichtiger wird.
- **Kostenkontrolle eingebaut:** jedes Tier loggt Tokens; Dashboard ab M2 in der TUI („Mentor kostete heute 3,2 % Overhead").

**Wedge-Strategie (empfohlen, nicht optional):** Ab M4 exportieren wir **Memory + Mentor als MCP-Server** (`metis-memory`). Damit testen wir unser Gehirn in **Claude Code, Hermes und OpenClaw** (die alle MCP-Clients sind), sammeln Felddaten und Proof, bevor unser eigener Harness reif ist. Umgekehrt importiert unser Harness fremde Skills (agentskills.io) und Tools (MCP). Das ist gleichzeitig Distribution: Unser Memory-Format wird zum Standard, ohne dass jemand seinen Harness wechseln muss.

---

## 5. Evaluationsplan (der Überlegenheits-Beweis)

**Baselines:** (a) derselbe Harness, Mentor aus (Ablation); (b) mini-SWE-agent als Minimal-Referenz; (c) Claude Code / Hermes / ggf. DSH auf identischen Tasks; (d) Shadow-Mode-Replays (Mentor läuft mit, wirkt nicht).

**Aufgaben:** SWE-bench-Verified-Subset (echte Repos), Terminal-Bench 2.0, Long-Horizon-Terminal-Bench, ProgramBench; Memory: LongMemEval-V2; **eigene „Repo-Continuity"-Suite**: dieselbe Codebasis über 5–10 Sessions mit eingebauten wiederkehrenden Fehlerklassen (scripted + realistische Bugs).

**Kernmetriken:**
- **Recurrence-Kill-Rate (Killer-Metrik):** Anteil der in Session s gelösten Problemklassen, die in den nächsten N Sessions (gleiches Repo) **nicht** wiederkehren. Ziel: ≥ 60 % vs. Baseline. (Diese Metrik existiert noch nirgends sauber — wir definieren sie und veröffentlichen den Messaufbau.)
- **Hint-Precision** (helpful / delivered, Ziel ≥ 70 %), **Harm-Rate** (< 5 %), Acceptance-Rate.
- Task-Erfolgsrate, **Time-to-Solve**, **Tokens/Task** (inkl. Mentor-Overhead, Ziel ≤ 15 %), Kosten/Task.
- Skill-Nutzungsrate & Erfolgsrate; Tool-Adoption & Fehlerrate; Research-Note-Trefferquote.

**Ablationsmatrix:** ohne Memory · ohne Mentor · ohne Skills · ohne Tool-Mining · full · Shadow. **Anti-Gaming:** Held-out Tasks, Hints fließen nie ins Grading ein, Hack-Verifiable-Bench (2608.22103) als Kontrolle.

**Ehrliche Erwartung:** Einzel-Benchmark-Sprünge werden moderat sein (der Scaffold ist nicht mehr der Flaschenhals — mini-SWE-agent beweist das). Der große Effekt liegt bei wiederkehrenden Problemen und Multi-Session-Arbeit. Genau das messen wir — und genau das ist die Marktlücke.

---

## 6. Roadmap

| Meilenstein | Zeit (Orientierung) | Deliverables | Akzeptanzkriterium |
|---|---|---|---|
| **M0 Kern** | W 1–3 | agentd: Loop, Tool-Runtime (Sandbox), Event-Log, simples TUI, Session-Resume | Ein echter Coding-Task läuft lokal durch; jedes Event persistiert; Replay funktioniert |
| **M1 Memory v1** | W 4–6 | L1 Incidents, L4 Research-Notes, Embeddings, Retrieval-API, 3-Kanal-Kontext, Night-Shift v0 | „Hatten wir das schon?"-Query liefert belegte Treffer; Konsolidierung schreibt sauber (Gates greifen) |
| **M2 Mentor v1** | W 7–9 | Trigger-Engine (Stufe 1), Triage (Stufe 2), Hint-Protokoll + Budget, `mentor_log`, Mentor-Pane in TUI, Shadow-Mode | Erste A/B-Zahlen (mentor an/aus/shadow) auf eigenem Korpus; Precision-Messung steht |
| **M3 Lernen** | W 10–14 | Skill-Mining-Pipeline + Promotion-Gates; Tool-Mining + Replay-Validierung; Research-Prewarming | Erster automatisch vorgeschlagener & shadow-validierter Skill; erstes synthetisiertes Tool im Einsatz |
| **M4 Beweis & Interop** | W 15–18 | Benchmark-Läufe (Terminal-Bench 2.0, Repo-Continuity-Suite); Injection-Härtung; **MCP-Export (Memory+Mentor)** | Recurrence-Kill-Rate-Report v1; `metis-memory` läuft in Claude Code & Hermes als MCP-Server |
| **M5 Desktop** | W 19–24 | Tauri-App: Trajectory-View, Mentor-Feed, Memory-Browser (Graph), Skill-/Tool-Studio | Feature-Parität zur TUI + die drei Views, die die TUI nicht kann |

---

## 7. Risiken

| Risiko | Wirkung | Gegenmaßnahme |
|---|---|---|
| **Hint-Qualität** — falsche Hinweise schaden (Studien: Selbstkorrektur ohne Evidenz schlägt fehl) | Vertrauen + Performance kaputt | Precision-First, Evidenzpflicht, Shadow-First-Tuning, Budget, ignorierbares Framing |
| **Cold-Start** (leeres Memory an Tag 1) | Mentor nutzlos in Woche 1 | Pre-Warming (Doku, Dependency-Index), Import bestehender CLAUDE.md/Notes, öffentliche Vorab-Quellen |
| **Overhead** (Kosten/Latenz) | Nutzer schaltet Mentor ab | Eigener Prozess, kleine Modelle für Stufe 1/2, harte Budgets, Overhead sichtbar machen |
| **Memory-Müll/Drift** | falsche „Erfahrungen" zementiert | Schreib-Gates + Verifikationspflicht, Importance-Gating, Decay, Provenienz, Night-Shift-Audit, `forget` |
| **Prompt-Injection via Retrieval** | Sicherheitsvorfall | 3.7 (Spotlighting, Scanner, Block bei kritischen Befunden, untrusted-Flag) |
| **Scope-Creep** (zu viele Features gleichzeitig) | nichts wird fertig | Nicht-Ziele in §0; mini-SWE-Lehre; M0–M2 sind unverhandelbar der schmale Pfad |
| **Überversprechen** („schlägt alle Harnesse") | Glaubwürdigkeit | Wir behaupten: bessere **Lernkurve** und **Multi-Session-Utility** — und messen es öffentlich. Keine One-Shot-Wunder behaupten |

---

## 8. Name

- **Entschieden: „Skaldir Agent".** Der Lenkungs-Layer heißt intern **„The Voice"** (statt „Mentor"). Subsystem-Namen und Details: `DESIGN-2-GODS-VOICE.md` §0.
- Vor Veröffentlichung: Marken-/Domain-/Handle-Check („SkaldirAgent", „Skaldir Agent AI"). Der Name setzt hohe Erwartungen — das Gegenmittel ist das Prinzip: **spricht selten, aber mit Belegen.**

## 9. Offene Entscheidungen (für Willy)

1. **Repo-Name/Handles** zu „Skaldir Agent" festlegen; Marken-Check (Name selbst ist gesetzt).
2. **Sprache:** Python-Kern bestätigt? (Alternative: TS/Node — bessere Nähe zum OpenClaw/DSH-Ökosystem, aber schlechter für den PyTorch-Use-Case.)
3. **Lizenz:** Apache-2.0 (empfohlen, Patent-Klausel) vs. MIT (wie OpenClaw/DSH).
4. **Autonomie-Default:** Skills/Tools erst `propose` (empfohlen) oder gleich `auto` mit Rollback?
5. **Wedge-Strategie:** MCP-Export früher ziehen (schon ab M2 als Alpha, um in Hermes/Claude Code Daten zu sammeln)?
6. **Desktop:** Tauri (empfohlen) vs. Electron — Präferenz?

---

*Quellen, alle Links und Take-aways: siehe `SOURCES.md`. Dieses Dokument ist ein lebendes Design — Änderungen mit Datum und Begründung im Kopf der Datei pflegen.*
