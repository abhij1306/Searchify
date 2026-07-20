import { cn } from '@/lib/utils';

/**
 * SegmentedControl (F6) — a single-select segmented toggle used for
 * `benchmark_mode`. Radiogroup semantics so it is keyboard + screen-reader
 * accessible; selection is fully controlled by the caller (react-hook-form).
 */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  id,
  'aria-describedby': describedBy,
}: Readonly<{
  value: T;
  onChange: (value: T) => void;
  options: readonly { value: T; label: string }[];
  ariaLabel?: string;
  id?: string;
  'aria-describedby'?: string;
}>) {
  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={ariaLabel}
      aria-describedby={describedBy}
      className="border-border bg-background-alt inline-flex flex-wrap gap-1 rounded-md border p-1"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              'focus-ring rounded-sm px-3 py-1.5 text-sm font-medium transition-colors',
              selected
                ? 'bg-panel text-foreground shadow-card'
                : 'text-secondary hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
