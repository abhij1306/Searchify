'use client';

import { X } from 'lucide-react';
import { useFieldArray, type Control, type FieldErrors } from 'react-hook-form';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import type { SetupFormValues } from '@/lib/setup/forms';

/**
 * Any dot-path in the setup form whose value is an array of `{ value }`
 * entries — the alias / owned-domain / unintended-domain lists and the
 * per-competitor alias / domain lists.
 */
type EntryListName =
  | 'aliases'
  | 'owned_domains'
  | 'unintended_domains'
  | `competitors.${number}.aliases`
  | `competitors.${number}.domains`;

/**
 * EntryList (F6) — a repeatable single-column text list backed by
 * react-hook-form's `useFieldArray`. Each row is an `{ value }` object so the
 * field array has a stable key; the form mappers flatten these to `string[]`.
 * Per-row validation errors render inline beneath each input.
 */
export function EntryList({
  control,
  name,
  label,
  placeholder,
  addLabel,
  errors,
}: Readonly<{
  control: Control<SetupFormValues>;
  name: EntryListName;
  label: string;
  placeholder?: string;
  addLabel: string;
  errors?: FieldErrors<SetupFormValues>;
}>) {
  const { fields, append, remove } = useFieldArray({ control, name });

  // Resolve the array of per-row errors for this named list from the (possibly
  // nested) form errors object without leaning on `any`.
  const rowErrors = resolveEntryErrors(errors, name);

  return (
    <fieldset className="grid gap-1.5">
      <legend className="text-secondary text-xs font-medium">{label}</legend>
      {fields.length > 0 ? (
        <ul className="grid gap-2">
          {fields.map((field, index) => {
            const message = rowErrors?.[index]?.value?.message;
            return (
              <li key={field.id} className="grid gap-1">
                <div className="flex items-center gap-2">
                  <Input
                    aria-label={`${label} ${index + 1}`}
                    aria-invalid={message ? true : undefined}
                    placeholder={placeholder}
                    {...control.register(`${name}.${index}.value` as const)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove ${label} ${index + 1}`}
                    onClick={() => remove(index)}
                  >
                    <X className="size-4" aria-hidden />
                  </Button>
                </div>
                {message ? (
                  <span role="alert" className={cn('text-danger-text text-xs')}>
                    {message}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <div>
        <Button type="button" variant="secondary" size="sm" onClick={() => append({ value: '' })}>
          {addLabel}
        </Button>
      </div>
    </fieldset>
  );
}

type EntryRowError = { value?: { message?: string } };

/** Walk the form errors to the array of row errors for a dotted list name. */
function resolveEntryErrors(
  errors: FieldErrors<SetupFormValues> | undefined,
  name: EntryListName,
): (EntryRowError | undefined)[] | undefined {
  if (!errors) return undefined;
  const segments = name.split('.');
  let node: unknown = errors;
  for (const segment of segments) {
    if (node == null || typeof node !== 'object') return undefined;
    node = (node as Record<string, unknown>)[segment];
  }
  return Array.isArray(node) ? (node as (EntryRowError | undefined)[]) : undefined;
}
