# JEV-ID-MAP — Auftrags-IDs (`JEV_IMPLEMENTATION_GOAL.md` §31) gegen `ABNAHME.md` §79–§98

**Warum diese Datei existiert:** `JEV_IMPLEMENTATION_GOAL.md` §31 sagt „Diese IDs mit `ABNAHME.md`
synchron halten" — die beiden Listen sind aber **nicht identisch**. Beide benutzen `JEV-01`…`JEV-20`
mit teils gleicher, teils verschobener Bedeutung. Wer eine Aussage wie „JEV-12 ist erfüllt" macht,
muss sagen, welche Liste gemeint ist. **Für die Abnahme gilt `ABNAHME.md`**, weil das der bindende
Vertrag ist; die Auftrags-IDs werden nur in den Artefakten dieses Auftrags verwendet und hier
zugeordnet.

## 1. Zuordnung

| Auftrag §31 | Bedeutung im Auftrag | `ABNAHME.md` | Bedeutung dort | Deckung |
|---|---|---|---|---|
| JEV-01 | Provider-Abstraktion | JEV-01 (§79) | Providerneutraler System-One-Vertrag | 1:1 |
| JEV-02 | Interner typisierter Vertrag | JEV-02 (§80) | Interner Antwortvertrag (Noul/Choice/Score) | 1:1 |
| JEV-03 | Versionierte Question Packs | JEV-03 (§81) | Versionierte Question Registry | 1:1 |
| — | (im Auftrag in §6 mitgeführt) | JEV-04 (§82) | Atomare Fragen, Code-Komposition | Auftrag hat **kein** eigenes Kriterium; Inhalt steckt in Auftrag §6 |
| JEV-04 | State Sanitization | JEV-05 (§83) | State Sanitizer | Inhalt gleich, **Nummer +1** |
| JEV-05 | LLM Compatibility Adapter | JEV-06 (§84) | LLM-System-One-Readinessadapter | Inhalt gleich, Nummer +1 |
| JEV-06 | TypeSafe Jev Provider | JEV-07 (§85) | TypeSafe-Liveadapter | Inhalt gleich, Nummer +1 |
| JEV-07 | Treasury | JEV-08 (§86) | Treasury-Integration | Inhalt gleich, Nummer +1 |
| JEV-08 | Providerfehler | JEV-09 (§87) | Fehler-/Deadline-Policy | Inhalt gleich, Nummer +1 |
| JEV-09 | Shadow Mode | JEV-10 (§88) | Shadow Mode | Inhalt gleich, Nummer +1 |
| JEV-10 | Risk-/Confidence Policy | JEV-11 (§89) | Kalibrierung und risikobasierte Thresholds | Inhalt gleich, Nummer +1 |
| JEV-11 | Voice Triage | JEV-12 (§90) | Voice-Triage-Pack | Inhalt gleich, Nummer +1 |
| JEV-12 | Stale Evaluation Rejection | JEV-14 (§92) | Staleness/Version Binding | Inhalt gleich, **Nummer +2** (JEV-13 wird übersprungen) |
| JEV-13 | Goal Completion Ledger | JEV-13 (§91) | Completion-Precheck | Ledger + Preflight deckt denselben Gegenstand breiter ab |
| JEV-14 | Semantic Evidence Matching | (JEV-13, semantischer Teil) | „…niemals allein Completion zertifizieren" | Auftrag trennt semantische Evidenzpassung als eigenes Kriterium; im Vertrag Teil des Prechecks |
| JEV-15 | Completion Gate Separation | (JEV-13 + §30 Ownership) | Gate-Autorität liegt beim Runtime-Gate | Auftrag trennt es als eigenes Kriterium |
| JEV-16 | GUIState | JEV-15 (§93) | GUIState-Vertrag | Inhalt gleich, **Nummer −1** |
| JEV-17 | Computer-Use Pre/Post Checks | JEV-16 (§94) | Computer-Use Reactive Checks | Inhalt gleich, Nummer −1 |
| JEV-18 | Visual Subtools | JEV-17 (§95) | Visuelle Subtools + System One | Inhalt gleich, Nummer −1 |
| JEV-19 | Benchmarks/Holdout | JEV-19 (§97) + JEV-11 (§89) | Benchmark/Kosten/Latenz + Kalibrierung | Auftrag bündelt Benchmark und Kalibrierung/Holdout in einem Kriterium |
| JEV-20 | Rollback/Degraded Mode | JEV-20 (§98) | Sicherer Rollout und Rückfall | 1:1 |
| — | (im Auftrag in §12) | JEV-18 (§96) | System-2-Eskalation | Auftrag hat es als Abschnitt, nicht als eigenes Kriterium |

## 2. Konsequenzen für Aussagen

* Jede Statusaussage in den Artefakten dieses Auftrags nennt **beide** IDs, z. B.
  „Auftrag JEV-09 (Shadow) = ABNAHME JEV-10 (§88)".
* Im Register (`audit/REQUIREMENTS.json`) und in `audit/verification-mapping.json` zählt
  ausschließlich die `ABNAHME.md`-Nummerierung. Die Auftragsnummerierung erscheint dort nie als
  Kriteriums-ID, damit zwei Kriterien nicht unter einer ID kollidieren.
* Wo der Auftrag eine Aussage an eine ID hängt, die es im Vertrag nicht gibt (Auftrag JEV-14, JEV-15),
  wird die Evidenz auf das tragende Vertragskriterium gebucht (ABNAHME JEV-13, §91) — nicht als
  eigenes Kriterium erfunden.

## 3. Was das für die drei Abschlussgrade bedeutet (§35–§37 des Auftrags, §117.1 des Vertrags)

    JEV_READY            = Auftrag §35 erfüllt, sofern dort auch der ehrliche Teil steht:
                           „kein Fake-Live-Nachweis", „Tests grün", „System-2-Fallback real getestet"
                           sind Bedingungen, die ohne echten Provider nur als Testnachweis gelten.
    JEV_LIVE_TESTED      = an ABNAHME JEV-07 (§85) gebunden: braucht echten TypeSafe-Zugang über
                           Treasury (Auftrag JEV-06/JEV-07) → heute `blocked_external_access`.
    JEV_ACTIVE_VERIFIED  = braucht ausgewerteten Shadow, Kalibrierung und Holdout aus echten
                           Entwicklungsdaten → heute `not_run`, kein Messwert liegt vor.

Kein Artefakt dieses Auftrags darf `JEV_LIVE_TESTED` oder `JEV_ACTIVE_VERIFIED` behaupten, solange
kein autorisierter TypeSafe-Aufruf dokumentiert ist.
