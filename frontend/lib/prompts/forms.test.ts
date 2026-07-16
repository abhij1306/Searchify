import { describe, expect, it } from 'vitest';

import type { Prompt } from '@/lib/api/types';
import {
  emptyPromptForm,
  formValuesToPromptInput,
  promptFormSchema,
  promptToFormValues,
} from './forms';

describe('promptFormSchema', () => {
  it('requires non-empty text', () => {
    expect(promptFormSchema.safeParse({ ...emptyPromptForm }).success).toBe(false);
    expect(
      promptFormSchema.safeParse({ ...emptyPromptForm, text: 'Hello' }).success,
    ).toBe(true);
  });
});

describe('form mapping', () => {
  it('prefills from an existing prompt', () => {
    const prompt: Prompt = {
      id: '11111111-1111-4111-8111-111111111111',
      prompt_set_id: '22222222-2222-4222-8222-222222222222',
      text: 'Best shoes?',
      theme: null,
      intent: 'purchase',
      branded: true,
      enabled: false,
      origin: 'manual',
    };
    expect(promptToFormValues(prompt)).toEqual({
      text: 'Best shoes?',
      theme: '',
      intent: 'purchase',
      branded: true,
      enabled: false,
    });
  });

  it('maps form values to a PromptInput with null theme when blank', () => {
    expect(
      formValuesToPromptInput({
        text: '  Best shoes?  ',
        theme: '   ',
        intent: 'discovery',
        branded: false,
        enabled: true,
      }),
    ).toEqual({
      text: 'Best shoes?',
      theme: null,
      intent: 'discovery',
      branded: false,
      enabled: true,
    });
  });
});
