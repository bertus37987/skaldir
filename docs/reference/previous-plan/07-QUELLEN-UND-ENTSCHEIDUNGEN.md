# Recherchegrundlage und Übernahmeentscheidungen

Recherchestand: 24. September 2026. Quellen wurden für die vorausgehende Recherche gelesen; sie sind keine unabhängig reproduzierten Skaldir-Ergebnisse. Aktuelle Bibliotheks-/API-Verträge bei der tatsächlichen Integration prüfen. Veröffentlichungen, insbesondere frische Preprints, liefern Ideen und Evidenz unter ihren jeweiligen Bedingungen.

## 1. Priorisierte Architekturquellen

| Quelle | Relevante Idee | Übertragung |
|---|---|---|
| [Prime Agent](https://www.primeintellect.ai/blog/prime-agent) | Programmatischer Zugriff auf Tools, Kontext und Subagents | Vorhandenen Executor entsprechend erweitern; kein Pflichtwechsel zu Python |
| [Memento-Skills](https://arxiv.org/abs/2603.18743) | Skills als veränderbares Verfahrenswissen | Kandidat, Prüfung, Wiederverwendung und Reparatur |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Sitzungsübergreifende Suche und Skill-Lernen | Referenz für praktische Abläufe, keine angenommene Skaldir-Abhängigkeit |
| [Letta Context Repositories](https://www.letta.com/blog/context-repositories/) | Dateibasiertes, versioniertes und selektiv geladenes Memory | Kleine Markdown-Dateien mit Herkunft und Version |
| [LCM](https://papers.voltropy.com/LCM) | Zusammenfassungen mit wiederauffindbaren Originalen | Originalbelege erhalten; Graph erst bei Bedarf |
| [Graphiti](https://github.com/getzep/graphiti) | Zeitliche Gültigkeit und Herkunft von Fakten | Metadaten übernehmen; Graphdatenbank nicht voraussetzen |
| [Mem0 Memory Algorithm](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm) | Faktenextraktion und mehrere Retrieval-Signale | Vergleichskandidat; Managed- und OSS-Ergebnisse unterscheiden |
| [SimpleMem](https://arxiv.org/abs/2601.02553) | Verdichtung, Konsolidierung und adaptive Abfrage | Als alternative Memory-Pipeline evaluieren |
| [EvolveMem](https://arxiv.org/abs/2605.13941) | Retrieval-Konfiguration anhand von Fehlern verbessern | Optionale spätere Optimierung |
| [EvoMemBench](https://arxiv.org/abs/2605.18421) | Vergleich verschiedener Memory-Formen | Kein universell bestes Memory voraussetzen |

## 2. Tool- und Kontextquellen

- [Anthropic Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use): bedarfsgeladene Tools und programmatische Tool-Aufrufe.
- [Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp): Rohdaten außerhalb des aktiven Modellkontexts verarbeiten.
- [Manus Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus): stabile Präfixe, externe Datenhaltung und nutzbare Fehlerhistorie.
- [Recursive Language Models](https://arxiv.org/abs/2512.24601): große Eingaben programmatisch untersuchen und gegebenenfalls zerlegen.
- [GPT-6 Sol/Luna und Caching](https://openai.com/index/introducing-gpt-6-sol-and-luna/): Beispiel dafür, dass Cache-Verträge providerspezifisch und zeitlich veränderlich sind.

Nicht übernehmen: jede Aufgabe rekursiv zerlegen, jeden Schritt delegieren oder alle alten Cache-Annahmen universell festschreiben. Einsparungen aus Herstellerberichten sind keine garantierten Skaldir-Einsparungen.

## 3. Selbstverbesserung

| Quelle | Übertragbarer Mechanismus | Grenze |
|---|---|---|
| [GEPA](https://arxiv.org/abs/2507.19457) | Prompts anhand von Ausführung und Feedback optimieren | Benötigt repräsentative Bewertung |
| [ACE](https://arxiv.org/abs/2510.04618) | Erfahrungswissen schrittweise ergänzen und korrigieren | Wachstum und Aktualität kontrollieren |
| [Darwin Gödel Machine](https://sakana.ai/dgm/) | Versionierte Kandidaten und empirische Codeverbesserung | Kein Beleg für unbegrenzte Verbesserung; Bewertungsmanipulation ist ein reales Problem |
| [Hyperagents](https://arxiv.org/abs/2603.19461) | Auch Verbesserungsmechanismus veränderbar | Spätere Forschung, kein Startumfang |
| [ModularRSI, 14.09.2026](https://arxiv.org/abs/2609.14857) | Einzelne Harness-Komponenten und getrennte Entwicklungsaufgaben | Übertragbarkeit selbst testen |
| [RRSI, 21.09.2026](https://arxiv.org/html/2609.24972v1) | Änderungsbudget, Kosten-/Komplexitätskontrolle und Pruning | Frischer Preprint; Gewinne abhängig von Feedback und Budget |
| [Dream-RSI, 14.09.2026](https://arxiv.org/abs/2609.14858) | Experimenthistorie für günstigere Explorationsauswahl | Replay ersetzt keine unbekannten realen Umgebungen |

Die Übernahmeentscheidung lautet: erst belegte Erfahrungen und getestete Skills, danach kleine kontrollierte Harness-Änderungen. Unkontrollierte Selbstmodifikation des laufenden Kernsystems wird verworfen.

## 4. Computer Use und Linux

- [Agent S](https://github.com/simular-ai/Agent-S): offenes Desktop-Agentenframework als Integrations- und Vergleichsreferenz.
- [Sai OSWorld 2.0](https://www.simular.ai/articles/sai-tops-osworld-2-0): Mischformen aus Code, Planung und Computer Use; Teilpunkte und vollständigen Erfolg trennen.
- [UI-TARS / Agent TARS](https://github.com/bytedance/UI-TARS-desktop): visuelle Interaktion und multimodale Tool-Nutzung; aktuelle Plattformunterstützung vor Integration prüfen.
- [AT-SPI Architektur](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/architecture.html): strukturierter Zugriff auf Linux-Oberflächen.
- [XDG ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html): Aufnahme und Stream-Verträge.
- [XDG RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html): Sitzung und Eingabe einschließlich EIS.
- [OSWorld 2.0](https://osworld-v2.xlang.ai/): längere Aufgaben, mehrere Prüfpunkte und unterschiedliche Bewertungsmetriken.

## 5. Modellkandidaten aus der Recherche

Aktuelle Vergleichskandidaten waren [Claude Opus 5.5](https://www.anthropic.com/claude-opus-5-5), [GPT-6 Astra](https://openai.com/index/gpt-6-astra/), [GPT-6 Sol/Luna](https://openai.com/index/introducing-gpt-6-sol-and-luna/) und [Navigator n2](https://yutori.com/blog/introducing-n2).

Dieser Implementierungsplan ist modellunabhängig. MiMo Code ist das vom Nutzer gewählte Werkzeug zur Umsetzung; daraus folgt keine Pflicht, Skaldirs Laufzeitmodell zu wechseln. Bestehende Provider, Nutzerentscheidungen und tatsächlich verfügbare Modelle berücksichtigen.

## 6. Bewusst verworfene oder bedingte Ideen

| Idee | Entscheidung | Begründung |
|---|---|---|
| Gesamte Wissensbasis im Systemprompt | Verwerfen | Kosten, Ablenkung, schlechte Skalierung |
| Zeilenverweise ohne Version | Verwerfen | Nach Änderungen falsche Belege |
| Informationen automatisch als Anweisungen behandeln | Verwerfen | Herkunft und Priorität werden vermischt |
| Jede erfolgreiche Antwort wird zu einem Skill | Verwerfen | Ungeprüfte, redundante oder einmalige Fähigkeiten |
| Memory-Produkte parallel als konkurrierende Wahrheiten | Verwerfen | Konflikte und doppelte Zuständigkeiten |
| Vollständiger Wissensgraph als Pflicht | Bedingt | Nur bei messbarem Bedarf |
| Vollständiges Runtime-Rewrite nach einem Forschungsframework | Bedingt | Nur falls kleinerer Umbau nachweislich nicht reicht |
| Mehrere Agenten gleichzeitig an Maus/Tastatur | Verwerfen | Konkurrenz und unzuverlässiger Zustand |
| Selbstoptimierung ohne zurückgehaltene Tests | Verwerfen | Anpassung an Beispiele statt übertragbarer Verbesserung |
| Eigene Modellgewichte trainieren | Außerhalb der ersten Stufen | Zusätzliche Daten-, Ressourcen- und Evaluationsvoraussetzungen |

MiMo soll diese Entscheidungen am tatsächlichen Code und an Ergebnissen überprüfen. Eine bessere belegte Alternative ist ausdrücklich erlaubt.

## 7. Ergänzende Recherche: Coding-Grundlagen

Am 24. September 2026 zusätzlich anhand der Primärdokumentation geprüft: [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), [OpenHands SDK](https://docs.openhands.dev/sdk), Aider zu [Prüfkommandos](https://aider.chat/docs/usage/lint-test.html), [Repository Maps](https://aider.chat/docs/repomap.html) und [Git](https://aider.chat/docs/git.html), Gemini CLI zu [Tools](https://geminicli.com/docs/reference/tools/) und [Checkpoints](https://geminicli.com/docs/cli/checkpointing/) sowie Anthropic zu [langen Agentenläufen](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

Konkrete Quellenbeobachtungen, Grenzen und Übernahmeentscheidungen stehen in [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md). Die 24 Anforderungen sind eine technische Synthese für Skaldir, kein offizieller universeller Standard. Prüfung und kontrollierte Ausführung haben Vorrang vor zusätzlichen Frameworks. LSP, MCP, Multiagenten und RSI sind keine allgemeine Mindestvoraussetzung.
