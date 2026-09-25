/**
 * App-Overlays — Hinweise, Menüs und Statusanzeigen der Skaldir-App.
 * Hell/Dunkel je Variante im Kopf. Nur Schwarz/Weiss/Grau; Fehler werden über
 * ✕ und Rahmenstärke (1.5px) deutlich, nie über Farbe. Symbole nur @tabler/icons-react.
 *
 * Werkbank-Regeln (UI-BEFUNDE-2026-09-18): Palette, Schriften, Radien und Feder kommen
 * ausschließlich aus `./_tokens` (A2/A3 Kontrast · B1 nur Grau · B3 Schriftuntergrenze 11 ·
 * B4 eine Radius-Tabelle); `useBarrierefrei()` im Rahmen deckt reduzierte Bewegung (A1) und
 * Tastaturbedienung (A5) ab; jeder Variantenrahmen trägt `data-stage` (C2).
 *
 * Extraktion aus der Original-App (nur gelesen, app-src):
 *  app/command-palette/index.tsx:1531-1560 HUD: rounded-xl, border, shadow,
 *    w=min(34rem,100vw-2rem), Slide-in von oben (slide-in-from-top-2, zoom-95)
 *  app/floating-hud.ts:11-45 HUD_POSITION top-3 zentriert; HUD_TEXT text-xs;
 *    HUD_HEADING 0.64rem uppercase tracking-[0.16em]; HUD_ITEM px-2 py-1
 *  app/command-palette/status-row.tsx StatusRow px-2 py-6 text-xs zentriert
 *  components/notifications.tsx:97-141,144-164,197-276 Toasts: top-center
 *    unter Titlebar, w=min(40rem), neuestes oben, "+N mehr"+Alle löschen;
 *    bottom-right w=min(24rem); Alert-Karte border+bg-popover/95+backdrop-blur
 *  components/ui/alert.tsx:8-36 Alert: rounded-lg border px-4 py-3 text-sm
 *  components/gateway-connecting-overlay.tsx:14-22,148-169 CONNECTINGOverlay:
 *    DecodeText cursor, prefix=4 (CONn bleibt), Abblende 360+300+520ms
 *  components/chat/status-row.tsx:39-96 Statuszeilen: min-h-6, führendes
 *    Symbol size-3.5, Trailing erscheint erst bei Hover
 *  components/billing-banner.tsx:53-88 persistente Statuszeile: CreditCard,
 *    truncate 0.73rem, Trailing sichtbar
 *  app/updates-overlay.tsx:419-465 ApplyingView: Progress indeterminate lg,
 *    Log-Fenster mono 11px rounded-md border, letzte 4 Zeilen
 *  app/updates-overlay.tsx:312-345 Update-Available: Bulletpunkt 4px round,
 *    Liste text-xs, primary lg Knopf + text-Variante "Später", +N weitere
 *  components/ui/tooltip.tsx:99,133 Tip: sideOffset 6, bg-foreground
 *    px-1.5 py-1 text-[11px] bold, Arial; eingangs-Verzögerung 150ms
 *  components/ui/dropdown-menu.tsx:96-135 Menü: rounded-lg border p-1
 *    shadow-md, Item px-2 py-1 text-xs rounded-md, Checkbox-Item mit Häkchen
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useRef, useState } from 'react'
import { IconAlertCircle, IconArrowBackUp, IconCheck, IconChevronDown, IconCircleCheck, IconCircleX, IconCopy, IconCornerDownLeft, IconCreditCard, IconInfoCircle, IconKeyboard, IconLoader2, IconMessageCircle, IconMoon, IconRefresh, IconSearch, IconSettings, IconSun, IconTerminal, IconWifi, IconWifiOff, IconX } from '@tabler/icons-react'

import { useBarrierefrei } from './_barrierefrei'
// Eine Palette für die ganze Werkbank (UI-BEFUNDE-2026-09-18: A2/A3 Kontrast, B1 nur Grau,
// B3 Schriftuntergrenze, B4 eine Radius-Tabelle). Lokale Kopien sind damit weg.
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
const POP = '0 1px 2px rgba(0,0,0,0.08), 0 10px 30px rgba(0,0,0,0.14)'

/* ------------------------------------------------------------ Bausteine */

function TitleLine({ P, name }) {
  return <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
}

