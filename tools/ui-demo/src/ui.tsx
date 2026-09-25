/**
 * Bausteine in shadcn-Bauweise: cva + clsx + tailwind-merge, Radix fuer Verhalten,
 * Tabler fuer Symbole, Tokens aus styles.css. Kein zweiter Zustands- oder Transport-Store.
 */
import React from 'react'
import { cva } from 'class-variance-authority'
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...a: unknown[]) => twMerge(clsx(a))

export const knopf = cva(
  'inline-flex items-center justify-center gap-1.5 font-medium transition-colors disabled:opacity-50',
  {
    variants: {
      variante: {
        primaer: 'bg-[var(--ink)] text-[var(--surface)] border border-[var(--ink)]',
        standard: 'bg-[var(--surface)] text-[var(--ink)] border border-[var(--line)]',
        still: 'bg-transparent text-[var(--sub)] border border-transparent'
      },
      groesse: { gross: 'h-8 px-3 text-[13px]', normal: 'h-7 px-2.5 text-[12px]', klein: 'h-6 px-2 text-[11px]' }
    },
    defaultVariants: { variante: 'standard', groesse: 'normal' }
  }
)

type KnopfProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variante?: 'primaer' | 'standard' | 'still'
  groesse?: 'gross' | 'normal' | 'klein'
}

export const Knopf = ({ variante, groesse, className, ...rest }: KnopfProps) => (
  <button className={cn(knopf({ variante, groesse }), 'rounded-[var(--radius-micro)] cursor-pointer', className)} {...rest} />
)

export const SymbolKnopf = ({ titel, aktiv, className, children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { titel: string; aktiv?: boolean }) => (
  <button
    title={titel}
    aria-label={titel}
    aria-pressed={aktiv}
    className={cn('h-6 w-6 inline-flex items-center justify-center rounded-[var(--radius-micro)] border cursor-pointer',
      aktiv ? 'bg-[var(--soft)] border-[var(--line)]' : 'bg-transparent border-transparent hover:bg-[var(--soft)]', className)}
    {...rest}
  >
    {children}
  </button>
)

export const Karte = ({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('bg-[var(--surface)] border border-[var(--line)] rounded-[var(--radius-module)] p-2.5', className)} {...rest} />
)

export const Schalter = ({ an, setAn, titel }: { an: boolean; setAn: (v: boolean) => void; titel: string }) => (
  <button
    role="switch"
    aria-checked={an}
    aria-label={titel}
    title={titel}
    onClick={() => setAn(!an)}
    className={cn('h-5 w-9 rounded-full border p-0.5 flex cursor-pointer transition-colors',
      an ? 'bg-[var(--ink)] border-[var(--ink)] justify-end' : 'bg-[var(--soft)] border-[var(--line)] justify-start')}
  >
    <span className={cn('h-4 w-4 rounded-full transition-transform', an ? 'bg-[var(--surface)]' : 'bg-[var(--faint)]')} />
  </button>
)

export const Kbd = ({ children }: { children: React.ReactNode }) => (
  <kbd className="inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-[var(--radius-micro)] border border-[var(--line)] bg-[var(--soft)] px-1.5 font-mono text-[11px]">{children}</kbd>
)

export const Abschnitt = ({ titel, children, meta }: { titel: string; children: React.ReactNode; meta?: React.ReactNode }) => (
  <section className="mb-3">
    <header className="mb-1.5 flex items-center gap-2">
      <h2 className="text-[13px] font-semibold">{titel}</h2>
      {meta ? <span className="ml-auto text-[11px] text-[var(--sub)]">{meta}</span> : null}
    </header>
    {children}
  </section>
)
