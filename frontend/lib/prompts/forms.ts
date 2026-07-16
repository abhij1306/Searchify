/**
 * Prompt manual-entry form model (F7).
 *
 * A small zod schema + helpers used by the add/edit dialog. Kept separate from
 * the component so the mapping to the API `PromptInput` is unit-testable and
 * the dialog stays presentational.
 */
import { z } from 'zod';

import type { PromptInput } from '@/lib/api/prompts';
import { promptIntentSchema } from '@/lib/api/schemas';
import type { Prompt, PromptIntent } from '@/lib/api/types';

export const promptFormSchema = z.object({
  text: z.string().trim().min(1, 'Prompt text is required.'),
  theme: z.string().trim().max(255, 'Theme is too long.'),
  intent: promptIntentSchema,
  branded: z.boolean(),
  enabled: z.boolean(),
});

export type PromptFormValues = z.infer<typeof promptFormSchema>;

/** Ordered intent options for the select; '' renders as "Unspecified". */
export const intentValues: PromptIntent[] = promptIntentSchema.options;

export const intentLabels: Record<PromptIntent, string> = {
  '': 'Unspecified',
  discovery: 'Discovery',
  comparison: 'Comparison',
  purchase: 'Purchase',
  service: 'Service',
  local: 'Local',
};

export const emptyPromptForm: PromptFormValues = {
  text: '',
  theme: '',
  intent: '',
  branded: false,
  enabled: true,
};

/** Prefill the form from an existing prompt (edit path). */
export function promptToFormValues(prompt: Prompt): PromptFormValues {
  return {
    text: prompt.text,
    theme: prompt.theme ?? '',
    intent: prompt.intent,
    branded: prompt.branded,
    enabled: prompt.enabled,
  };
}

/** Map validated form values to the API create/update payload. */
export function formValuesToPromptInput(values: PromptFormValues): PromptInput {
  return {
    text: values.text.trim(),
    theme: values.theme.trim() || null,
    intent: values.intent,
    branded: values.branded,
    enabled: values.enabled,
  };
}