function Head({ P, note, dark, toggle }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1 }}>{note}</span>
      {toggle && (
        <span data-themetoggle style={{ display: 'inline-flex', gap: 6 }}>
          {[['hell', false], ['dunkel', true]].map(([label, v]) => (
            <motion.span key={label} whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => toggle(v)}
              style={{
                fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px', cursor: 'pointer',
                border: `1px solid ${dark === v ? P.ink : P.line}`,
                background: dark === v ? P.ink : P.surface, color: dark === v ? P.bg : P.ink
              }}>{label}</motion.span>
          ))}
        </span>
      )}
    </div>
  )
}

function Case({ name, note, children }) {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage data-theme={dark ? 'dunkel' : 'hell'} style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <TitleLine P={P} name={name} />
      <div style={{ margin: '3px 0 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1 }}>{note}</span>
        <Head P={P} note="" dark={dark} toggle={setDark} />
      </div>
      {children(P)}
    </div>
  )
}

const Kbd = ({ P, children }) => (
  <span style={{
    fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1, padding: '3px 4px', flex: '0 0 auto',
    border: `1px solid ${P.line}`, borderBottomWidth: 2, borderRadius: RADIUS.micro,
    background: P.surface, color: P.faint
  }}>{children}</span>
)

const Ico = ({ P, children, size = 14 }) => (
  <span style={{ display: 'inline-flex', flex: '0 0 auto', color: P.ink }}>
    {React.createElement(children.type, { size, strokeWidth: 1.8 })}
  </span>
)
/* ---------------------------------------------------- 01 · Befehlspalette */

const PALETTE_ROWS = (P) => [
  { Icon: IconMessageCircle, label: 'Neue Sitzung', kbd: ['⌘', 'N'] },
  { Icon: IconSettings, label: 'Einstellungen', kbd: ['⌘', ','] },
  { Icon: IconSun, label: 'Hell umschalten', kbd: ['⌃', '⇧', 'H'], note: 'ein' },
  { Icon: IconTerminal, label: 'Gateway neu starten', kbd: ['⌘', 'R'] },
  { Icon: IconKeyboard, label: 'Tastenkürzel', kbd: [], note: 'Seite' }
]

const Palette = ({ P }) => (
  <motion.div data-commandpalette
    initial={{ opacity: 0, y: -8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={SPRING}
    style={{
      width: '100%', maxWidth: 544, background: P.surface, borderRadius: RADIUS.module,
      border: `1px solid ${P.hair}`, boxShadow: POP, overflow: 'hidden', margin: '0 auto'
    }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderBottom: `1px solid ${P.hair}`, position: 'relative' }}>
      <Ico P={P}><IconSearch /></Ico>
      <input readOnly placeholder="Befehl suchen…" style={{
        flex: 1, border: 'none', outline: 'none', background: 'transparent',
        fontFamily: SANS, fontSize: 12, color: P.ink, minWidth: 0
      }} />
      <Kbd P={P}>esc</Kbd>
    </div>
    <div style={{ padding: 6 }}>
      <div style={{
        fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600, letterSpacing: '0.16em',
        textTransform: 'uppercase', color: P.ink, padding: '10px 10px 5px'
      }}>Sitzung</div>
      {PALETTE_ROWS(P).map((r, i) => (
        <motion.div key={r.label} data-palette-row={r.label} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          transition={{ ...SPRING, delay: 0.03 * i }}
          whileHover={{ backgroundColor: P.hair }}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px',
            borderRadius: RADIUS.row, cursor: 'pointer', background: i === 0 ? P.hair : 'transparent'
          }}>
          <Ico P={P}><r.Icon /></Ico>
          <span style={{ fontFamily: SANS, fontSize: 12, color: P.ink, whiteSpace: 'nowrap' }}>{r.label}</span>
          {r.note && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginLeft: -4, textDecoration: 'underline', textUnderlineOffset: 2 }}>{r.note}</span>}
          <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 2 }}>
            {r.kbd.length ? r.kbd.map(k => <Kbd key={k} P={P}>{k}</Kbd>) : null}
            {i === 0 && <Kbd P={P}><IconCornerDownLeft size={9} /></Kbd>}
          </span>
        </motion.div>
      ))}
      {/* Statuszeile der Palette (status-row.tsx): leerer Suchraum, zentriert */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '18px 8px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        <IconLoader2 size={11} stroke={2} className="spin-slow" /> Ergebnisliste lädt …
      </div>
    </div>
  </motion.div>
)

