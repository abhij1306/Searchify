'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Save, Sparkles } from 'lucide-react';
import { useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input, Textarea } from '@/components/ui/input';
import {
  projectsApi,
  type BrandProfileField,
  type BrandProfileUpdateInput,
} from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import type { BrandProfile, BrandProfileDraft, BrandProfileSuggestion } from '@/lib/api/types';
import { setupErrorMessage } from '@/lib/setup/forms';

import { GenerateBrandDialog } from './generate-brand-dialog';

const profileFields = [
  'description',
  'positioning',
  'products_services',
  'target_audience',
] as const satisfies readonly BrandProfileField[];

function profileDraft(profile: BrandProfile): BrandProfileDraft {
  return {
    description: profile.description,
    positioning: profile.positioning,
    products_services: profile.products_services,
    target_audience: profile.target_audience,
  };
}

function sameValue(
  left: BrandProfileDraft[BrandProfileField],
  right: BrandProfileDraft[BrandProfileField],
) {
  return Array.isArray(left) && Array.isArray(right)
    ? JSON.stringify(left) === JSON.stringify(right)
    : left === right;
}

function hasValue(value: BrandProfileDraft[BrandProfileField]) {
  return Array.isArray(value) ? value.length > 0 : value.trim().length > 0;
}

function parseProductsInput(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function BrandProfilePanel({
  projectId,
  profile,
}: Readonly<{
  projectId: string;
  profile: BrandProfile;
}>) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<BrandProfileDraft>(() => profileDraft(profile));
  const [productsInput, setProductsInput] = useState(() =>
    profile.products_services.join(', '),
  );
  const [suggestion, setSuggestion] = useState<BrandProfileSuggestion | null>(null);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const storeProfile = (next: BrandProfile) => {
    queryClient.setQueryData(queryKeys.projects.brandProfile(projectId), next);
    setDraft(profileDraft(next));
    setProductsInput(next.products_services.join(', '));
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      projectsApi.updateBrandProfile(projectId, {
        ...draft,
        products_services: parseProductsInput(productsInput),
      }),
    onSuccess: (next) => {
      storeProfile(next);
      setSuggestion(null);
      setNotice('Brand knowledge saved. These details now inform assisted features.');
    },
  });

  const suggestMutation = useMutation({
    mutationFn: () => projectsApi.suggestBrandProfile(projectId),
    onSuccess: (next) => {
      const mergedDraft: BrandProfileDraft = {
        description: next.draft.description || draft.description,
        positioning: next.draft.positioning || draft.positioning,
        products_services:
          next.draft.products_services.length > 0
            ? next.draft.products_services
            : parseProductsInput(productsInput),
        target_audience: next.draft.target_audience || draft.target_audience,
      };
      setSuggestion(next);
      setDraft(mergedDraft);
      setProductsInput(mergedDraft.products_services.join(', '));
      setSuggestOpen(false);
      setNotice('AI draft loaded for review. Edit anything before applying it.');
    },
  });

  const acceptMutation = useMutation({
    mutationFn: () => {
      if (!suggestion) throw new Error('No AI draft is awaiting review.');
      const currentDraft: BrandProfileDraft = {
        ...draft,
        products_services: parseProductsInput(productsInput),
      };
      const acceptedFields: BrandProfileField[] = [];
      const manualOverrides: BrandProfileUpdateInput = {};
      for (const field of profileFields) {
        const current = currentDraft[field];
        const suggested = suggestion.draft[field];
        if (sameValue(current, suggested) && hasValue(current)) {
          acceptedFields.push(field);
        } else {
          Object.assign(manualOverrides, { [field]: current });
        }
      }
      return projectsApi.acceptBrandProfileSuggestion(projectId, suggestion.id, {
        accepted_fields: acceptedFields,
        manual_overrides: manualOverrides,
      });
    },
    onSuccess: (result) => {
      storeProfile(result.profile);
      setSuggestion(null);
      setNotice(
        result.skipped_manual_fields.length > 0
          ? `Applied the draft; preserved manual fields: ${result.skipped_manual_fields.join(', ')}.`
          : 'Reviewed AI draft applied to the brand knowledge base.',
      );
    },
  });

  const pendingError = saveMutation.error ?? acceptMutation.error;

  return (
    <Card aria-labelledby="brand-knowledge-title">
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="grid gap-1">
          <CardTitle id="brand-knowledge-title">Brand knowledge</CardTitle>
          <p className="text-secondary text-sm">
            Curated positioning and audience context used by competitor and prompt generation.
          </p>
        </div>
        <Button variant="secondary" onClick={() => setSuggestOpen(true)}>
          <Sparkles className="size-4" aria-hidden />
          Draft with AI
        </Button>
      </CardHeader>
      <CardContent className="grid gap-4">
        {pendingError ? <Alert tone="danger">{setupErrorMessage(pendingError)}</Alert> : null}
        {notice ? <Alert tone="success">{notice}</Alert> : null}
        {suggestion ? (
          <Alert tone="info">
            Reviewing AI draft from {suggestion.model_identity.transport_model ?? 'default agent'}.
            Unchanged fields retain AI provenance; edits are saved as manual.
          </Alert>
        ) : null}

        <Field label="Description">
          {(field) => (
            <Textarea
              {...field}
              value={draft.description}
              onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))}
            />
          )}
        </Field>
        <Field label="Positioning" hint="Include price tier, differentiation, and competitive segment.">
          {(field) => (
            <Textarea
              {...field}
              value={draft.positioning}
              onChange={(event) => setDraft((value) => ({ ...value, positioning: event.target.value }))}
            />
          )}
        </Field>
        <Field label="Products and services" hint="Comma-separated category labels.">
          {(field) => (
            <Input
              {...field}
              value={productsInput}
              onChange={(event) => setProductsInput(event.target.value)}
            />
          )}
        </Field>
        <Field label="Target audience">
          {(field) => (
            <Textarea
              {...field}
              value={draft.target_audience}
              onChange={(event) =>
                setDraft((value) => ({ ...value, target_audience: event.target.value }))
              }
            />
          )}
        </Field>

        <div className="flex justify-end gap-2">
          {suggestion ? (
            <>
              <Button
                variant="ghost"
                onClick={() => {
                  setSuggestion(null);
                  setDraft(profileDraft(profile));
                  setProductsInput(profile.products_services.join(', '));
                  setNotice(null);
                }}
              >
                Discard draft
              </Button>
              <Button
                variant="primary"
                onClick={() => acceptMutation.mutate()}
                disabled={acceptMutation.isPending}
              >
                <Sparkles className="size-4" aria-hidden />
                {acceptMutation.isPending ? 'Applying…' : 'Apply reviewed draft'}
              </Button>
            </>
          ) : (
            <Button
              variant="primary"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
            >
              <Save className="size-4" aria-hidden />
              {saveMutation.isPending ? 'Saving…' : 'Save brand knowledge'}
            </Button>
          )}
        </div>
      </CardContent>

      <GenerateBrandDialog
        open={suggestOpen}
        onOpenChange={setSuggestOpen}
        title="Draft brand knowledge with AI"
        description="The default agent will draft positioning, products, and audience context for you to review. Nothing is applied automatically."
        onGenerate={async () => {
          await suggestMutation.mutateAsync();
        }}
        isGenerating={suggestMutation.isPending}
        error={suggestMutation.error}
      />
    </Card>
  );
}
