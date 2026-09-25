/**
 * Barrierefreiheit fuer alle Fixtures — zwei Befunde an einer Stelle:
 *
 * A1 (reduzierte Bewegung): der UI-Vertrag verlangt, dass „Reduced Motion" unnoetige Bewegung
 *    abschaltet. Messung am 18.09.2026: mit erzwungenem `prefers-reduced-motion: reduce` lief der
 *    Ecken-Puls der Computer-Use weiter. Hier wird (a) eine CSS-Regel eingezogen, die CSS-Animationen
 *    und -Uebergaenge auf 0 setzt, und (b) `MotionGlobalConfig.skipAnimations` gesetzt, damit auch
 *    Motion-Animationen springen statt zu laufen. Reagiert live auf Wechsel der Einstellung.
 *
 * A5 (Tastatur): gemessen 261 × onClick, aber 0 × tabIndex. Hier wird jedes Element im Fixture-Rahmen,
 *    das per Zeiger bedienbar ist (`cursor: pointer`), zu `role="button"` + `tabIndex=0` aufgewertet und
 *    mit Enter/Leertaste bedienbar gemacht (also auch nachtraeglich eingehaengte Elemente, per
 *    MutationObserver). WICHTIG fuer den Produkteinbau: dort echte <button> verwenden — diese Bruecke
 *    ist die Werkbank-Loesung fuer die 261 Bestandsstellen, kein Ersatz fuer sauberes Markup.
 */
import { useEffect } from 'react'
import { MotionGlobalConfig } from 'motion/react'

const CSS = `@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}`

/** Schon beim Laden des Moduls auswerten — VOR dem Aufbau jeder Komponente.
 *  Grund: Motion liest `skipAnimations` beim ERZEUGEN einer Animation. Wird das Flag erst im
 *  useEffect gesetzt, laeuft die Endlos-Schleife (z. B. der Ecken-Puls) bereits — Messung
 *  zeigte 272 → 120 → 268 px trotz reduzierter Bewegung. */
if (typeof window !== 'undefined' && window.matchMedia) {
  try {
    MotionGlobalConfig.skipAnimations = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch { /* aeltere Motion-Version */ }
}

export const useBarrierefrei = () => {
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    let takt = null
    // Laufende Animationen (auch WAAPI/Motion) anhalten, solange die Einstellung aktiv ist.
    // Grund: `skipAnimations` wirkt nur auf NEU erzeugte Animationen — eine bereits laufende
    // Schleife (z. B. der Ecken-Puls) lief in der Messung weiter (270 → 198 → 224 px).
    const anhalten = () => {
      try { (document.getAnimations ? document.getAnimations() : []).forEach(a => { try { a.pause() } catch {} }) } catch {}
    }
    const anwenden = () => {
      try { MotionGlobalConfig.skipAnimations = mq.matches } catch { /* aeltere Version */ }
      if (mq.matches) { anhalten(); if (!takt) takt = setInterval(anhalten, 400) }
      else if (takt) { clearInterval(takt); takt = null }
    }
    anwenden()
    mq.addEventListener('change', anwenden)
    if (!document.getElementById('gv-reduce-motion')) {
      const s = document.createElement('style')
      s.id = 'gv-reduce-motion'
      s.textContent = CSS
      document.head.appendChild(s)
    }
    return () => { mq.removeEventListener('change', anwenden); if (takt) clearInterval(takt) }
  }, [])

  useEffect(() => {
    const root = document.body
    const aufwerten = () => {
      const kandidaten = root.querySelectorAll('span,div,svg')
      kandidaten.forEach(el => {
        let zeiger = false
        try { zeiger = getComputedStyle(el).cursor === 'pointer' } catch { return }
        if (!zeiger) return
        if (el.getAttribute('data-kb') === '1') return
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0')
        if (!el.hasAttribute('role')) el.setAttribute('role', 'button')
        if (!el.hasAttribute('aria-label') && !el.getAttribute('title')) {
          const t = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60)
          el.setAttribute('aria-label', t || 'Aktion')
        }
        el.setAttribute('data-kb', '1')
      })
    }
    aufwerten()
    const beobachter = new MutationObserver(() => aufwerten())
    beobachter.observe(root, { childList: true, subtree: true })
    const taste = (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return
      const t = e.target
      if (t && t.getAttribute && t.getAttribute('data-kb') === '1') {
        e.preventDefault()
        if (typeof t.click === 'function') t.click()
      }
    }
    root.addEventListener('keydown', taste)
    return () => { beobachter.disconnect(); root.removeEventListener('keydown', taste) }
  }, [])
}
