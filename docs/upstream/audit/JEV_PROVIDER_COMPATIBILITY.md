# JEV_PROVIDER_COMPATIBILITY.md — TypeSafe/Jev provider contract of this build

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, uncommitted working tree.
Pflichtartefakt nach `JEV_IMPLEMENTATION_GOAL.md` §15 („HTTP/SDK-Vertrag dokumentieren"),
§17 (Providerfehler), §16 (Treasury), §21 (Canary). Evidenzklassen nach `ABNAHME.md` §5.1.

## 0. Die ehrliche Antwort zuerst

**Es gab in diesem Arbeitslauf KEINEN echten TypeSafe-Aufruf.** Alles, was unten als
„verifiziert" steht, ist `contract`/`mock`/`integration` mit **Test-Doubles**; ein
einziger realer `jev-latest`-Call wurde nicht ausgeführt. Damit bleibt die
Höchstaussage `JEV_READY`; `JEV_LIVE_TESTED` und `JEV_ACTIVE_VERIFIED` bleiben
`blocked` (kein autorisierter TypeSafe-Zugang, `ABNAHME` §85/§117.1).

Reproduzierbar belegt:

```bash
$ cd ~/projects/gods-voice
$ .venv/bin/python -m pytest tests/test_jev_provider_compat.py tests/test_jev_provider_treasury.py -p no:randomly
32 passed in 0.31s
$ .venv/bin/python -c "from godsv.system_one_treasury import DEFAULT_HANDLE; print(DEFAULT_HANDLE)"
secret://typesafe/systemone
$ .venv/bin/python -c "import godsv.system_one_adapters as a; print(a.TYPESAFE_ENDPOINT)"
https://api.typesafe.ai/v1/systemone
```

Das leere Treasury dieses Hosts hat keinen Handle für `typesafe/systemone`; der
Live-Adapter meldet deshalb `blocked_external_access` (siehe §3).

## 1. Request-Vertrag (§15) — exakt wie im Auftrag, ohne erfundene Felder

| Größe | Wert | Ort im Code |
|---|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` | `system_one_adapters.TYPESAFE_ENDPOINT` |
| Auth | `Authorization: Bearer ***` | `TYPESAFE_AUTH_SCHEME` + Treasury-Adapter (§3) |
| Content-Type | `application/json` | `TYPESAFE_CONTENT_TYPE` |
| Modell | `jev-latest` | `TYPESAFE_MODEL` (Default von `TypeSafeJevEvaluator`) |
| `state` | `string \| object \| array` | `build_typesafe_request(..., state_shape=...)` |
| Fragen | `Noul`/`Choice`/`Score` gemischt in **einem** Call | `build_typesafe_request` |

Der Body enthält **ausschließlich** die dokumentierten Felder
(`DOCUMENTED_REQUEST_FIELDS = ("model", "state", "questions")`). Kein zusätzliches
Envelope-Feld, kein Routing-Hinweis, kein Retry-Zähler:

```bash
$ .venv/bin/python -c "
from godsv.system_one import SystemOneState
from godsv.system_one_adapters import build_typesafe_request
from godsv.system_one_questions import Question, QuestionSet
qs = QuestionSet(id='d', version='v1', owner='d', questions=(Question(id='d.n', version='v1', owner='d', kind='noul', text='N?', group='g'),))
print(sorted(build_typesafe_request(SystemOneState(payload={'task': 'x'}), qs)))"
['model', 'questions', 'state']
```

Die drei `state`-Formen sind getestet
(`tests/test_jev_provider_compat.py::test_state_may_be_a_string_an_object_or_an_array`):
`object` = das interne State-Envelope (Payload + `input_scope` + `fingerprint`),
`string` = dasselbe kanonisch serialisiert, `array` = Scope/Fingerprint/Payload.
Eine unbekannte Form wird abgewiesen, nicht geraten.

Der Frage-Mix wird **nicht** im Adapter zusammengestellt: die Fragen kommen
ausschließlich aus der versionierten Registry (§5/§39), der Adapter fügt nur die
Serialisierungsanweisung hinzu. Die Candidate-`Choice` des Computer-Use-Packs darf
nur bereits erlaubte Optionen enthalten (§10.3, siehe `JEV_CU_PACK.md`).

## 2. Providerfehler, Retries, Deadline (§17, §43, §67)

Sieben Codes als erstklassige Namen (`PROVIDER_ERROR_CODES`), auflösbar über
`classify_error(exc)` / `error_code_for_status(status)`:

| Code | Auslöser | interne Exception (unverändert) | Retry |
|---|---|---|---|
| `connection_invalid` | 401 | `SystemOneAuthError` | nie |
| `request_contract_error` | 422 | `SystemOneContractError` | nie |
| `provider_rate_limited` | 429 | `SystemOneRateLimited` | ja, innerhalb der Deadline |
| `provider_overloaded` | 529 | `SystemOneRateLimited` | ja, innerhalb der Deadline |
| `provider_timeout` | `TimeoutError`/`asyncio.TimeoutError`/`.timeout` | `SystemOneTimeout` | ja, innerhalb der Deadline |
| `provider_unavailable` | Transportfehler ohne Status (z. B. `ConnectionError`) | `SystemOneError` | nein |
| `adapter_error` | lokale Mapping-/Validierungsfehler (`JSONDecodeError`, eigene `SystemOneContractError`) | `SystemOneContractError` | nein |

Der interne Fehler behält seine Klasse und trägt zusätzlich `err.code`. Retries
laufen ausschließlich über den geteilten Entscheider
`system_one_policy.plan_retry` (401/422 nie, 429/529/Timeout nur solange Versuche
**und** Deadline reichen); SDK-interne Retries (`exc.sdk_retries`,
`response["sdk_retries"]`) bleiben im Gesamtzähler sichtbar
(`evaluation.attempts` / `evaluation.sdk_retries`).

Bewusste Verhaltensänderung gegenüber dem Vorzustand: ein Provider-Fehler **ohne**
Status ist jetzt `provider_unavailable` und nicht mehr ein synthetischer
`SystemOneTimeout` (§17 trennt „Timeout" von „Transport"). Belegt durch
`test_every_code_is_reachable_from_a_concrete_failure`.

Fallback-Kette (§17) als `SystemOneFallbackChain`:

```text
primary (typesafe_jev)
  ├─ authorized LLM System-One fallback   (nur bei llm_fallback_authorized=True)
  ├─ System-2 Voice fallback              (voice_fallback; liefert KEINE System-One-Antwort)
  └─ degraded observe/blocked             (evaluation=None, error_codes gefüllt)
```

`FallbackOutcome` nennt immer `rung`, `primary_failed`, `error_codes`; ein
LLM-Fallback-Ergebnis bleibt `provider_mode=llm_system_one_adapter`,
`live_jev=False`. Getestet mit Doubles:
`tests/test_jev_provider_compat.py` (5 Fallback-Tests, u. a. „unautorisierter
LLM-Fallback wird gar nicht erst aufgerufen", „beide Rungs scheitern → degraded").

## 3. Treasury und Schlüsseldisziplin (§16, ABNAHME §86/JEV-08)

`ConnectionRef` hat genau sechs Felder:

```text
provider = typesafe      target = system_one      scope = evaluate
secret_handle = secret://typesafe/systemone
status   = unconfigured | missing | expired | unauthorized | active
revision = rev-<12 hex>  (deterministisch aus Kind/Scopes/created abgeleitet)
```

Aufgelöst wird gegen den **echten** `godsv.treasury.Vault` (Fernet + scrypt,
`TreasuryState` für Grants) — kein zweiter Store, keine Key-Datei, kein Env-Var.
Die Revision ist *abgeleitet*, nicht persistiert (der Treasury hat keine
Revisionsspalte); dieselbe Entry ergibt dieselbe Revision, eine rotierte Entry
eine neue. Ohne `evaluate`-Scope → `unauthorized`, ohne Handle → `missing`,
abgelaufen → `expired`; in allen drei Fällen liefert der Resolver `None` und der
Evaluator bleibt `blocked_external_access`.

Kein Key in Prompt, Event, Model State, Memory, Logs, Diagnostics:

* was zum Evaluator wandert, ist eine **Referenz** (`handle`, `provider`,
  `target`, `scope`, `revision`) — nie das Material;
* `SystemOneTreasuryAdapter.authorization_headers()` holt das Material pro
  Versuch, benutzt es für einen Header und verwirft es; nichts wird am Adapter,
  an der Evaluation oder in einem Event gespeichert;
* `assert_no_secret(payload, secret, where)` prüft zwei unabhängige Kriterien
  (exaktes Material inkl. url-quoted + `secretscan.scan`) und schlägt **fail-closed**
  fehl; `GuardedEmitter` erzwingt das *vor* jeder Aufzeichnung;
* „Screenshots": dieser Pfad erzeugt und versendet **keinen** Screenshot (§34);
  ein Screenshot-Store existiert auf diesem headless Host nicht — die Klausel ist
  damit `not_run`, nicht „geprüft".

Der Beweis, der bei einem Leck **fehlschlagen müsste**:

```bash
$ .venv/bin/python -m pytest tests/test_jev_provider_treasury.py -p no:randomly -k leak
1 passed, 13 deselected in 0.04s

(der Guard-Test selbst; die Integrationsbeweise ohne Leck laufen in den uebrigen
13 Tests derselben Datei mit)
```

Enthalten sind: ein echtes Vault mit echtem gespeichertem Secret, ein Transport,
der die Header sieht (`Authorization: Bearer <secret>` **nur** dort), und die
Assertion, dass Request, `usage`, `evidence()`, `as_dict()` und `raw_ref`
zusammen das Secret **nicht** enthalten (`secretscan.redact` findet nichts).
Bekannte Grenze des Projekt-Redaktors: `secretscan.generic_key` greift nur in
key-artigem Kontext (`api_key:`/`secret:`/`token:`); der Guard matcht zusätzlich
das Material selbst — auch dieser Fall ist getestet.

## 4. Canary-Bezug (§21)

Die Stufen J0…J5 und ihre Flag-Zuordnung liegen in `godsv.system_one_canary`;
provider-seitig relevant ist die Kappung: **der LLM-Readiness-Adapter erreicht
höchstens J3**. `J4`/`J5` verlangen einen eingefrorenen Threshold-Profile-Nachweis
(`J5` zusätzlich Holdout + unabhängigen Review) und werden sonst verweigert.
Siehe Abschnitt 6 in `JEV_CU_PACK.md` und `tests/test_jev_canary_stages.py`.

## 5. Offen / nicht gefahren (je Punkt mit nächster Aktion)

| Was | Status | Warum | Kleinste nächste Aktion |
|---|---|---|---|
| Echter `jev-latest`-Call (§15) | `blocked_external_access` | kein autorisierter Zugang; kein Treasury-Handle für `typesafe/systemone` | Handle über `godsvoice vault add` setzen (Owner-Schritt), dann `TypeSafeJevEvaluator` mit `credential_resolver=SystemOneTreasuryAdapter(...).credential_reference` fahren |
| Offizielles SDK `typesafe-sdk` | nicht verwendet | in diesem Repo nicht installiert; Transport ist injiziert | SDK installieren (Python ≥ 3.10) und als `transport` einhängen; `sdk_retries`-Feld des SDK auf `response["sdk_retries"]` mappen |
| SDK-Retry-Inventar (echte Zahlen) | `not_run` | ohne SDK keine realen SDK-Retries | nach SDK-Einbau: ein Fehlerlauf mit `sdk_retries` und ein Erfolgslauf, beide gegen den Gesamtversuchszähler |
| 429/529/Timeout gegen den echten Provider | `not_run` | nur mit Zugang | Fehler provozieren (Rate-Limit-Test-Account) und Codes + `attempts` protokollieren |
| Screenshot-Klausel (§16) | `not_run` | headless Host, kein Bildpfad in diesem Modul | Desktop-Host mit Display; Bild-Hash vs. Vault-Werte prüfen |
| `JEV_LIVE_TESTED` / `JEV_ACTIVE_VERIFIED` | `blocked` | ohne Live-Call bzw. ohne eigene Kalibrierungs-/Holdout-Daten | siehe oben + echte Entwicklungsdaten (siehe `JEV_CU_PACK.md` §7) |

## 6. Was dieser Adapter ausdrücklich NICHT ist

Kein zweiter Orchestrator, keine zweite Policy-Engine: der Adapter transportiert,
validiert streng und übersetzt in den internen Vertrag (§42). Kein Business-Entscheid,
kein Completion, keine Rechte, kein Routing. Ein Adapter-Ergebnis erscheint nie als
`typesafe_jev_live` (siehe `tests/test_system_one_adapters.py`,
`test_adapter_result_is_never_labelled_live_jev`), und ein `typesafe`-Provider ohne
`live_jev=True` wird von `provider_mode_for` als Verdrahtungsfehler zurückgewiesen
statt beschönigt.
