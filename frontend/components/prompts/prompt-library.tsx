'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { promptsApi, type PromptInput } from '@/lib/api/prompts';
import { queryKeys } from '@/lib/api/query-keys';
import type { Prompt, PromptSet } from '@/lib/api/types';
import { emptyFilters, filterPrompts, type PromptFilters } from '@/lib/prompts/filter';
import { usePromptSet } from '@/lib/prompts/use-prompt-set';

import { AiSuggestPanel } from './ai-suggest-panel';
import { CsvImportDialog } from './csv-import-dialog';
import { PromptEmptyState } from './prompt-empty-state';
import { PromptFormDialog } from './prompt-form-dialog';
import { PromptTable } from './prompt-table';
import { PromptToolbar } from './prompt-toolbar';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Prompt library client (F7). Owns the active prompt set (via F5 project
 * context), the search/filter state, and every CRUD/import mutation. Renders
 * the toolbar, table (or empty state), the CSV import + add/edit dialogs, and
 * the AI-suggest coming-soon panel.
 */
export function PromptLibrary() {
  const queryClient = useQueryClient();
  const { projectId, promptSet, prompts, isLoading, isError, ensurePromptSet } = usePromptSet();

  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<PromptFilters>(emptyFilters);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Prompt | undefined>(undefined);
  const [importOpen, setImportOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const invalidate = async () => {
    if (projectId) await queryClient.invalidateQueries({ queryKey: queryKeys.prompts.sets(projectId) });
    if (promptSet) await queryClient.invalidateQueries({ queryKey: queryKeys.prompts.set(promptSet.id) });
  };

  const createMutation = useMutation({
    mutationFn: async (input: PromptInput) => {
      const set = await ensurePromptSet();
      return promptsApi.createPrompt(set.id, input);
    },
    onSuccess: async () => {
      await invalidate();
      setFormOpen(false);
      setEditing(undefined);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (vars: { id: string; input: Partial<PromptInput> }) =>
      promptsApi.updatePrompt(vars.id, vars.input),
    onSuccess: async () => {
      await invalidate();
      setFormOpen(false);
      setEditing(undefined);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => promptsApi.deletePrompt(id),
    onSettled: () => setBusyId(null),
    onSuccess: invalidate,
  });

  const toggleMutation = useMutation({
    mutationFn: (prompt: Prompt) =>
      promptsApi.updatePrompt(prompt.id, { enabled: !prompt.enabled }),
    onSettled: () => setBusyId(null),
    onSuccess: invalidate,
  });

  const importMutation = useMutation({
    mutationFn: async (rows: PromptInput[]): Promise<PromptSet> => {
      const set = await ensurePromptSet();
      return promptsApi.importRows(set.id, rows);
    },
    onSuccess: async () => {
      await invalidate();
      setImportOpen(false);
    },
  });

  const visible = useMemo(() => filterPrompts(prompts, search, filters), [prompts, search, filters]);
  const hasPrompts = prompts.length > 0;

  const openAdd = () => {
    setEditing(undefined);
    setFormOpen(true);
  };
  const openEdit = (prompt: Prompt) => {
    setEditing(prompt);
    setFormOpen(true);
  };
  const submitForm = async (input: PromptInput) => {
    if (editing) await updateMutation.mutateAsync({ id: editing.id, input }).catch(() => undefined);
    else await createMutation.mutateAsync(input).catch(() => undefined);
  };

  if (!projectId) {
    return (
      <Alert tone="info">
        Select or create a project first — prompts belong to a project&apos;s prompt set.
      </Alert>
    );
  }

  if (isLoading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      {isError ? (
        <Alert tone="danger">Could not load prompts. Check your connection and try again.</Alert>
      ) : null}

      <PromptToolbar
        search={search}
        onSearchChange={setSearch}
        filters={filters}
        onFiltersChange={setFilters}
        onImport={() => setImportOpen(true)}
        onAdd={openAdd}
      />

      <AiSuggestPanel />

      {!hasPrompts ? (
        <PromptEmptyState onAdd={openAdd} onImport={() => setImportOpen(true)} />
      ) : visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-panel px-6 py-12 text-center text-sm text-secondary">
          No prompts match your search or filters.
        </div>
      ) : (
        <PromptTable
          prompts={visible}
          onEdit={openEdit}
          onDelete={(prompt) => {
            setBusyId(prompt.id);
            deleteMutation.mutate(prompt.id);
          }}
          onToggleEnabled={(prompt) => {
            setBusyId(prompt.id);
            toggleMutation.mutate(prompt);
          }}
          busyId={busyId}
        />
      )}

      <PromptFormDialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditing(undefined);
        }}
        prompt={editing}
        onSubmit={submitForm}
        isSaving={createMutation.isPending || updateMutation.isPending}
        error={
          createMutation.isError
            ? errorMessage(createMutation.error)
            : updateMutation.isError
              ? errorMessage(updateMutation.error)
              : undefined
        }
      />

      <CsvImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImport={async (rows) => {
          await importMutation.mutateAsync(rows).catch(() => undefined);
        }}
        isImporting={importMutation.isPending}
        error={importMutation.isError ? errorMessage(importMutation.error) : undefined}
      />
    </div>
  );
}
