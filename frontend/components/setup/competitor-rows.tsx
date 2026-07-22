'use client';

import { Trash2 } from 'lucide-react';
import type { Control, FieldErrors, UseFieldArrayReturn, UseFormRegister } from 'react-hook-form';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import type { SetupFormValues } from '@/lib/setup/forms';

import { EntryList } from './entry-list';
import { eyebrowClasses } from '@/components/ui/eyebrow';

/**
 * CompetitorRows (F6) — repeatable competitor cards, each with a name plus
 * nested alias and domain lists (their own `useFieldArray` via `EntryList`).
 * Add / remove a whole competitor row here; the inner lists manage their rows.
 *
 * The `competitors` field array is owned by the parent form (react-hook-form
 * allows only one `useFieldArray` per name, and SetupForm appends AI-suggested
 * competitors itself) and passed in via `fieldArray`.
 */
export function CompetitorRows({
  control,
  register,
  errors,
  fieldArray,
}: Readonly<{
  control: Control<SetupFormValues>;
  register: UseFormRegister<SetupFormValues>;
  errors: FieldErrors<SetupFormValues>;
  fieldArray: UseFieldArrayReturn<SetupFormValues, 'competitors'>;
}>) {
  const { fields, append, remove } = fieldArray;

  return (
    <div className="grid gap-4">
      {fields.length === 0 ? (
        <p className="text-muted text-sm">
          No competitors yet. Add the brands you want to benchmark against.
        </p>
      ) : null}

      {fields.map((field, index) => (
        <div
          key={field.id}
          className="border-border-subtle bg-background-alt grid gap-4 rounded-lg border p-4"
        >
          <div className="flex items-start justify-between gap-2">
            <span className={eyebrowClasses}>Competitor {index + 1}</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Remove competitor ${index + 1}`}
              onClick={() => remove(index)}
            >
              <Trash2 className="size-4" aria-hidden />
              Remove
            </Button>
          </div>

          <Field
            label="Competitor name"
            required
            error={errors.competitors?.[index]?.name?.message}
          >
            {(props) => (
              <Input
                {...props}
                {...register(`competitors.${index}.name` as const)}
                placeholder="Acme Corp"
              />
            )}
          </Field>

          <EntryList
            control={control}
            name={`competitors.${index}.aliases` as const}
            label="Aliases"
            placeholder="Acme"
            addLabel="Add alias"
            errors={errors}
          />

          <EntryList
            control={control}
            name={`competitors.${index}.domains` as const}
            label="Domains"
            placeholder="acme.com"
            addLabel="Add domain"
            errors={errors}
          />
        </div>
      ))}

      <div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => append({ name: '', aliases: [], domains: [] })}
        >
          Add competitor
        </Button>
      </div>
    </div>
  );
}
