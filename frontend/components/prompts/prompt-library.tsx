'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  promptsApi,
  type PromptGenerateInput,
  type PromptInput,
} from '@/lib/api/prompts';
import { queryKeys } from '@/lib/api/query-keys';
import { topicsApi } from '@/lib/api/topics';
import type {
  Prompt,
  PromptGenerateResponse,
  PromptSet,
  PromptStatus,
  Topic,
} from '@/lib/api/types';
import { emptyFilters, filterPrompts, type PromptFilters } from '@/lib/prompts/filter';
import { usePromptSet } from '@/lib/prompts/use-prompt-set';
import { cn } from '@/lib/utils';

import { CsvImportDialog } from './csv-import-dialog';
import { GeneratePromptsDialog } from './generate-prompts-dialog';
import { PromptEmptyState } from './prompt-empty-state';
import { PromptFormDialog } from './prompt-form-dialog';
import { PromptTable } from './prompt-table';
import { PromptToolbar } from './prompt-toolbar';
import { TopicRail } from './topic-rail';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

const STATUS_TABS: { id: PromptStatus; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'proposed', label: 'Proposed' },
  { id: 'archived', label: 'Archived' },
];

/**
 * Prompt library client (F7). Owns the active prompt set (via F5 project
 * context), the topic/status/search filter state, and every CRUD, import,
 * review, and AI-generation mutation. Layout: topics rail on the left;
 * Active / Proposed / Archived status tabs over the prompt table on the
 * right; "Generate prompts & topics" opens the consent-gated AI dialog.
 */
