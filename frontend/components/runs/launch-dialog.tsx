'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input, inputClasses } from '@/components/ui/input';
import { promptsApi } from '@/lib/api/prompts';
import { providersApi } from '@/lib/api/providers';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import type { Audit, LogicalEngine } from '@/lib/api/types';
import { ENGINE_LABELS, ENGINE_ORDER } from '@/lib/providers/catalog';
import {
  buildLaunchPayload,
  canLaunch,
  clampRepetitions,
  DEFAULT_REPETITIONS,
  MAX_REPETITIONS,
  MIN_REPETITIONS,
  toggleEngine,
} from '@/lib/runs/launch';

import { filterChipClasses } from './filter-chip';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Could not launch the run. Please try again.';
}

/**
 * Launch-audit dialog (F10, design.md §9.7).
 *
 * Collects the run configuration — a prompt set, one or more logical engines
 * (only those with a configured BYOK provider route are selectable), and a
 * repetition count — and posts it to `POST /audits` via `buildLaunchPayload`.
 * The active project comes from F5 context (passed in as `projectId`). On a
 * successful launch it invalidates the runs list and hands the new audit back
 * to the parent (which routes into the run detail page).
 */
export function LaunchDialog({
  open,
  onOpenChange,
  projectId,
  onLaunched,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onLaunched?: (audit: Audit) => void;
}>) {
  const queryClient = useQueryClient();

  const promptSetsQuery = useQuery({
    queryKey: queryKeys.prompts.sets(projectId),
    queryFn: ({ signal }) => promptsApi.listPromptSets(projectId, { signal }),
    enabled: open,
  });

  const connectionsQuery = useQuery({
    queryKey: queryKeys.providers.connections(),
    queryFn: ({ signal }) => providersApi.listConnections({ signal }),
    enabled: open,
  });

  const promptSets = promptSetsQuery.data ?? [];
  const connections = connectionsQuery.data;

  // A logical engine is selectable only when a BYOK connection with a stored
  // key backs a route for it (the backend rejects a launch otherwise).
  const configuredEngines = useMemo<LogicalEngine[]>(() => {
    const configured = new Set<LogicalEngine>();
    for (const connection of connections ?? []) {
      if (!connection.api_key_set) continue;
      for (const route of connection.routes ?? []) {
        configured.add(route.logical_engine);
      }
    }
    return ENGINE_ORDER.filter((engine) => configured.has(engine));
  }, [connections]);

  const [promptSetId, setPromptSetId] = useState<string | null>(null);
  const [engines, setEngines] = useState<LogicalEngine[]>([]);
  const [repetitions, setRepetitions] = useState(DEFAULT_REPETITIONS);

  // Resolve the effective prompt set: the explicit selection, else the first.
  const effectivePromptSetId = promptSetId ?? promptSets[0]?.id ?? null;

  const selection = {
    projectId,
    promptSetId: effectivePromptSetId,
    engines,
    repetitions,
  };
  const ready = canLaunch(selection);

  const launchMutation = useMutation({
    mutationFn: () => runsApi.launchAudit(buildLaunchPayload(selection)),
    onSuccess: async (audit) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
      onOpenChange(false);
      setEngines([]);
      setPromptSetId(null);
      setRepetitions(DEFAULT_REPETITIONS);
      onLaunched?.(audit);
    },
  });

  const noPromptSets = !promptSetsQuery.isLoading && promptSets.length === 0;
  const noEngines = !connectionsQuery.isLoading && configuredEngines.length === 0;
  // Set lookup: `selected` is computed per engine chip in the render loop.
  const selectedEngines = new Set(engines);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Launch an audit"
      description="Run your prompts across the selected AI engines and measure your brand's visibility."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => launchMutation.mutate()}
            disabled={!ready || launchMutation.isPending}
          >
            {launchMutation.isPending ? 'Launching…' : 'Launch audit'}
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        {launchMutation.isError ? (
          <Alert tone="danger">{errorMessage(launchMutation.error)}</Alert>
        ) : null}

        <Field label="Prompt set" required>
          {(props) =>
            noPromptSets ? (
              <p className="text-muted text-sm">
                No prompt set yet. Add prompts on the Prompts screen first.
              </p>
            ) : (
              <select
                {...props}
                className={inputClasses}
                value={effectivePromptSetId ?? ''}
                onChange={(event) => setPromptSetId(event.target.value)}
              >
                {promptSets.map((set) => (
                  <option key={set.id} value={set.id}>
                    {set.name}
                    {typeof set.prompt_count === 'number' ? ` (${set.prompt_count})` : ''}
                  </option>
                ))}
              </select>
            )
          }
        </Field>

        <fieldset className="grid gap-2">
          <legend className="text-secondary text-xs font-medium">
            Engines <span className="text-danger">*</span>
          </legend>
          {noEngines ? (
            <p className="text-muted text-sm">
              No configured engines. Add a provider key on the Providers screen first.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {configuredEngines.map((engine) => {
                const selected = selectedEngines.has(engine);
                return (
                  <button
                    key={engine}
                    type="button"
                    role="checkbox"
                    aria-checked={selected}
                    onClick={() => setEngines((prev) => toggleEngine(prev, engine))}
                    className={filterChipClasses(selected)}
                  >
                    {ENGINE_LABELS[engine]}
                  </button>
                );
              })}
            </div>
          )}
        </fieldset>

        <Field
          label="Repetitions"
          hint={`How many times to run each prompt per engine (${MIN_REPETITIONS}–${MAX_REPETITIONS}).`}
        >
          {(props) => (
            <Input
              {...props}
              type="number"
              min={MIN_REPETITIONS}
              max={MAX_REPETITIONS}
              value={repetitions}
              onChange={(event) => setRepetitions(Number(event.target.value))}
              onBlur={() => setRepetitions((prev) => clampRepetitions(prev))}
              className="w-28"
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