const V01 = () => <Case name="01 · Befehlspalette (Suche mit Tastenkürzel)" note="⌘K — HUD top-3 zentriert, w min(34rem), rows px-2 py-1, Heading 0.64rem uppercase 0.16em">
  {(P) => <Palette P={P} />}
</Case>

/* ---------------------------------------------------- 02 · Aufklappmenü */

const MENU_ITEMS = (P) => [
  { Icon: IconTerminal, label: 'Gateway öffnen', kbd: ['⌘', 'G'] },
  { Icon: IconCopy, label: 'Protokoll kopieren' },
  { sep: true },
  { Icon: IconArrowBackUp, label: 'Zurücksetzen', kbd: ['⌃', 'Z'] },
  { Icon: IconX, label: 'Schliessen', kbd: ['esc'] }
]

const MenuToggle = () => {
  const [open, setOpen] = useState(true)
  return (
    <Case name="02 · Aufklappmenü" note="rounded-lg p-1 border shadow · Item px-2 py-1 text-xs · AnimatePresence-Feder">
      {(P) => (
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '10px 0 30px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 6 }}>
            <motion.span data-menutrigger whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }} transition={SPRING}
              onClick={() => setOpen(o => !o)} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                fontFamily: SANS, fontSize: 12, color: P.ink, padding: '5px 10px',
                border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface
              }}>
              Fenster Aktionen <motion.span animate={{ rotate: open ? 180 : 0 }} transition={SPRING}><IconChevronDown size={12} stroke={1.8} /></motion.span>
            </motion.span>
            <AnimatePresence>
              {open && (
                <motion.div data-menucontext data-dropdownmenu
                  initial={{ opacity: 0, y: -6, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -6, scale: 0.96 }} transition={SPRING}
                  style={{
                    width: 240, background: P.surface, borderRadius: RADIUS.row, border: `1px solid ${P.hair}`,
                    padding: 4, boxShadow: POP, transformOrigin: 'top left'
                  }}>
                  <div style={{ padding: '6px 10px 4px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, letterSpacing: '0.1em', textTransform: 'uppercase' }}>Fenster A</div>
                  {MENU_ITEMS(P).map((m, i) => m.sep
                    ? <div key={`sep${i}`} data-menusep style={{ height: 1, background: P.hair, margin: '4px 6px' }} />
                    : (
                      <motion.div key={m.label} data-dropdownitem={m.label} whileHover={{ backgroundColor: P.hair }} style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderRadius: RADIUS.row, cursor: 'pointer',
                        fontFamily: SANS, fontSize: 12, color: m.danger ? P.ink : P.ink
                      }}>
                        <Ico P={P}><m.Icon /></Ico>
                        <span style={{ whiteSpace: 'nowrap' }}>{m.label}</span>
                        {m.kbd && <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 2 }}>{m.kbd.map(k => <Kbd key={k} P={P}>{k}</Kbd>)}</span>}
                      </motion.div>
                    ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}
    </Case>
  )
}

/* ---------------------------------------------------- 03 · Kontextmenü */

const ContextMenu = ({ P }) => (
  <div data-contextmenu-demo style={{ position: 'relative' }}>
    <div style={{
      width: '100%', maxWidth: 544, margin: '0 auto', height: 110, display: 'grid',
      placeItems: 'center', border: `1px dashed ${P.line}`, borderRadius: RADIUS.row
    }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Rechtsklick auf einen Knoten → Aktionen</span>
    </div>
    <motion.div data-contextmenu
      initial={{ opacity: 0, scale: 0.94, y: -4 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
      style={{
        position: 'absolute', left: '38%', top: 44, width: 216, background: P.surface,
        borderRadius: RADIUS.row, border: `1px solid ${P.hair}`, padding: 4, boxShadow: POP, transformOrigin: 'top left'
      }}>
      <div style={{ padding: '6px 10px 4px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, letterSpacing: '0.1em', textTransform: 'uppercase' }}>Knoten: skill · weber</div>
      <motion.div data-contextitem="bearbeiten" whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderRadius: RADIUS.row, cursor: 'pointer', fontFamily: SANS, fontSize: 12, color: P.ink }}>
        <Ico P={P}><IconSettings /></Ico>Inhalt bearbeiten
      </motion.div>
      <motion.div data-contextitem="archivieren" whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderRadius: RADIUS.row, cursor: 'pointer', fontFamily: SANS, fontSize: 12, color: P.ink }}>
        <Ico P={P}><IconArrowBackUp /></Ico>Archivieren
      </motion.div>
      <div style={{ height: 1, background: P.hair, margin: '4px 6px' }} />
      <motion.div data-contextitem="loeschen" whileHover={{ backgroundColor: P.hair }} style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderRadius: RADIUS.row, cursor: 'pointer',
        fontFamily: SANS, fontSize: 12, color: P.ink, borderTop: `1.5px solid ${P.line}`, marginTop: 2
      }}>
        <Ico P={P}><IconX /></Ico>Löschen <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>✕</span>
      </motion.div>
    </motion.div>
  </div>
)

