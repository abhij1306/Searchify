import { describe, expect, it } from 'vitest';

import type { Project } from '@/lib/api/types';
import {
  emptySetupForm,
  formValuesToProjectInput,
  projectToFormValues,
  setupErrorMessage,
  setupFormSchema,
} from './forms';

const project: Project = {
  id: '22222222-2222-4222-8222-222222222222',
  workspace_id: '33333333-3333-4333-8333-333333333333',
  name: 'Searchify — US',
  brand_name: 'Searchify',
  website_url: 'https://searchify.com',
  country_code: 'US',
  language_code: 'en',
  benchmark_mode: 'controlled_localized',
  default_repetitions: 5,
  brand: { aliases: ['Searchify AI', 'Searchify.io'] },
  owned_domains: ['searchify.com'],
  unintended_domains: ['searchify.net'],
  competitors: [
    {
      id: '44444444-4444-4444-8444-444444444444',
      name: 'Acme',
      aliases: ['Acme Corp'],
      domains: ['acme.com'],
    },
  ],
  prompt_sets: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('setupFormSchema', () => {
  const base = {
    ...emptySetupForm,
    brand_name: 'Searchify',
    name: 'Searchify — US',
    website_url: 'https://searchify.com',
  };

  it('accepts a fully valid form', () => {
    const result = setupFormSchema.safeParse(base);
    expect(result.success).toBe(true);
  });

  it('rejects a missing brand name and an invalid website URL', () => {
    const result = setupFormSchema.safeParse({ ...base, brand_name: '  ', website_url: 'notaurl' });
    expect(result.success).toBe(false);
    if (!result.success) {
      const fields = result.error.issues.map((issue) => issue.path.join('.'));
      expect(fields).toContain('brand_name');
      expect(fields).toContain('website_url');
    }
  });

  it('rejects a bad country code and out-of-range repetitions', () => {
    const result = setupFormSchema.safeParse({
      ...base,
      country_code: 'USA',
      default_repetitions: 99,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const fields = result.error.issues.map((issue) => issue.path.join('.'));
      expect(fields).toContain('country_code');
      expect(fields).toContain('default_repetitions');
    }
  });

  it('validates domain rows (owned/unintended) and empty alias rows', () => {
    const result = setupFormSchema.safeParse({
      ...base,
      owned_domains: [{ value: 'https://bad.com/path' }],
      aliases: [{ value: '   ' }],
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const fields = result.error.issues.map((issue) => issue.path.join('.'));
      expect(fields).toContain('owned_domains.0.value');
      expect(fields).toContain('aliases.0.value');
    }
  });

  it('validates competitor rows including nested domains', () => {
    const result = setupFormSchema.safeParse({
      ...base,
      competitors: [{ name: '', aliases: [], domains: [{ value: 'bad domain' }] }],
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const fields = result.error.issues.map((issue) => issue.path.join('.'));
      expect(fields).toContain('competitors.0.name');
      expect(fields).toContain('competitors.0.domains.0.value');
    }
  });
});

describe('mappers', () => {
  it('projectToFormValues hydrates entry lists from string arrays', () => {
    const values = projectToFormValues(project);
    expect(values.brand_name).toBe('Searchify');
    expect(values.aliases).toEqual([{ value: 'Searchify AI' }, { value: 'Searchify.io' }]);
    expect(values.owned_domains).toEqual([{ value: 'searchify.com' }]);
    expect(values.competitors[0]).toEqual({
      name: 'Acme',
      aliases: [{ value: 'Acme Corp' }],
      domains: [{ value: 'acme.com' }],
    });
  });

  it('formValuesToProjectInput flattens entries, trims, and uppercases the country', () => {
    const values = projectToFormValues(project);
    const input = formValuesToProjectInput({
      ...values,
      country_code: 'gb',
      aliases: [{ value: ' Searchify AI ' }, { value: '  ' }],
    });
    expect(input.country_code).toBe('GB');
    expect(input.brand.aliases).toEqual(['Searchify AI']);
    expect(input.owned_domains).toEqual(['searchify.com']);
    expect(input.competitors[0]).toEqual({
      name: 'Acme',
      aliases: ['Acme Corp'],
      domains: ['acme.com'],
    });
  });

  it('round-trips project → form → input without losing fields', () => {
    const input = formValuesToProjectInput(projectToFormValues(project));
    expect(input.name).toBe(project.name);
    expect(input.benchmark_mode).toBe(project.benchmark_mode);
    expect(input.default_repetitions).toBe(project.default_repetitions);
    expect(input.unintended_domains).toEqual(project.unintended_domains);
  });
});

describe('setupErrorMessage', () => {
  it('surfaces an Error message and falls back for anything else', () => {
    expect(setupErrorMessage(new Error('Bad request.'))).toBe('Bad request.');
    expect(setupErrorMessage(null)).toMatch(/something went wrong/i);
  });
});
