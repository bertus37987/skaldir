# Prompt für Transfer und Implementierung

Arbeite in `/home/willy/Agentic_Projects/Skaldir_Agent`.

Lies zuerst `AGENTS.md`, `TRANSFER-UND-IMPLEMENTIERUNG.md` und `docs/masterplan/00-START-HIER.md`. Führe anschließend den Auftrag aus: Übernimm die sinnvollen aktiven Teile von `/home/willy/projects/gods-voice` selektiv in dieses neue Projekt, richte es sauber ein und implementiere den vollständigen aktuellen Masterplan auf der vorhandenen Architektur.

Erstelle anhand des Transferauftrags ein neues **privates** GitHub-Repository `Skylia-Agent` mit der vorhandenen GitHub CLI. Prüfe Account, Namenskollision, Secret-freien Commitbestand und tatsächliche private Sichtbarkeit. Initialer Transfer-Commit und Push sind beauftragt. Den alten Quellordner weder löschen noch verändern; kein blindes Kopieren von Laufzeitdaten, Credentials, Caches oder Altlasten. Erhalte erforderliche Tests, Lizenzen und tatsächlich verwendete Ressourcen.

Protokolliere Übernahmeentscheidungen, Codeänderungen und echte Testergebnisse unter `docs/audit/`, ausführliche redigierte Logs unter `logs/`. Vergleiche alle 134 Anforderungen mit dem tatsächlichen Code, erhalte funktionierende Lösungen und schließe die Integrationslücken. Ein Audit allein reicht nicht: Nach dem Transfer weiter implementieren und testen.

Die UI ist entschieden: `skaldir` öffnet eine Terminal-Vollbild-TUI; großer Chat links, Tools oben rechts, Goal unten rechts. Ergebnisvorschau pro Call mit aufklappbaren Details. Tab im Leerfeld wechselt Approvals, bei aktiver Completion bedient es Vorschläge. Dezente animierte Figur, gelegentlicher Humor, überwiegend monochrom und Fehler rot. Kleine Desktop-Statusleiste in einer Ecke; globales Doppel-Escape funktioniert auch außerhalb der TUI.

Arbeite nach den Etappen und Abnahmekriterien im Plan. Nach einzelnen Änderungen gezielte Tests, nach zusammenhängenden Änderungen Integration und vor Abschluss die vorgeschriebenen Projektchecks. Keine wiederholt gestarteten identischen Fullsuite-Läufe. Belege echte Nutzerabläufe, statt vorhandene Klassen oder registrierte Befehle als fertig zu zählen.

Frage nur bei tatsächlich fehlenden entscheidenden Angaben oder externen Voraussetzungen; setze unabhängige Arbeit fort. Erfinde keine Testerfolge. Liefere am Ende Repository-Link, Transfermanifest, aktives Setup, umgesetzte Anforderungen, Ergebnisse, verbleibende Grenzen und HANDOFF.md.
