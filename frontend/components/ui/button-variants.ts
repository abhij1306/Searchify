import { cva } from 'class-variance-authority';

/**
 * Button CVA — token-driven surfaces (§8). Variants map to semantic bridged
 * tokens only (no raw hex). Sizes use the control-height tokens via bridged
 * `h-*` utilities defined in globals.css (--control-height*).
 *
 * CUBE27 midnight language (Phase D2): every variant is a pill. Primary is
 * the landing's monochrome pill — `bg-foreground` (bridged --text-primary:
 * white on midnight, near-black on warm paper) with `text-background`
 * (bridged --bg-base); blue stays reserved for links/active/focus. Secondary
 * is the mockups' raised social-button surface (elevated + hairline border +
 * hover lift); ghost is transparent with a soft accent hover fill.
 */
export const buttonVariants = cva(
  'focus-ring inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-full border font-sans font-medium leading-none no-underline transition-[background-color,color,border-color,box-shadow,transform] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'border-transparent bg-foreground text-background hover:bg-foreground/90',
        secondary:
          'border-border bg-elevated text-foreground hover:-translate-y-px hover:border-border-strong hover:bg-well',
        neutral: 'border-border bg-background-alt text-foreground hover:bg-well',
        ghost:
          'border-transparent bg-transparent text-secondary hover:bg-accent-soft hover:text-foreground',
        destructive: 'border-transparent bg-danger text-accent-fg hover:opacity-90',
      },
      size: {
        sm: 'h-[var(--control-height-sm)] px-2.5 text-xs',
        md: 'h-[var(--control-height)] px-3.5 text-sm',
        lg: 'h-[var(--control-height-lg)] px-4 text-base',
        icon: 'size-[var(--control-height)] px-0',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
);
