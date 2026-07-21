'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Controller, useFieldArray, useForm, useWatch, type FieldErrors } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
  projectsApi,
  type CompetitorSuggestInput,
  type OwnedDomainSuggestInput,
  type ProjectInput,
} from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import type { Project } from '@/lib/api/types';
import { useProjectContext } from '@/lib/project/project-context';
import {
  benchmarkModeLabels,
  benchmarkModeValues,
  emptySetupForm,
  formValuesToProjectInput,
  projectToFormValues,
  setupErrorMessage,
  setupFormSchema,
  type SetupFormValues,
} from '@/lib/setup/forms';

import { CompetitorRows } from './competitor-rows';
import { EntryList, EntryListView } from './entry-list';
import { GenerateBrandDialog } from './generate-brand-dialog';
import { SegmentedControl } from './segmented-control';
import { SetupStepper, type SetupStep } from './setup-stepper';

const benchmarkOptions = benchmarkModeValues.map((value) => ({
  value,
  label: benchmarkModeLabels[value],
}));

/**
 * The wizard's steps in order, with the form fields each validates. `fields`
 * drives both per-step validation (`trigger`) on Next and the jump-to-first-
 * error behavior on submit.
 */
const STEPS = [
  {
    id: 'brand',
    label: 'Brand',
    fields: ['brand_name', 'name', 'website_url', 'aliases'],
  },
  { id: 'market', label: 'Market', fields: ['country_code', 'language_code'] },
  { id: 'domains', label: 'Domains', fields: ['owned_domains', 'unintended_domains'] },
  { id: 'competitors', label: 'Competitors', fields: ['competitors'] },
  { id: 'defaults', label: 'Defaults', fields: ['benchmark_mode', 'default_repetitions'] },
] as const satisfies readonly (SetupStep & {
  fields: readonly (keyof SetupFormValues)[];
})[];

/** Index of the first step that owns a field with a validation error. */
function firstErrorStep(errors: FieldErrors<SetupFormValues>): number {
  const index = STEPS.findIndex((step) => step.fields.some((field) => field in errors));
  return index === -1 ? 0 : index;
}

/**
 * SetupForm (F6) — the Brand/Project setup wizard for both create and edit.
 *
 * A horizontal stepper (Brand → Market → Domains → Competitors → Defaults)
 * with a progress line; one step renders at a time and react-hook-form keeps
 * values across steps. Next validates only the current step's fields; the
 * final submit validates everything and jumps back to the first step with an
 * error.
 *
 * - **Create** (`project` undefined): POSTs a new project, sets it active via
 *   the F5 project context, and routes to `/visibility`.
 * - **Edit** (`project` provided): prefills from the existing project, every
 *   step is immediately reachable, and Save is available on any step.
 */
