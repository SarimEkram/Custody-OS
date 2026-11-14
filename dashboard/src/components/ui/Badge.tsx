import type { LucideIcon } from 'lucide-react'
import type { Tone } from '../../lib/format'

export const toneText: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  bad: 'text-bad',
  info: 'text-info',
  mute: 'text-muted',
}
export const toneBorder: Record<Tone, string> = {
  ok: 'border-ok/40 bg-ok/10',
  warn: 'border-warn/40 bg-warn/10',
  bad: 'border-bad/40 bg-bad/10',
  info: 'border-info/40 bg-info/10',
  mute: 'border-line bg-surface',
}
const toneSoft: Record<Tone, string> = {
  ok: 'bg-ok/12 text-ok',
  warn: 'bg-warn/12 text-warn',
  bad: 'bg-bad/12 text-bad',
  info: 'bg-info/12 text-info',
  mute: 'bg-white/5 text-muted',
}
const toneDot: Record<Tone, string> = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad', info: 'bg-info', mute: 'bg-muted' }

/** "PENDING APPROVAL" → "Pending approval". Mixed-case labels pass through untouched. */
export function sentence(label: string): string {
  if (/[a-z]/.test(label)) return label
  const s = label.toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Soft pill: a dot or icon plus a sentence-case label. Never colour alone. */
export function Badge({ tone, label, icon: Icon, title }: { tone: Tone; label: string; icon?: LucideIcon; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${toneSoft[tone]}`}>
      {Icon ? <Icon size={12} aria-hidden /> : <span className={`size-1.5 rounded-full ${toneDot[tone]}`} aria-hidden />}
      {sentence(label)}
    </span>
  )
}

export const Mono = ({ children, className = '' }: { children: React.ReactNode; className?: string }) => (
  <span className={`font-mono text-[0.9em] tracking-tight ${className}`}>{children}</span>
)
