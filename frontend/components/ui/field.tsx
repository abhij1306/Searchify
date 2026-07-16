import { useId } from 'react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Field (§8) — wraps a control with a label, optional hint, and an inline
 * error. The label is associated to the control via a generated id (passed
 * through the `children` render prop) for accessibility.
 */
export function Field({
  label,
  hint,
  error,
  required,
  className,
  children,
}: Readonly<{
  label: string;
  hint?: string;
  error?: ReactNode;
  required?: boolean;
  className?: string;
  children: (props: {
    id: string;
    'aria-invalid'?: boolean;
    'aria-describedby'?: string;
  }) => ReactNode;
}>) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') ||
    undefined;

  return (
    <div className={cn('grid gap-1.5', className)}>
      <label htmlFor={id} className="text-xs font-medium text-secondary">
        {label}
        {required ? <span className="ml-0.5 text-danger">*</span> : null}
      </label>
      {children({
        id,
        'aria-invalid': error ? true : undefined,
        'aria-describedby': describedBy,
      })}
      {hint && !error ? (
        <span id={hintId} className="text-xs text-muted">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={errorId} role="alert" className="text-xs text-danger-text">
          {error}
        </span>
      ) : null}
    </div>
  );
}