export function SetupForm({ project }: Readonly<{ project?: Project }>) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useProjectContext();
  const isEdit = Boolean(project);

  const {
    register,
    control,
    handleSubmit,
    getValues,
    trigger,
    formState: { errors, isSubmitting },
  } = useForm<SetupFormValues>({
    resolver: zodResolver(setupFormSchema),
    defaultValues: project ? projectToFormValues(project) : emptySetupForm,
  });

  const [step, setStep] = useState(0);
  // Furthest step reached: gates forward jumps in create mode; edit mode has
  // everything unlocked from the start.
  const [maxVisited, setMaxVisited] = useState(isEdit ? STEPS.length - 1 : 0);
  const isLast = step === STEPS.length - 1;
  const stepId = STEPS[step].id;

  const goTo = (index: number) => {
    setStep(index);
    setMaxVisited((max) => Math.max(max, index));
  };

  const onNext = async () => {
    const valid = await trigger([...STEPS[step].fields], { shouldFocus: true });
    if (valid) goTo(step + 1);
  };

  // Lifted here (one `useFieldArray` per name) so the AI suggestion handlers
  // can `append` — which preserves existing row ids, in-progress edits, and
  // focus, unlike a whole-array `setValue`/`replace`.
  const ownedDomainsArray = useFieldArray({ control, name: 'owned_domains' });
  const competitorsArray = useFieldArray({ control, name: 'competitors' });

  const [suggestOpen, setSuggestOpen] = useState<'competitors' | 'domains' | null>(null);
  const [suggestSummary, setSuggestSummary] = useState<string | null>(null);

  // The agent needs a brand to research; keep the buttons disabled until the
  // brand name is filled (the backend also 422s on an empty brand_name).
  const brandName = useWatch({ control, name: 'brand_name' }).trim();

  /** Brand context for the stateless suggestion endpoints, from live values. */
  const brandContext = () => {
    const values = getValues();
    const websiteUrl = values.website_url.trim();
    const countryCode = values.country_code.trim();
    const languageCode = values.language_code.trim();
    return {
      brand_name: values.brand_name.trim(),
      // Optional fields are omitted (not sent as '') when empty so the
      // suggestion endpoints only see fields that carry a value.
      ...(websiteUrl ? { website_url: websiteUrl } : {}),
      brand_aliases: values.aliases.map((a) => a.value.trim()).filter(Boolean),
      ...(countryCode ? { country_code: countryCode } : {}),
      ...(languageCode ? { language_code: languageCode } : {}),
      confirm_send_evidence: true as const,
    };
  };

  const suggestCompetitorsMutation = useMutation({
    mutationFn: (input: CompetitorSuggestInput) => projectsApi.suggestCompetitors(input),
    onSuccess: (result) => {
      const existing = new Set(getValues().competitors.map((c) => c.name.trim().toLowerCase()));
      let added = 0;
      for (const competitor of result.competitors) {
        const key = competitor.name.trim().toLowerCase();
        if (!key || existing.has(key)) continue;
        existing.add(key);
        competitorsArray.append({
          name: competitor.name,
          aliases: competitor.aliases.map((value) => ({ value })),
          domains: competitor.domains.map((value) => ({ value })),
        });
        added += 1;
      }
      const skipped = result.competitors.length - added;
      setSuggestSummary(
        `Added ${added} competitor${added === 1 ? '' : 's'} to the form for review` +
          (skipped > 0 ? `; ${skipped} duplicate${skipped === 1 ? '' : 's'} skipped` : '') +
          '.',
      );
    },
  });

  const suggestDomainsMutation = useMutation({
    mutationFn: (input: OwnedDomainSuggestInput) => projectsApi.suggestOwnedDomains(input),
    onSuccess: (result) => {
      const existing = new Set(getValues().owned_domains.map((d) => d.value.trim().toLowerCase()));
      let added = 0;
      for (const domain of result.domains) {
        const key = domain.trim().toLowerCase();
        if (!key || existing.has(key)) continue;
        existing.add(key);
        ownedDomainsArray.append({ value: domain });
        added += 1;
      }
      const skipped = result.domains.length - added;
      setSuggestSummary(
        `Added ${added} owned domain${added === 1 ? '' : 's'} to the form for review` +
          (skipped > 0 ? `; ${skipped} duplicate${skipped === 1 ? '' : 's'} skipped` : '') +
          '.',
      );
    },
  });

  const openSuggest = (kind: 'competitors' | 'domains') => {
    suggestCompetitorsMutation.reset();
    suggestDomainsMutation.reset();
    setSuggestSummary(null);
    setSuggestOpen(kind);
  };

  const runSuggest = async () => {
    if (suggestOpen === 'competitors') {
      await suggestCompetitorsMutation
        .mutateAsync({
          ...brandContext(),
          existing_competitor_names: getValues()
            .competitors.map((c) => c.name.trim())
            .filter(Boolean),
        })
        .catch(() => undefined);
    } else if (suggestOpen === 'domains') {
      await suggestDomainsMutation
        .mutateAsync({
          ...brandContext(),
          existing_owned_domains: getValues()
            .owned_domains.map((d) => d.value.trim())
            .filter(Boolean),
        })
        .catch(() => undefined);
    }
  };

  const mutation = useMutation({
    mutationFn: (input: ProjectInput) =>
      project ? projectsApi.updateProject(project.id, input) : projectsApi.createProject(input),
    onSuccess: async (saved: Project) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      if (project) {
        queryClient.setQueryData(queryKeys.projects.detail(saved.id), saved);
        return;
      }
      setActiveProjectId(saved.id);
      router.replace('/visibility');
    },
  });

  const onSubmit = handleSubmit(
    (values) => mutation.mutateAsync(formValuesToProjectInput(values)).catch(() => undefined),
    // Full-form validation failed: jump to the earliest step that owns an
    // erroring field so the message is on screen.
    (submitErrors) => setStep(firstErrorStep(submitErrors)),
  );

  return (
    <form noValidate onSubmit={onSubmit} className="grid gap-6">
      <SetupStepper steps={STEPS} current={step} maxVisited={maxVisited} onSelect={goTo} />

      {mutation.isError ? <Alert tone="danger">{setupErrorMessage(mutation.error)}</Alert> : null}
      {isEdit && mutation.isSuccess ? <Alert tone="success">Project saved.</Alert> : null}

      {stepId === 'brand' ? (
        <Card>
          <CardHeader>
            <CardTitle>Brand profile</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Brand name" required error={errors.brand_name?.message}>
                {(props) => (
                  <Input {...props} {...register('brand_name')} placeholder="Searchify" />
                )}
              </Field>
              <Field label="Project name" required error={errors.name?.message}>
                {(props) => <Input {...props} {...register('name')} placeholder="Searchify — US" />}
              </Field>
            </div>
            <Field
              label="Website URL"
              required
              error={errors.website_url?.message}
              hint="Full URL including https://"
            >
              {(props) => (
                <Input
                  {...props}
                  {...register('website_url')}
                  type="url"
                  placeholder="https://searchify.com"
                />
              )}
            </Field>
            <EntryList
              control={control}
              name="aliases"
              label="Brand aliases"
              placeholder="Searchify AI"
              addLabel="Add alias"
              errors={errors}
            />
          </CardContent>
        </Card>
      ) : null}

      {stepId === 'market' ? (
        <Card>
          <CardHeader>
            <CardTitle>Location &amp; language</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Country code"
              required
              error={errors.country_code?.message}
              hint="2-letter ISO code, e.g. US"
            >
              {(props) => (
                <Input {...props} {...register('country_code')} placeholder="US" maxLength={2} />
              )}
            </Field>
            <Field
              label="Language code"
              required
              error={errors.language_code?.message}
              hint="e.g. en or en-US"
            >
              {(props) => (
                <Input {...props} {...register('language_code')} placeholder="en" maxLength={5} />
              )}
            </Field>
          </CardContent>
        </Card>
      ) : null}

      {stepId === 'domains' ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Domains</CardTitle>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!brandName}
              onClick={() => openSuggest('domains')}
            >
              <Sparkles className="size-4" aria-hidden />
              Generate with AI
            </Button>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <EntryListView
              control={control}
              name="owned_domains"
              label="Owned domains"
              placeholder="searchify.com"
              addLabel="Add owned domain"
              errors={errors}
              fieldArray={ownedDomainsArray}
            />
            <EntryList
              control={control}
              name="unintended_domains"
              label="Unintended domains"
              placeholder="searchify.io"
              addLabel="Add unintended domain"
              errors={errors}
            />
          </CardContent>
        </Card>
      ) : null}

      {stepId === 'competitors' ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Competitors</CardTitle>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!brandName}
              onClick={() => openSuggest('competitors')}
            >
              <Sparkles className="size-4" aria-hidden />
              Generate with AI
            </Button>
          </CardHeader>
          <CardContent>
            <CompetitorRows
              control={control}
              register={register}
              errors={errors}
              fieldArray={competitorsArray}
            />
          </CardContent>
        </Card>
      ) : null}

      {stepId === 'defaults' ? (
        <Card>
          <CardHeader>
            <CardTitle>Audit defaults</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Field label="Benchmark mode" required error={errors.benchmark_mode?.message}>
              {(props) => (
                <Controller
                  control={control}
                  name="benchmark_mode"
                  render={({ field }) => (
                    <SegmentedControl
                      id={props.id}
                      aria-describedby={props['aria-describedby']}
                      ariaLabel="Benchmark mode"
                      value={field.value}
                      onChange={field.onChange}
                      options={benchmarkOptions}
                    />
                  )}
                />
              )}
            </Field>
            <Field
              label="Default repetitions"
              required
              error={errors.default_repetitions?.message}
              hint="How many times each prompt runs per engine (1–10)."
              className="max-w-[200px]"
            >
              {(props) => (
                <Input
                  {...props}
                  {...register('default_repetitions', { valueAsNumber: true })}
                  type="number"
                  min={1}
                  max={10}
                />
              )}
            </Field>
          </CardContent>
        </Card>
      ) : null}

      <GenerateBrandDialog
        open={suggestOpen !== null}
        onOpenChange={(next) => {
          if (!next) setSuggestOpen(null);
        }}
        title={suggestOpen === 'domains' ? 'Generate owned domains' : 'Generate competitors'}
        description={
          suggestOpen === 'domains'
            ? 'Searchify suggests domains your brand owns and operates. Suggestions are added to the form for review — nothing is saved until you save the project.'
            : 'Searchify suggests competitors from your brand profile. Suggestions are added to the form for review — nothing is saved until you save the project.'
        }
        onGenerate={runSuggest}
        isGenerating={suggestCompetitorsMutation.isPending || suggestDomainsMutation.isPending}
        error={
          suggestOpen === 'domains'
            ? suggestDomainsMutation.error
            : suggestCompetitorsMutation.error
        }
        resultSummary={suggestSummary}
      />

      <div className="flex items-center justify-between gap-2">
        <div>
          {step > 0 ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => setStep(step - 1)}
              disabled={isSubmitting || mutation.isPending}
            >
              Back
            </Button>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {/* Edit mode: saving is allowed from any step (full-form validation
              still runs; an error jumps to its step). */}
          {isEdit && !isLast ? (
            <Button type="submit" variant="secondary" disabled={isSubmitting || mutation.isPending}>
              {mutation.isPending || isSubmitting ? 'Saving…' : 'Save changes'}
            </Button>
          ) : null}
          {isLast ? (
            <Button type="submit" disabled={isSubmitting || mutation.isPending}>
              {mutation.isPending || isSubmitting
                ? isEdit
                  ? 'Saving…'
                  : 'Creating…'
                : isEdit
                  ? 'Save changes'
                  : 'Create project'}
            </Button>
          ) : (
            <Button type="button" onClick={() => void onNext()}>
              Next
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}
