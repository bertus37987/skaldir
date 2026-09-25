# Skaldir ausprobieren

Das Tool ist als echtes CLI installiert: `~/.local/bin/skaldir` (uv tool,
`skaldir-agent v1.5.0`). Kein `sudo`, alles im User-Level.

```bash
skaldir --version          # skaldir 1.5.0
which skaldir              # /home/willy/.local/bin/skaldir
```

## 1. Am schnellsten sehen, was es kann

```bash
skaldir --help                 # alle Kommandogruppen
skaldir --all-commands         # jede Unterfunktion mit Kurzbeschreibung
```

## 2. Die bestätigten Kernstücke live anfassen

**Vollbild-TUI** (Chat links, Tools oben rechts, Goal unten rechts):

```bash
skaldir tui preview --width 100 --height 30
```

Einen **echten Lauf** damit rendern — hier sieht man Toolzeilen mit Dauer,
Vorschau und Fehler statt eines Demobeispiels:

```bash
skaldir tui preview --transcript <pfad>/run.jsonl
```

**Desktopkontrolle** (Lease, Fence, Eckindikator):

```bash
skaldir desk acquire --run demo --task "test"
skaldir desk status
skaldir desk indicator-show --hold 5     # echtes X11-Eckfenster, 5 s sichtbar
skaldir desk stop --run demo             # volle Notstopp-Kette
skaldir desk release --run demo --backend x11 --session s1
```

**Notstopp** (was der physische Doppel-Escape auslöst):

```bash
skaldir stop report                      # ehrliche Verfügbarkeitsmessung
skaldir stop detect --gap-ms 120 --gap-ms 80   # Detektor trocken üben
skaldir stop report --proof <proof.json> # gemessenen Beweis eintragen
```

**Freigabemodi** (Tab im leeren Eingabefeld wechselt den Modus):

```bash
skaldir tui approve --help
```

**Zeitplaner** (Definition ≠ Aktivierung):

```bash
skaldir schedule define --workflow report --version 1.0 --at 02:30 --id test1
skaldir schedule due                     # leer — noch nicht aktiviert
skaldir schedule activate --id test1
skaldir schedule due                     # feuert jetzt
skaldir schedule pause --id test1
```

**Datenquellen-Ehrlichkeit** (DOCKER-05):

```bash
skaldir sources add --name stooq --kind free --provider stooq.com \
                    --terms https://stooq.com/legal/ --tool stock
skaldir sources report --tool stock      # "availability is unknown" bis gemessen
skaldir sources add --name paid --kind paid --tool stock   # Exit 3, erzwungene Freigabe
```

**Voice-Rebrief** (VOICE-03):

```bash
skaldir voice rebrief --finding "x" --evidence "y" --criterion "z" --next-step "observe"
skaldir voice rebrief --finding "x" --evidence "y" --criterion "z" --next-step "continue"
#                                          ^ Exit 3: "not a step"
```

## 3. Was `selftest` auf einer frischen Installation sagt

```bash
skaldir selftest --json        # Exit 2 — und das ist korrekt
```

Exit 2 bedeutet laut eigenem Vertrag „a layer is honestly unreachable". Auf
einem neuen Laufzeitverzeichnis meldet die Ebene `COMPOSE`:

```
x  COMPOSE - no tools db yet (read path wired)
```

Das ist kein Defekt: es gibt noch keine gebauten Tools, aus denen COMPOSE etwas
zusammensetzen könnte. Sobald Tools registriert sind, schaltet diese Ebene um.

## 4. Browser-Werkzeug (gepinnt, projektlokal)

`agent-browser 0.38.1` (Apache-2.0) ist **exakt gepinnt und projektlokal**
unter `node_modules/.bin` installiert — bewusst nicht global.

Das heißt: es ist sichtbar, wenn der Befehl aus dem Repo läuft, und nicht aus
der uv-tool-Umgebung heraus. Beides ist korrekt gemeldet:

```bash
# aus dem Repo heraus:
cd ~/Agentic_Projects/Skaldir_Agent && .venv/bin/skaldir browser probe
#   available: true, version: agent-browser 0.38.1

# als uv tool:
skaldir browser probe
#   available: false, mit dem exakten Installbefehl als Begruendung
```

Die Ablehnung sagt genau das, statt etwas zu tun, das nicht geprüft ist.

```bash
.venv/bin/skaldir browser read --url https://example.com
.venv/bin/skaldir browser tabs
```

Wer es auch ausserhalb des Repos nutzen will, setzt es auf den PATH:

```bash
export PATH="$HOME/Agentic_Projects/Skaldir_Agent/node_modules/.bin:$PATH"
```

## 5. Rückweg / deinstallieren

Der vorherige Launcher ist gesichert:

```bash
cat ~/.local/bin/skaldir.gods-voice.backup
#   exec /home/willy/projects/gods-voice/.venv/bin/python -m skaldir "$@"

# zurückschalten:
cp ~/.local/bin/skaldir.gods-voice.backup ~/.local/bin/skaldir

# oder ganz entfernen:
scripts/install-cli.sh uninstall
```

## Hinweise

- `python -m skaldir` ist die Modulschreibweise des CLI. `python -m skaldir.cli`
  ist **kein** Einstiegspunkt und gibt nichts aus.
- `godsvoice` bleibt als veralteter Alias für denselben Einstiegspunkt erhalten.
- Nicht interaktive Aufrufe (JSON, Pipe) bleiben nutzbar — nur `skaldir` ohne
  Unterbefehl öffnet die Vollbild-TUI.
