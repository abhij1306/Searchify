'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { Controller, useForm } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { projectsApi, type ProjectInput } from '@/lib/api/projects';
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
import { EntryList } from './entry-list';
import { SegmentedControl } from './segmented-control';

const benchmarkOptions = benchmarkModeValues.map((value) => ({
  value,
  label: benchmarkModeLabels[value],
}));

/**
 * SetupForm (F6) — the Brand/Project setup form for both create and edit.
 *
 * - **Create** (`project` undefined): POSTs a new project, sets it active via
 *   the F5 project context, and routes to `/visibility`.
 * - **Edit** (`project` provided): prefills from the existing project and
 *   PATCHes; on success it invalidates the project caches and stays put.
 *
 * react-hook-form + zod validate every field including the repeatable
 * competitor / domain / alias rows. UI is composed from F3 primitives with
 * bridged tokens only.
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
    formState: { errors, isSubmitting },
  } = useForm<SetupFormValues>({
    resolver: zodResolver(setupFormSchema),
    defaultValues: project ? projectToFormValues(project) : emptySetupForm,
  });

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

  const onSubmit = handleSubmit((values) =>
    mutation.mutateAsync(formValuesToProjectInput(values)).catch(() => undefined),
  );

  return (
    <form noValidate onSubmit={onSubmit} className="grid gap-6 pb-24">
      {mutation.isError ? <Alert tone="danger">{setupErrorMessage(mutation.error)}</Alert> : null}
      {isEdit && mutation.isSuccess ? <Alert tone="success">Project saved.</Alert> : null}

      <Card>
        <CardHeader>
          <CardTitle>Brand profile</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Brand name" required error={errors.brand_name?.message}>
              {(props) => <Input {...props} {...register('brand_name')} placeholder="Searchify" />}
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

      <Card>
        <CardHeader>
          <CardTitle>Domains</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <EntryList
            control={control}
            name="owned_domains"
            label="Owned domains"
            placeholder="searchify.com"
            addLabel="Add owned domain"
            errors={errors}
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

      <Card>
        <CardHeader>
          <CardTitle>Competitors</CardTitle>
        </CardHeader>
        <CardContent>
          <CompetitorRows control={control} register={register} errors={errors} />
        </CardContent>
      </Card>

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

      <div className="border-border bg-panel/95 fixed inset-x-0 bottom-0 z-20 border-t px-4 py-3 backdrop-blur sm:px-8">
        <div className="mx-auto flex max-w-3xl items-center justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => router.back()}
            disabled={isSubmitting || mutation.isPending}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={isSubmitting || mutation.isPending}>
            {mutation.isPending || isSubmitting
              ? isEdit
                ? 'Saving…'
                : 'Creating…'
              : isEdit
                ? 'Save changes'
                : 'Create project'}
          </Button>
        </div>
      </div>
    </form>
  );
}
