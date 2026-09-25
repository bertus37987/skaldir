# Linux Computer Use, überprüfte Aktionen und Recovery

Anforderungen: `ACT-01` bis `ACT-04`; Grundlage für GUI-Skills.

## 1. Integration in die vorhandene Runtime

Erweitere ACT oder das vorhandene Äquivalent. API-, CLI-, Browser- und Desktop-Aktionen sollen dieselben Verträge für Goal-Zuordnung, Berechtigung, Budget, Ergebnis, Evidence und Recovery verwenden.

Die Auswahl der Oberfläche ist aufgabenabhängig. Eine vorhandene stabile API oder CLI ist für strukturierte Änderungen oft geeigneter als Bildschirmklicks. Für visuelle Bearbeitung und Layoutprüfung bleibt die tatsächliche Darstellung relevant.

Vorgeschlagene Auswahlpräferenz:

```text
API / CLI
→ Browser-DOM oder Browser-Accessibility
→ Desktop-Accessibility
→ visuelle Lokalisierung und Eingabe
```

Das ist kein Zwang, alle Ebenen nacheinander zu versuchen. Bereits bekannte Eignung und Fehlerhistorie berücksichtigen.

## 2. Linux-Fähigkeiten tatsächlich erkennen

Ermittle zur Laufzeit die verfügbaren Fähigkeiten, ohne Secret-Inhalte zu erfassen:

- X11, Wayland, Headless oder andere tatsächliche Sitzung.
- Desktop-/Compositor-Umgebung, soweit relevant und feststellbar.
- Verfügbarer Screenshot-/Stream-Pfad.
- Verfügbarer Eingabepfad für Tastatur und Pointer.
- Accessibility-Verfügbarkeit und brauchbare App-Abdeckung.
- Bildschirmgeometrie, Skalierung und mehrere Monitore.
- Aktives Fenster und Möglichkeit, Fokus verlässlich zu beobachten.
- Einschränkungen einer Container-/Remote-Sitzung.

Keine Unterstützung behaupten, weil eine Bibliothek installiert ist. Aufnahme, Eingabe und Ergebnisprüfung getrennt testen. Eine nicht verfügbare Fähigkeit als solche zurückgeben.

### Wayland

Als Integrationskandidaten XDG ScreenCast/RemoteDesktop, PipeWire und EIS/libei prüfen. Das konkrete Desktop-Backend muss die benötigten Funktionen anbieten. Bestehende OS-Freigaben und Sitzungsverträge beachten; fehlende Unterstützung nicht durch pauschale Rechteausweitung ersetzen.

### X11

Vorhandene, funktionierende X11-Adapter beibehalten. Fokus, Koordinatenraum und aktuelle Bildschirmgeometrie explizit behandeln. Eine funktionierende X11-Implementierung ist kein Wayland-Nachweis.

### Accessibility

AT-SPI kann Rollen, Namen, Zustände, Inhalte und Aktionen liefern. Die Abdeckung hängt von App und Toolkit ab. Brauchbarkeit pro Ziel prüfen. Ein leerer oder veralteter Baum darf nicht als vollständige Beschreibung gelten.

## 3. Beobachtungsvertrag

Eine Beobachtung sollte enthalten:

```text
observation_id
session_id
captured_at
backend
active_window_or_surface
geometry_and_scale
screenshot_ref / accessibility_snapshot_ref
state_fingerprint
capability_snapshot
```

Screenshot und Accessibility-Daten können leicht unterschiedliche Aufnahmezeitpunkte haben. Keine perfekte Synchronität vortäuschen. Für zeitkritische Aktionen neue Beobachtung anfordern.

Visuelle Aktionen binden sich an Beobachtung und Geometrie. Nach Fensterwechsel, Scrollen, Dialogöffnung oder veränderter Skalierung sind alte Koordinaten möglicherweise ungültig.

Ein Fingerprint ist ein Hinweis auf Zustandsänderung, kein Beweis vollständiger Gleichheit. Dynamische Inhalte und verdeckte Zustände können zusätzliche Prüfung erfordern.

## 4. Aktionsvertrag

Vorgeschlagene Felder:

```text
action_id, run_id, goal_revision
observation_id
target_session, target_window_or_surface
action_type, parameters
preconditions
expected_postconditions
side_effect_class
idempotency_key_if_supported
lease_or_fencing_token
timeout
```

Vor Eingabe prüfen:

1. Stimmt die Sitzung und besitzt der ausführende Worker die aktuelle Steuerungsberechtigung?
2. Ist die zugrunde liegende Beobachtung noch brauchbar?
3. Ist das erwartete Fenster/Feld aktiv?
4. Sind Voraussetzungen und Berechtigung erfüllt?

Nach Eingabe Resultat beobachten. „Klick gesendet“ ist ein Ausführungsereignis, kein Nachweis des erwarteten fachlichen Ergebnisses.

## 5. Kurze Aktionssequenzen statt blindem Makro

Sequenzen können Modellaufrufe sparen, wenn ihre Zwischenzustände vorhersehbar sind. Nach einem unerwarteten Dialog oder fehlender Postcondition abbrechen und neu beobachten.

Geeignetes Beispiel:

```text
In geprüftem Suchfeld Text eingeben
→ Suche auslösen
→ auf beobachtbares Ergebnis warten
→ neuen Zustand zurückgeben
```