const V03 = () => <Case name="03 · Kontextmenü" note="Rechtsklick (starmap/node-context-menu.tsx) — edit, archive, löschen">
  {(P) => <ContextMenu P={P} />}
</Case>

/* ---------------------------------------------------- 04 · Kurzhinweis */

const Tooltip = ({ P }) => (
  <div style={{ display: 'flex', gap: 56, justifyContent: 'center', padding: '22px 0 40px', flexWrap: 'wrap' }}>
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
      <motion.span data-tip
        initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{
          fontFamily: 'Arial, sans-serif', fontSize: TEXT.meta, fontWeight: 700,
          padding: '4px 6px', borderRadius: RADIUS.micro, background: P.ink, color: P.bg, whiteSpace: 'nowrap'
        }}>Sitzung teilen<span style={{ display: 'block', width: 6, height: 6, background: P.ink, transform: 'rotate(45deg)', margin: '3px auto -8px' }} /></motion.span>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface }}><IconCopy size={14} stroke={1.8} color={P.ink} /></span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>delay 150 ms · sideOffset 6</span>
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface }}><IconRefresh size={14} stroke={1.8} color={P.ink} /></span>
      <motion.span data-tip-below
        initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{
          fontFamily: 'Arial, sans-serif', fontSize: TEXT.meta, fontWeight: 700,
          padding: '4px 6px', borderRadius: RADIUS.micro, background: P.ink, color: P.bg
        }}>Neu verbinden<span style={{ display: 'block', width: 6, height: 6, background: P.ink, transform: 'rotate(45deg)', margin: '-8px auto 3px' }} /></motion.span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>bg-foreground px-1.5 py-1 · 11px bold</span>
    </div>
  </div>
)
/* ---------------------------------------------------- 05 · Toast-Meldungen */

const TONE = (P) => ({
  info:    { Icon: IconInfoCircle },
  warning: { Icon: IconAlertCircle },
  error:   { Icon: IconCircleX },
  success: { Icon: IconCircleCheck }
})

const Toast = ({ P, kind, title, message, actions, i = 0 }) => {
  const { Icon } = TONE(P)[kind]
  return (
    <motion.div data-toast={kind}
      initial={{ opacity: 0, y: -10, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={SPRING}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 10px 8px 14px',
        background: P.surface, border: `1px solid ${kind === 'error' ? P.ink : P.hair}`,
        borderWidth: kind === 'error' ? 1.5 : 1, borderRadius: RADIUS.row, boxShadow: POP,
        width: '100%', maxWidth: 640
      }}>
      <span style={{ marginTop: 2, display: 'inline-flex', color: P.ink, flex: '0 0 auto' }}><Icon size={15} stroke={1.8} /></span>
      <div style={{ minWidth: 0, flex: 1 }}>
        {title && <div style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>{title}</div>}
        <div style={{ fontFamily: SANS, fontSize: 11.5, color: P.sub, lineHeight: 1.45 }}>{message}</div>
        {actions && (
          <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
            {actions.map((a, j) => (
              <motion.span key={a.label} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
                style={{
                  fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 8px', cursor: 'pointer',
                  border: `1px solid ${j === 0 ? P.ink : P.line}`,
                  background: j === 0 ? P.ink : 'transparent', color: j === 0 ? P.bg : P.ink
                }}>{a.label}</motion.span>
            ))}
          </div>
        )}
      </div>
      <motion.span data-toastdismiss whileHover={{ scale: 1.12 }} transition={SPRING}
        style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, cursor: 'pointer', flex: '0 0 auto', lineHeight: 1.3 }}>✕</motion.span>
    </motion.div>
  )
}

