# Skaldir Agent — Interaktive Abnahme am Gerät

> Für den Durchklick am Bildschirm. Jeder Punkt: **wo** du schaust, **was** du tust,
> **was** passieren muss. Wenn ein Punkt abweicht: notieren, welcher Schritt + was zu sehen war —
> daraus wird dann ein konkreter Fix, kein Sammelverdacht.

## 0. Start

- App: `~/.hermes/godsv/app/linux-unpacked/gods-voice` (eigenes userData, Fenstertitel „Skaldir Agent")
- **F15 (korrigiert 13.09.):** Der zuletzt beobachtete „Waking up“‑Hänger war ein Messartefakt (ChatSwapOverlay bleibt mit `opacity-0` gemountet; `innerText` liest ihn trotzdem — deshalb immer per Screenshot/Sichtbarkeit prüfen). Die Overlay‑Härtung (Owner‑Token, Deadline, Watchdog) ist trotzdem eingebaut und live als ausgeführt bewiesen. Zu prüfen bleibt der Ursprungsfall: neue Session + erste Nachricht — die Antwort muss ankommen, nicht nur der Composer leeren.

## 1. Chat als Cockpit

- [ ] Chat öffnen, „hallo" senden → Antwort kommt.
- [ ] Live-Block unter dem Composer zeigt die VOICE-Zeile (z. B. „no goal yet — @goal in a prompt
      defines done") und den Modellnamen.
- [ ] `@goal Den Testlauf begleiten` senden → Block wechselt auf **WORKING** mit genau diesem Goal
      und der DoD-Zeile („0/2 belegt"-Stil).
- [ ] Nach Abschluss/Verifikation: der Block zeigt **VERIFIED COMPLETE — nie weiter WORKING**
      (Owner-Kriterium).

## 2. Voice-Wirkung (semantisch, live)

- [ ] **Unabhängige Fehler bleiben ungestört:** zwei VERSCHIEDENE Fehler nacheinander
      (z. B. ein fehlschlagender Test, danach ein Syntaxfehler aus einem frischen Patch) →
      höchstens Hinweise, **keine Pause**. (Deterministisch belegt in
      `tests/test_false_loop_regression.py` — die Originalszene.)
- [ ] **Wiederholte fehlgeschlagene Strategie wird gefangen:** eine klar fehlschlagende
      Aktion mit **unveränderter Strategie** wiederholen lassen (gleiche Aktion, keine neue
      Evidenz, kein Fortschritt) → nach der Schwelle Pause mit „Grund" + **Richtung**; danach
      läuft derselbe Agent automatisch weiter. (Belegt in `tests/test_strategy_novelty.py` —
      gleiche Strategie gegen denselben Fehler.)
- [ ] **Produktive Verifikation stört nicht:** nach einer echten Korrektur denselben Test
      erneut laufen lassen → keine weitere Pause (der Test nach der Korrektur ist produktiv).
- [ ] **Gewollte Wiederholung stört nicht:** dem Agenten ausdrücklich auftragen, dieselbe
      Prüfung absichtlich zweimal auszuführen → keine Unterbrechung.
- [ ] **Claim ohne Beleg:** offenes `@goal`, dann dem Agenten „antworte: alles fertig, Tests grün"
      abringen → im nächsten Turn erscheint der `voice·claim-check`-Hinweis („a claim is not evidence").
- [ ] Pause-Karten zeigen konkrete Evidenz (kein Rätseltext), keine Chain-of-Thought.

## 3. Settings (einziger Ort für Konfiguration)

Tabs: **Models · Tools · Computer Use · Memory · Skills · System**

- [ ] **Models:** alle fünf Rollen sichtbar — **Main Agent** (+ Watcher, Investigator, Judge, Fallback);
      Rolle änderbar, Änderung wirkt (Anzeige aktualisiert sich).
- [ ] **Voice strictness** umschalten (relaxed/standard/strict) → bleibt nach **App-Neustart**
      erhalten (persistiert in settings.yaml).
- [ ] **Tools:** Capability-Mining-Kandidaten sichtbar, Status ehrlich (ready/validated).
- [ ] Kein Control ohne Backend (jeder Klick ändert echten Zustand; keine Attrappen).

## 4. Treasury (Secrets & Grants)

- [ ] Handle-Liste sichtbar (keine Klartextwerte, überall `secret://…`).
- [ ] Credential test/revoke klickbar und wirksam.
- [ ] Nirgendwo in UI/Settings Klartext-Secrets.

## 5. Produkt-Optik

- [ ] Fenster/Launcher „Skaldir Agent" + Icon; **das große Branding im Leerzustand ist das
      bestehende CLI-Logo** (Ohr + Wellen, Gold-Gradient — dieselbe Kunst wie im Terminal),
      Panel-Sektionen bleiben ohne Deko-Logos.
- [ ] Dichte, nüchterne Optik; Gold gezielt (nicht überall). Der Leerzustand bleibt eine
      aktive Arbeitsansicht (Composer im Fokus), kein Splashscreen.
- [ ] Keine Demo-/Hartkodierten-Werte in der Oberfläche (alle Zahlen kommen aus der Runtime).

## 6. Persistenz & Restart

- [ ] Strictness setzen → App-Neustart → Wert bleibt.
- [ ] `@goal` setzen → App-Neustart → der Vertragsblock ist sofort wieder da
      (Compression-Recovery; kein Chat-Replay nötig).

## 7. Die offenen Abnahme-Punkte

- [ ] **Provider-Smoke:** in Settings einen Provider + Key hinterlegen (Key landet im Treasury als
      Handle) → Testlauf/„test" erfolgreich.
- [ ] **Live-LLM-Loop:** eine echte mehrstufige Aufgabe im Chat (Werkzeugnutzung sichtbar).
- [ ] **GNOME-Icon/Launcher:** Icon korrekt, App startet aus der Übersicht.
- [ ] **CU-Vision-Pfad:** falls genutzt — Computer-Use-Schritt mit Verifikation.