Ungeeignetes Beispiel:

```text
20 Klicks mit festen Wartezeiten aus einer alten Aufnahme wiederholen
```

Verwende vorhandene Zustands-/Event-Waits. Feste Sleeps nur begrenzt und mit anschließendem Zustandscheck. Bei ausbleibendem Fortschritt Fehlerkategorie und Belege zurückgeben.

## 6. Exklusive Steuerung einer Desktop-Sitzung

Eine konkrete interaktive Sitzung hat einen ausführenden Besitzer. Andere Worker können unabhängig recherchieren, Informationen lesen oder isoliert rechnen.

- Besitz zeitlich begrenzen und bei Recovery erneuern.
- Veraltete Worker durch Fencing an weiteren Aktionen hindern.
- Besitzverlust bricht geplante Aktionsserien ab.
- Nutzerinteraktion während eines Runs erkennen, soweit Backend das ermöglicht, und Fokus/Zustand erneut prüfen.
- Kein automatisches Übersteuern aktiver Nutzerarbeit auf Basis alter Beobachtungen.

Nutze vorhandene Lease-/Scheduler-Mechanismen. Kein eigener Lock-Dienst, falls die Runtime bereits einen korrekten Vertrag besitzt.

## 7. Ergebnisprüfung über JEV

Prüfungen an das fachliche Ziel binden:

| Aktion | Unzureichender Beleg | Geeignete Postcondition |
|---|---|---|
| Datei exportieren | Exportdialog geschlossen | Neue Datei am Ziel, lesbar, Inhalt und Format geprüft |
| Tabelle ändern | Zelle angeklickt | Erwarteter Zellwert/Formel im richtigen Dokument |
| Einstellung ändern | Checkbox visuell betätigt | Einstellung erneut gelesen oder Verhalten bestätigt |
| Webformular absenden | Button geklickt | Bestätigung oder maßgeblicher Zielzustand nachgewiesen |
| Layout korrigieren | Datei gespeichert | Aktuell gerenderte Ansicht geprüft |

Evidenz enthält Artefakt-/Zustandsversion, Zeit, Prüfverfahren und zugehöriges Goal-Kriterium. Nach einer weiteren Änderung muss passende Evidence erneuert werden.

Die Prüfstrategie darf sich nach der Oberfläche richten: API-Zustand, Dateiinhalte, DOM, Accessibility oder Screenshot. Ein zweites LLM ist nicht für jede Prüfung erforderlich. Deterministische Prüfungen bevorzugen, wo sie den Sachverhalt zuverlässig erfassen.

## 8. Recovery bei unbekanntem Ergebnis

Aktionszustände unterscheiden:

```text
planned → dispatched → confirmed_success
                    → confirmed_failure
                    → outcome_unknown
```

Nach Timeout oder Prozessverlust kann eine externe Aktion bereits erfolgt sein. Deshalb:

1. Persistierten Aktionsdatensatz und letzte Evidence laden.
2. Kontrolle über die Sitzung sicher wiederherstellen.
3. Den tatsächlichen Zielzustand lesen.
4. Bereits erfolgte Wirkung bestätigen, wenn belegbar.
5. Nur bei ausreichend geklärtem Zustand erneut ausführen.
6. Bei nicht auflösbarer Unklarheit genau diese Unklarheit melden; keine doppelte Mutation riskieren.

Lokales Undo, erneuter Versuch und externer Zustandsabgleich sind unterschiedliche Operationen. Die UI darf kein Undo versprechen, wenn nur eine erneute Prüfung möglich ist.

## 9. Geeignete Linux-Abnahmeszenarien

- Eine lokale Datei in einer tatsächlich unterstützten App öffnen und eine überprüfbare Änderung speichern.
- Eine kleine Tabelle ändern und exportieren; Werte sowie Exportdatei prüfen.
- Browserarbeit mit Download und anschließendem Dateischritt.
- Fokusverlust oder unerwarteten Dialog injizieren; falsche Eingabe verhindern.
- Während einer Aufgabe Geometrie oder sichtbaren Zustand ändern; neu beobachten.
- Einen Abbruch nach Dispatch simulieren; bei Resume keine doppelte Wirkung.
- Einen alten Worker nach Lease-Wechsel weiterlaufen lassen; Aktionen werden abgewiesen.

Die Auswahl an die installierten Apps anpassen. Mindestens ein echtes Desktop-Szenario auf einem unterstützten Linux-Backend ausführen. Reine Mock-Tests gelten nur als Vertragsprüfung. Nicht verfügbare Backends ausdrücklich als ungetestet dokumentieren.

## 10. Nicht automatisch hinzufügen

- Neue GUI-Modelle, wenn ein vorhandenes Modell die Aufgabe ausreichend gut erfüllt.
- Eine zweite Desktop-App nur zur Integration eines Forschungssystems.
- Unbegrenzte Video- oder Screenshot-Historie im Modellkontext.
- Globale privilegierte Eingabeschnittstellen als bequemen Ersatz für passende Backend-Verträge.
- Automatisches Training eines eigenen GUI-Modells ohne belegte Daten- und Ressourcenbasis.

Agent S und UI-TARS sind Referenzen für Architektur und Vergleich. Ihre Scores oder Fähigkeiten nicht ohne Integrationstest auf Skaldir übertragen.