const ExpandBar = ({ P, hidden }) => (
  <motion.div data-toastexpandbar
    initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...SPRING, delay: 0.1 }}
    style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 12px',
      maxHeight: 26, background: P.surface, border: `1px solid ${P.hair}`, borderRadius: RADIUS.row, boxShadow: POP
    }}>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>mehr anzeigen · {hidden} weitere</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>alle löschen</span>
  </motion.div>
)

const ToastStack = ({ P }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center', width: '100%' }}>
    <Toast P={P} kind="error" title="Werkzeug abgelehnt: filesystem.write"
      message="Pfad liegt außerhalb der erlaubten Wurzel. Es wurde nichts verändert."
      actions={[{ label: 'mit Freigabe erneut' }, { label: 'Details' }]} />
    <Toast P={P} kind="info" title="Gateway-Verbindung verloren"
      message="Neue Anfragen warten, bis der lokale Dienst wieder antwortet. Der Chat-Verlauf bleibt." />
    <Toast P={P} kind="success" message="Update installiert — neu starten anwenden." />
    <ExpandBar P={P} hidden={2} />
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 6 }}>
      {'top-center · w min(40rem) unter der Titelzeile · neuestes oben · mehr-Lauf · alle loeschen · bottom-right w min(24rem)'}
    </span>
  </div>
)

const V05 = () => <Case name="05 · Toast-Meldungen (Benachrichtigungen)" note="Stack-Karte border+blur, Titel max 4.5em dann Scroll, Detail-Aufklapper, Haptik je Art">
  {(P) => <ToastStack P={P} />}
</Case>

/* ---------------------------------------------------- 06 · Fehlerbanner */

const ErrorBanner = ({ P }) => (
  <motion.div data-errorbanner
    initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
    style={{
      width: '100%', maxWidth: 560, margin: '0 auto', background: P.surface,
      border: `1.5px solid ${P.ink}`, borderRadius: RADIUS.row, boxShadow: POP, padding: '10px 12px'
    }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontFamily: MONO, fontSize: 13, color: P.ink, lineHeight: 1 }}>✕</span>
      <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Gateway inference unavailable</span>
      <motion.span whileHover={{ scale: 1.05 }} transition={SPRING}
        style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 9px', border: `1px solid ${P.ink}`, background: P.ink, color: P.bg, cursor: 'pointer', whiteSpace: 'nowrap' }}>
        neu starten
      </motion.span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>✕</span>
    </div>
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 0', lineHeight: 1.5 }}>
      Der Dienst unter http://127.0.0.1:5050 antwortet nicht · Folgeanfragen werden gepuffert.
    </div>
  </motion.div>
)

const V06 = () => <Case name="06 · Fehlerbanner (in-stack Statuszeile)" note="billing-banner-Form: CreditCard-Glyph, Trailing-Knopf, nie rot —.standard Rahmenstärke">
  {(P) => (
    <div>
      <ErrorBanner P={P} />
      <motion.div data-statusrow-billing
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={SPRING}
        style={{
          width: '100%', maxWidth: 560, margin: '14px auto 0', display: 'flex', alignItems: 'center',
          gap: 8, padding: '4px 6px', borderRadius: RADIUS.row, background: P.hair,
          border: `1px solid ${P.ink}`, borderWidth: 1.5
        }}>
        <IconCreditCard size={13} stroke={1.8} color={P.ink} />
        <span style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          <b>Limit erreicht</b> <span style={{ color: P.sub }}>· Guthaben aufstocken, um fortzufahren</span>
        </span>
        <motion.span whileHover={{ scale: 1.03 }} transition={SPRING} style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 8px', border: `1px solid ${P.line}`, background: P.surface, color: P.ink, cursor: 'pointer', whiteSpace: 'nowrap' }}>Aufladen</motion.span>
      </motion.div>
    </div>
  )}
</Case>

/* ---------------------------------------------------- 07 · Verbindungsanzeige */

