import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';
import {
  badgeBase,
  classificationBadge,
  neutralBadge,
  runStatusBadge,
  sentimentBadge,
  statusBadge,
  type ClassificationValue,
  type RunStatusValue,
  type SentimentValue,
  type StatusValue,
} from './badge-variants';

/**
 * Badge — discriminated on `variant` so each family only accepts its own
 * values, and each value resolves to the correct bridged token classes.
 * A text label is always rendered (never color-only meaning — WCAG 1.4.1).
 */
export type BadgeProps = {
  children: ReactNode;
  className?: string;
} & (
  | { variant: 'status'; value: StatusValue }
  | { variant: 'sentiment'; value: SentimentValue }
  | { variant: 'classification'; value: ClassificationValue }
  | { variant: 'run-status'; value: RunStatusValue }
  | { variant?: 'neutral'; value?: undefined }
) &
  Omit<HTMLAttributes<HTMLSpanElement>, 'children'>;

function badgeClasses(props: BadgeProps): string {
  switch (props.variant) {
    case 'status':
      return statusBadge[props.value];
    case 'sentiment':
      return sentimentBadge[props.value];
    case 'classification':
      return classificationBadge[props.value];
    case 'run-status':
      return runStatusBadge[props.value];
    default:
      return neutralBadge;
  }
}

export function Badge(props: Readonly<BadgeProps>) {
  const { children, className } = props;
  return (
    <span className={cn(badgeBase, badgeClasses(props), className)}>
      <span className="size-1 rounded-full bg-current" aria-hidden />
      {children}
    </span>
  );
}
