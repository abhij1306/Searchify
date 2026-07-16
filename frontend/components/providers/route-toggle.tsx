'use client';

import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { EngineRouteOption } from '@/lib/providers/catalog';
import type { TransportProvider } from '@/lib/api/types';

/**
 * Segmented direct/OpenRouter route toggle (F8, design.md §9.5).
 *
 * Renders one segment per route option. Enabled options are selectable;
 * reserved options (e.g. ChatGPT's "Direct OpenAI — coming soon", B-3) render
 * disabled with a tooltip and are never selectable. ChatGPT therefore shows a
 * single enabled OpenRouter segment plus the disabled OpenAI segment.
 */
export function RouteToggle({
  options,
  value,
  onChange,
  idBase,
}: Readonly<{
  options: EngineRouteOption[];
  value: TransportProvider | null;
  onChange: (transport: TransportProvider) => void;
  idBase: string;
}>) {
  return (
    <div
      role="radiogroup"
      aria-label="Route"
      className="inline-flex rounded-md border border-border-strong bg-background-alt p-0.5"
    >
      {options.map((option) => {
        const selected = option.transport_provider === value;
        const button = (
          <button
            key={option.transport_provider}
            type="button"
            role="radio"
            id={`${idBase}-${option.transport_provider}`}
            aria-checked={selected}
            aria-disabled={option.disabled || undefined}
            disabled={option.disabled}
            onClick={() => !option.disabled && onChange(option.transport_provider)}
            className={cn(
              'focus-ring rounded-[calc(var(--radius-md)-2px)] px-3 py-1 text-xs font-medium transition-colors',
              selected
                ? 'bg-panel text-foreground shadow-card'
                : 'text-secondary hover:text-foreground',
              option.disabled && 'cursor-not-allowed opacity-50 hover:text-secondary',
            )}
          >
            {option.label}
            {option.disabled && option.disabledReason ? (
              <span className="ml-1 text-muted">— {option.disabledReason}</span>
            ) : null}
          </button>
        );
        return option.disabled && option.disabledReason ? (
          <Tooltip key={option.transport_provider} content={`Direct OpenAI is ${option.disabledReason}.`}>
            {button}
          </Tooltip>
        ) : (
          button
        );
      })}
    </div>
  );
}
