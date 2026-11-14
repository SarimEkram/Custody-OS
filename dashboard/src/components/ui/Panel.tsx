import type { ReactNode } from 'react'

/** A surface with an optional quiet header. Hierarchy comes from spacing, not heavy borders. */
export function Panel({
  title,
  icon,
  actions,
  children,
  className = '',
  bodyClass = 'p-4',
}: {
  title?: string
  icon?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClass?: string
}) {
  return (
    <section className={`flex min-h-0 flex-col rounded-xl bg-surface ring-1 ring-line/60 ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 px-4 pt-3 pb-1">
          <h2 className="flex items-center gap-2 text-[13px] font-medium text-muted">
            {icon}
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className={`min-h-0 flex-1 ${bodyClass}`}>{children}</div>
    </section>
  )
}
