/**
 * Gemeinsame Tokens der Werkbank — eine Wahrheit fuer alle Fixtures.
 *
 * Angelegt wegen der Befunde UI-BEFUNDE-2026-09-18.md:
 *   A2/A3  Kontrast: helles „faint" war #8C8C8C (3,22:1) → jetzt #6E6E6E (≈4,9:1);
 *          Grenzlinien hell waren #DEDEDE (1,29:1) → jetzt #8F8F8F (3,05:1),
 *          dunkel waren #3A3A3A (1,66:1) → jetzt #6A6A6A (≈3,0:1).
 *          `hair` bleibt die ZIERLINIE (darf schwach sein, traegt keine Bedeutung).
 *   B4     Radien: eine Tabelle statt drei Systemen.
 *   B3     Schriftuntergrenze 11 px (TEXT.meta).
 *   B1     Nur Schwarz/Weiss/Grau — diese Datei ist die erlaubte Liste.
 */
export const LIGHT = {
  bg: '#FAFAFA', surface: '#FFFFFF', ink: '#171717', sub: '#595959',
  faint: '#6E6E6E',            // war #8C8C8C → Kontrast 3,22:1 war zu wenig
  line: '#8F8F8F',             // Grenzlinie, traegt Bedeutung → 3,05:1
  hair: '#DEDEDE',             // Zierlinie, traegt keine Bedeutung
  soft: '#F2F2F0', veil: 'rgba(255,255,255,0.40)'
}
export const DARK = {
  bg: '#111111', surface: '#191919', ink: '#F5F5F5', sub: '#B7B7B7',
  faint: '#8A8A8A',
  line: '#6A6A6A',             // war #3A3A3A → 1,66:1 war zu wenig
  hair: '#3A3A3A',
  soft: '#202020', veil: 'rgba(10,10,10,0.42)'
}
/** Radien: micro (Kleinststeuerung) · row (Zeile) · module (Karte) · dialog · bar (Composer) · pill */
export const RADIUS = { micro: 4, row: 8, module: 12, dialog: 16, bar: 24, pill: 999 }
/** Schrift: Untergrenze 11 px fuer alles Bedienbare und alle Metadaten */
export const TEXT = { meta: 11, body: 13, title: 15 }
export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
export const SANS = 'Geist, system-ui, sans-serif'
export const SPRING = { type: 'spring', stiffness: 420, damping: 34, mass: 0.7 }
