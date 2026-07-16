'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { promptsApi } from '@/lib/api/prompts';
import { queryKeys } from '@/lib/api/query-keys';
import type { PromptSet } from '@/lib/api/types';
import { useActiveProject } from '@/lib/project/project-context';

/**
 * Resolve the active project's prompt set (F7).
 *
 * Prompts are scoped to a `PromptSet` under the active project (F5 context).
 * The active project may already embed a prompt set (`project.prompt_sets`);
 * otherwise we list them and, if none exists, expose an idempotent
 * `ensurePromptSet` that creates a default one through the API. The list query
 * is the source of truth once loaded so a freshly-created set is picked up.
 */
export function usePromptSet() {
  const project = useActiveProject();
  const projectId = project?.id ?? null;
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: projectId ? queryKeys.prompts.sets(projectId) : ['prompts', 'sets', 'none'],
    queryFn: ({ signal }) => promptsApi.listPromptSets(projectId as string, { signal }),
    enabled: Boolean(projectId),
    // Seed from the embedded prompt sets so the table can render immediately.
    initialData: project?.prompt_sets?.length ? project.prompt_sets : undefined,
  });

  const sets = listQuery.data ?? [];
  const promptSet: PromptSet | null = sets[0] ?? null;

  const createMutation = useMutation({
    mutationFn: () =>
      promptsApi.createPromptSet({
        project_id: projectId as string,
        name: 'Default prompt set',
      }),
    onSuccess: async (created) => {
      if (projectId) {
        queryClient.setQueryData<PromptSet[]>(queryKeys.prompts.sets(projectId), (prev) =>
          prev && prev.length ? prev : [created],
        );
        await queryClient.invalidateQueries({ queryKey: queryKeys.prompts.sets(projectId) });
      }
    },
  });

  const ensurePromptSet = useCallback(async (): Promise<PromptSet> => {
    if (promptSet) return promptSet;
    return createMutation.mutateAsync();
  }, [promptSet, createMutation]);

  return {
    projectId,
    promptSet,
    prompts: promptSet?.prompts ?? [],
    isLoading: Boolean(projectId) && listQuery.isLoading,
    isError: listQuery.isError,
    ensurePromptSet,
    isEnsuring: createMutation.isPending,
  };
}
