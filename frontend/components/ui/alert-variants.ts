import { cva } from 'class-variance-authority';

/** Alert CVA (§8) — tone maps to bridged status token classes (no raw hex). */
export const alertVariants = cva(
  'flex items-start gap-2 rounded-md border px-3 py-2 text-sm leading-normal',
  {
    variants: {
      tone: {
        danger: 'border-danger-border bg-danger-bg text-danger-text',
        warning: 'border-warning-border bg-warning-bg text-warning-text',
        success: 'border-success-border bg-success-bg text-success-text',
        info: 'border-info-border bg-info-bg text-info-text',
        neutral: 'border-border bg-background-alt text-secondary',
      },
    },
    defaultVariants: {
      tone: 'danger',
    },
  },
);
