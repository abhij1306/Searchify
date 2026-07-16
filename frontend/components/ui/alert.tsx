import type { ReactNode } from 'react';
import type { VariantProps } from 'class-variance-authority';
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from 'lucide-react';

import { cn } from '@/lib/utils';
import { alertVariants } from './alert-variants';

const toneIcon = {
  danger: AlertCircle,
  warning: TriangleAlert,
  success: CheckCircle2,
  info: Info,
  neutral: Info,
} as const;

export type AlertProps = {
  children: ReactNode;
  className?: string;
  /** Hide the leading tone icon. */
  hideIcon?: boolean;
} & VariantProps<typeof alertVariants>;

export function Alert({ children, tone, hideIcon, className }: Readonly<AlertProps>) {
  if (!children) return null;
  const Icon = toneIcon[tone ?? 'danger'];
  return (
    <div role="alert" className={cn(alertVariants({ tone }), className)}>
      {hideIcon ? null : <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />}
      <div className="min-w-0">{children}</div>
    </div>
  );
}