const Connecting = ({ P, label, state }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'center' }}>
    <motion.span data-connectingword
      animate={{ opacity: [1, 0.45, 1] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
      style={{ fontFamily: MONO, fontSize: 20, letterSpacing: '0.22em', color: P.ink }}>
      {label.slice(0, 3)}<span style={{ opacity: 0.45 }}>{label.slice(3)}</span>
    </motion.span>
  </div>
)

const StatusBar = ({ P }) => (
  <div data-statusbar style={{
    width: '100%', maxWidth: 560, margin: '0 auto', display: 'flex', alignItems: 'center',
    gap: 12, padding: '5px 10px', borderRadius: RADIUS.row, background: P.surface, border: `1px solid ${P.line}`
  }}>
    {[['dedicated', Wifi, 'ready'], ['out-of-sync', IconAlertCircle, 'inference unavailable']].map(([state, Icon, note], i) => (
      <span key={state} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: i === 1 ? P.ink : P.sub }}>
        <Icon size={11} stroke={1.8} color={i === 1 ? P.ink : P.sub} /> {note}
      </span>
    ))}
    <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>backend v1.4.0</span>
  </div>
)

const V07 = () => <Case name="07 · Verbindungsanzeige (CONNECTING + Gateway-Status)" note="Fullscreen Decode-Text beim Kaltstart; danach Statuszeile: ready / inference unavailable / offline">
  {(P) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'grid', placeItems: 'center', height: 96 }}>
        <Connecting P={P} label="CONNECTING" />
      </div>
      <StatusBar P={P} />
    </div>
  )}
</Case>

/* ---------------------------------------------------- 08 · Fortschrittsanzeige */

const Progress = ({ P, value }) => (
  <div style={{ width: '100%', maxWidth: 560 }}>
    <div data-progress style={{
      height: 8, borderRadius: RADIUS.pill, background: P.hair, overflow: 'hidden', position: 'relative'
    }}>
      <motion.div data-progressfill
        initial={{ width: 0 }} animate={{ width: `${value}%` }}
        transition={{ duration: 0.9, ease: 'easeInOut' }}
        style={{ height: '100%', borderRadius: RADIUS.pill, background: P.ink }} />
    </div>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
      <span>Gesamtpaket · Lades Backend</span>
      <span style={{ fontFamily: MONO, color: P.ink }}>{value} %</span>
    </div>
  </div>
)

const Indeterminate = ({ P }) => (
  <div data-progress-indet style={{
    width: '100%', maxWidth: 560, height: 8, borderRadius: RADIUS.pill, background: P.hair,
    overflow: 'hidden', position: 'relative'
  }}>
    <motion.div style={{
      position: 'absolute', top: 0, height: '100%', width: '34%', borderRadius: RADIUS.pill, background: P.ink
    }} animate={{ x: ['-34%', 'calc(520px)'] }} transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut', repeatType: 'mirror' }} />
  </div>
)

const LogBox = ({ P, lines }) => (
  <div data-progresslog style={{
    width: '100%', maxWidth: 560, border: `1px solid ${P.line}`, background: P.bg,
    borderRadius: RADIUS.row, padding: '8px 12px', fontFamily: MONO, fontSize: TEXT.meta, lineHeight: '16px', color: P.sub
  }}>
    {lines.map((l, i) => <div key={i} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l}</div>)}
  </div>
)

const ProgressDemo = ({ P }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center' }}>
    <Progress P={P} value={37} />
    <Indeterminate P={P} />
    <LogBox P={P} lines={[
      'step 3/8 — packages ok',
      'venv synced (uv, 214 wheels cached)',
      'renderer bundle — 3.9 MiB · tsc clean',
      'installer echoing install log …'
    ]} />
  </div>
)

const V08 = () => <Case name="08 · Fortschrittsanzeige (Update anwenden)" note="determinate 2..100 grenzwertig · indeterminate laufend · Log-Fenster: letzte 4 Zeilen, mono 11px">
  {(P) => <ProgressDemo P={P} />}
</Case>

/* ---------------------------------------------------- Exporte */

export default {
  '01 · Befehlspalette (Suche mit Tastenkürzel)': () => <V01 />,
  '02 · Aufklappmenü': () => <MenuToggle />,
  '03 · Kontextmenü': () => <V03 />,
  '04 · Kurzhinweis': () => <Case name="04 · Kurzhinweis (Tooltip)" note="bg-invert px-1.5 py-1 · 11px bold · Pfeil 6px · delay 150">{(P) => <Tooltip P={P} />}</Case>,
  '05 · Toast-Meldungen': () => <V05 />,
  '06 · Fehlerbanner': () => <V06 />,
  '07 · Verbindungsanzeige': () => <V07 />,
  '08 · Fortschrittsanzeige': () => <V08 />
}