export function PromptLibrary() {
  const queryClient = useQueryClient();
  const { projectId, promptSet, prompts, isLoading, isError, ensurePromptSet } = usePromptSet();

  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<PromptFilters>(emptyFilters);
  const [statusTab, setStatusTab] = useState<PromptStatus>('active');
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Prompt | undefined>(undefined);
  const [importOpen, setImportOpen] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateResult, setGenerateResult] = useState<PromptGenerateResponse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const topicsQuery = useQuery({
    queryKey: projectId ? queryKeys.topics.list(projectId) : ['topics', 'list', 'none'],
    queryFn: ({ signal }) => topicsApi.list(projectId as string, { signal }),
    enabled: Boolean(projectId),
  });
  const topics: Topic[] = useMemo(() => topicsQuery.data ?? [], [topicsQuery.data]);

  const invalidate = async () => {
    if (projectId) {
      await queryClient.invalidateQueries({ queryKey: queryKeys.prompts.sets(projectId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.topics.list(projectId) });
    }
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

  const statusMutation = useMutation({
    mutationFn: (vars: { prompt: Prompt; status: PromptStatus }) =>
      promptsApi.updatePrompt(vars.prompt.id, { status: vars.status }),
    onSettled: () => setBusyId(null),
    onSuccess: invalidate,
  });

  const bulkStatusMutation = useMutation({
    mutationFn: (vars: { promptIds: string[]; status: PromptStatus }) =>
      promptsApi.bulkStatus(promptSet?.id as string, {
        prompt_ids: vars.promptIds,
        status: vars.status,
      }),
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

  const generateMutation = useMutation({
    mutationFn: async (input: PromptGenerateInput) => {
      const set = await ensurePromptSet();
      return promptsApi.generate(set.id, input);
    },
    onSuccess: async (result) => {
      setGenerateResult(result);
      // Generated prompts fill the set-wide active pool first (backend
      // auto-promotes up to a threshold), so a run can land rows in either
      // Active or Proposed. Switch to a tab that actually contains new rows —
      // prefer Proposed when it has any, otherwise Active — so the user never
      // lands on an empty tab after generating.
      const hasProposed = result.generated.some((prompt) => prompt.status === 'proposed');
      const hasActive = result.generated.some((prompt) => prompt.status === 'active');
      if (hasProposed) setStatusTab('proposed');
      else if (hasActive) setStatusTab('active');
      await invalidate();
    },
  });

  const createTopicMutation = useMutation({
    mutationFn: (name: string) => topicsApi.create(projectId as string, { name }),
    onSuccess: invalidate,
  });

  const deleteTopicMutation = useMutation({
    mutationFn: (topic: Topic) => topicsApi.remove(topic.id),
    onSuccess: async (_data, topic) => {
      if (selectedTopicId === topic.id) setSelectedTopicId(null);
      await invalidate();
    },
  });

  // Status tab -> topic -> search/filters, preserving order.
  const byStatus = useMemo(
    () => prompts.filter((prompt) => prompt.status === statusTab),
    [prompts, statusTab],
  );
  const byTopic = useMemo(
    () =>
      selectedTopicId === null
        ? byStatus
        : byStatus.filter((prompt) => prompt.topic_id === selectedTopicId),
    [byStatus, selectedTopicId],
  );
  const visible = useMemo(() => filterPrompts(byTopic, search, filters), [byTopic, search, filters]);

  const statusCounts = useMemo(() => {
    const counts: Record<PromptStatus, number> = { active: 0, proposed: 0, archived: 0 };
    for (const prompt of prompts) counts[prompt.status] += 1;
    return counts;
  }, [prompts]);

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

      <div className="grid items-start gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
        <TopicRail
          topics={topics}
          selectedTopicId={selectedTopicId}
          onSelect={setSelectedTopicId}
          onCreate={async (name) => {
            await createTopicMutation.mutateAsync(name);
          }}
          onDelete={(topic) => deleteTopicMutation.mutate(topic)}
          isCreating={createTopicMutation.isPending}
          loadError={topicsQuery.isError}
          actionError={
            createTopicMutation.isError
              ? errorMessage(createTopicMutation.error)
              : deleteTopicMutation.isError
                ? errorMessage(deleteTopicMutation.error)
                : null
          }
        />

        <div className="grid min-w-0 content-start gap-3 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b-2 border-border">
            <div role="tablist" aria-label="Prompt status" className="flex gap-0">
              {STATUS_TABS.map((tab) => {
                const selected = tab.id === statusTab;
                const count = statusCounts[tab.id];
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    onClick={() => setStatusTab(tab.id)}
                    className={cn(
                      'focus-ring -mb-0.5 border-b-2 px-4 py-2 text-sm font-medium transition-colors',
                      selected
                        ? 'border-accent font-semibold text-foreground'
                        : 'border-transparent text-secondary hover:text-foreground',
                    )}
                  >
                    {tab.label}
                    {count > 0 ? <span className="ml-1 text-xs text-muted">({count})</span> : null}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2 pb-1">
              {statusTab === 'proposed' && visible.length > 0 ? (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={bulkStatusMutation.isPending}
                  onClick={() =>
                    bulkStatusMutation.mutate({
                      promptIds: visible.map((prompt) => prompt.id),
                      status: 'active',
                    })
                  }
                >
                  Accept all
                </Button>
              ) : null}
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setGenerateResult(null);
                  generateMutation.reset();
                  setGenerateOpen(true);
                }}
              >
                <Sparkles className="size-4" aria-hidden />
                Generate prompts & topics
              </Button>
            </div>
          </div>

          {bulkStatusMutation.isError ? (
            <Alert tone="danger">{errorMessage(bulkStatusMutation.error)}</Alert>
          ) : null}

          {!hasPrompts ? (
            <PromptEmptyState onAdd={openAdd} onImport={() => setImportOpen(true)} />
          ) : visible.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border bg-panel px-6 py-12 text-center text-sm text-secondary">
              {statusTab === 'proposed'
                ? 'No proposed prompts. Use "Generate prompts & topics" to draft suggestions.'
                : 'No prompts match your search or filters.'}
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
              onSetStatus={(prompt, status) => {
                setBusyId(prompt.id);
                statusMutation.mutate({ prompt, status });
              }}
              busyId={busyId}
            />
          )}
        </div>
      </div>

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

      <GeneratePromptsDialog
        open={generateOpen}
        onOpenChange={setGenerateOpen}
        topics={topics}
        defaultTopicId={selectedTopicId}
        onGenerate={async (input) => {
          await generateMutation.mutateAsync(input).catch(() => undefined);
        }}
        isGenerating={generateMutation.isPending}
        error={generateMutation.isError ? generateMutation.error : undefined}
        result={generateResult}
      />
    </div>
  );
}
