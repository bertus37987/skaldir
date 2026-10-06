# Browser-State-Liveness-Reconcile — State-Ehrlichkeit der Browser-Sitzung

Feature: `browser-state-liveness-reconcile` (m4-web). Fix des gemessenen
State-Ehrlichkeits-Defekts aus browser-research-acceptance (2026-10-06,
`logs/browser-research/step15+step16`): nach dem Out-of-Band-Tod des Session-Browsers
blieb die State-Datei bei `state=ready / starts=1 / last_launch_attempt` gefroren
stehen, und der nächste Transport-Aufruf startete den Browser per Call neu
(`lifecycle.launched: true`, neue targetId), OHNE dass das Produkt ihn beobachtete —
der Start-Zähler verpasste einen echten Launch.

Run-Datum: 2026-10-06. Repo-Stand bei Reproduktion: `9b4e77b`.

## Skeleton — Beweise werden laufend angehängt

(gefüllt während des Runs, Methode gemäß mission-AGENTS.md)
