# Browser-Session-Reuse — zentraler Ensure-Lifecycle über CLI- und Agent-Pfad

Feature: `browser-session-reuse` (m4-web), erfüllt `VAL-WEB-003` (Sitzungen
wiederverwenden, keine Fensterflut) und schließt die gemessene Lücke aus Scrutiny
m3 Runde 2: der Browser-Handler (Fix 83115f7) baute einen `SessionManager`, benutzte
aber nur `.state_dir` — `ensure()`/`discover()`/der prozessübergreifende Start-Lock
wurden auf dem Agent-Pfad nie gefahren, und das Run-State-Dir (`<run runtime>/browser`)
divergierte vom State-Dir, das `skaldir desk browser` liest (`<CLI runtime>/browser`).

## Status

(in Arbeit — wird am Ende dieser Runde final gefüllt)

## Geplante Nachweise

1. 10 gleichzeitige Ensure-Aufrufe → genau 1 Browser-Start (Zähler-Test, Threads +
   echte Cross-Process-CLI-Proofs).
2. Leere Beobachtung → Zustand `unconfirmed` mit Diagnose, null neue Starts.
3. Detach lässt nicht-agenteneigene Fenster/Tabs offen (Live/ recorded).
4. `browser`-Tool im gemessenen Agent-Toolset (F08-Klasse).
5. Live-Reuse: Agent-Pfad und `skaldir desk browser` teilen EIN State-Dir und EINEN
   Session-Namen; zweiter Ensure → `reused`, Startzähler bleibt 1.
