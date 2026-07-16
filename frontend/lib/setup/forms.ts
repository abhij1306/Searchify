/**
 * Client-side Brand/Project setup form schema + mappers (F6).
 *
 * Like `lib/auth/forms.ts`, this validates the `/setup` form *input* in the
 * browser (react-hook-form + zod) before a request is made — it is separate
 * from the API contract schemas in `lib/api/schemas.ts` (which validate server
 * *output*). It also carries the two mappers that bridge the form's shape and
 * the backend `ProjectInput` / `Project` shapes:
 *   - repeatable string lists are modelled as arrays of `{ value }` objects so
 *     they work with react-hook-form's `useFieldArray`; the mappers flatten to
 *     / hydrate from plain `string[]`.
 */
import { z } from 'zod';

import type { BenchmarkMode, Project } from '@/lib/api/types';
import type { ProjectInput } from '@/lib/api/projects';

/** A single repeatable text entry (alias / domain). */
const entrySchema = (label: string, validate?: (value: string) => boolean, message?: string) =>
  z.object({
    value: z
      .string()
      .trim()
      .min(1, `${label} cannot be empty.`)
      .refine((value) => (validate ? validate(value) : true), {
        message: message ?? `Enter a valid ${label.toLowerCase()}.`,
      }),
  });

/** Loose domain check: `example.com`, `sub.example.co.uk` — no scheme/path. */
const DOMAIN_PATTERN = /^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/i;
const isDomain = (value: string) => DOMAIN_PATTERN.test(value);

const aliasEntrySchema = entrySchema('Name');
const domainEntrySchema = entrySchema('Domain', isDomain, 'Enter a bare domain, e.g. example.com.');

export const benchmarkModeValues = [
  'consumer_like',
  'controlled_localized',
  'forced_grounded',
] as const satisfies readonly BenchmarkMode[];

/** Human labels for the `benchmark_mode` segmented select. */
export const benchmarkModeLabels: Record<BenchmarkMode, string> = {
  consumer_like: 'Consumer-like',
  controlled_localized: 'Controlled / localized',
  forced_grounded: 'Forced grounded',
};

export const setupFormSchema = z.object({
  brand_name: z.string().trim().min(1, 'Brand name is required.'),
  name: z.string().trim().min(1, 'Project name is required.'),
  website_url: z
    .string()
    .trim()
    .min(1, 'Website URL is required.')
    .url('Enter a full URL, e.g. https://example.com.'),
  country_code: z
    .string()
    .trim()
    .regex(/^[A-Za-z]{2}$/, 'Use a 2-letter country code, e.g. US.'),
  language_code: z
    .string()
    .trim()
    .regex(/^[A-Za-z]{2}(-[A-Za-z]{2})?$/, 'Use a language code, e.g. en or en-US.'),
  benchmark_mode: z.enum(benchmarkModeValues),
  default_repetitions: z
    .number({ invalid_type_error: 'Enter a number.' })
    .int('Must be a whole number.')
    .min(1, 'At least 1 repetition.')
    .max(10, 'At most 10 repetitions.'),
  aliases: z.array(aliasEntrySchema),
  owned_domains: z.array(domainEntrySchema),
  unintended_domains: z.array(domainEntrySchema),
  competitors: z.array(
    z.object({
      name: z.string().trim().min(1, 'Competitor name is required.'),
      aliases: z.array(aliasEntrySchema),
      domains: z.array(domainEntrySchema),
    }),
  ),
});

export type SetupFormValues = z.infer<typeof setupFormSchema>;

/** Blank form state for the create flow. */
export const emptySetupForm: SetupFormValues = {
  brand_name: '',
  name: '',
  website_url: '',
  country_code: 'US',
  language_code: 'en',
  benchmark_mode: 'consumer_like',
  default_repetitions: 3,
  aliases: [],
  owned_domains: [],
  unintended_domains: [],
  competitors: [],
};

const toEntries = (values: string[]): { value: string }[] =>
  values.map((value) => ({ value }));

const fromEntries = (entries: { value: string }[]): string[] =>
  entries.map((entry) => entry.value.trim()).filter((value) => value.length > 0);

/** Hydrate the form from an existing project (edit mode prefill). */
export function projectToFormValues(project: Project): SetupFormValues {
  return {
    brand_name: project.brand_name,
    name: project.name,
    website_url: project.website_url,
    country_code: project.country_code,
    language_code: project.language_code,
    benchmark_mode: project.benchmark_mode,
    default_repetitions: project.default_repetitions,
    aliases: toEntries(project.brand.aliases),
    owned_domains: toEntries(project.owned_domains),
    unintended_domains: toEntries(project.unintended_domains),
    competitors: project.competitors.map((competitor) => ({
      name: competitor.name,
      aliases: toEntries(competitor.aliases),
      domains: toEntries(competitor.domains),
    })),
  };
}

/** Flatten validated form values into the backend `ProjectInput` payload. */
export function formValuesToProjectInput(values: SetupFormValues): ProjectInput {
  return {
    name: values.name.trim(),
    brand_name: values.brand_name.trim(),
    website_url: values.website_url.trim(),
    country_code: values.country_code.trim().toUpperCase(),
    language_code: values.language_code.trim(),
    benchmark_mode: values.benchmark_mode,
    default_repetitions: values.default_repetitions,
    brand: { aliases: fromEntries(values.aliases) },
    owned_domains: fromEntries(values.owned_domains),
    unintended_domains: fromEntries(values.unintended_domains),
    competitors: values.competitors.map((competitor) => ({
      name: competitor.name.trim(),
      aliases: fromEntries(competitor.aliases),
      domains: fromEntries(competitor.domains),
    })),
  };
}

/**
 * Best-effort human message from a thrown mutation error. Mirrors the auth
 * form helper: the transport unwraps a JSON `{ detail }` into `error.message`.
 */
export function setupErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}
