'use client';

import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import Link from 'next/link';
import { Fragment, useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { buttonVariants } from '@/components/ui/button-variants';
import { Input } from '@/components/ui/input';
import { scoreBand, scoreBandText } from '@/components/ui/score-band';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { queryKeys } from '@/lib/api/query-keys';
import { topicsApi } from '@/lib/api/topics';
import { visibilityApi } from '@/lib/api/visibility';
import type { Prompt, Topic, VisibilityExecutionEvidence } from '@/lib/api/types';
import { useActiveProject } from '@/lib/project/project-context';
import { usePromptSet } from '@/lib/prompts/use-prompt-set';
import { cn } from '@/lib/utils';

/**
 * Per-prompt measured visibility: the share of persisted executions for the
 * prompt where the brand was mentioned (design.md §9.4 Visibility Score
 * column). Derived read-only from the evidence projection — no provider call.
 * Prompts with no completed executions yet score `null` (rendered as an
 * em-dash), never 0.
 */
function promptScores(items: VisibilityExecutionEvidence[]): Map<string, number> {
  const totals = new Map<string, { runs: number; mentioned: number }>();
  for (const item of items) {
    if (!item.prompt_id) continue;
    const entry = totals.get(item.prompt_id) ?? { runs: 0, mentioned: 0 };
    entry.runs += 1;
    if (item.mentions.some((mention) => mention.kind === 'brand')) entry.mentioned += 1;
    totals.set(item.prompt_id, entry);
  }
  const scores = new Map<string, number>();
  for (const [promptId, { runs, mentioned }] of totals) {
    if (runs > 0) scores.set(promptId, Math.round((mentioned / runs) * 100));
  }
  return scores;
}

function ScoreCell({ score }: Readonly<{ score: number | null }>) {
  if (score === null) return <span className="text-subtle">—</span>;
  return (
    <span
      className={cn(
        'font-mono text-sm font-semibold tabular-nums',
        scoreBandText[scoreBand(score)],
      )}
    >
      {score}%
    </span>
  );
}

type TopicGroup = {
  key: string;
  topic: Topic | null;
  prompts: Prompt[];
  /** Mean of the group's measured prompt scores; null until any prompt has data. */
  score: number | null;
};

function groupByTopic(
  prompts: Prompt[],
  topics: Topic[],
  scores: Map<string, number>,
): TopicGroup[] {
  const byTopicId = new Map<string | null, Prompt[]>();
  for (const prompt of prompts) {
    const key = prompt.topic_id ?? null;
    byTopicId.set(key, [...(byTopicId.get(key) ?? []), prompt]);
  }
  const groups: TopicGroup[] = [];
  for (const topic of topics) {
    const grouped = byTopicId.get(topic.id) ?? [];
    if (grouped.length > 0) groups.push({ key: topic.id, topic, prompts: grouped, score: null });
  }
  // Ungrouped = prompts with no topic_id PLUS prompts whose topic_id has no
  // matching topic (e.g. a deleted topic) — never silently dropped.
  const knownTopicIds = new Set(topics.map((topic) => topic.id));
  const ungrouped = [...(byTopicId.get(null) ?? [])];
  for (const [topicId, bucket] of byTopicId) {
    if (topicId !== null && !knownTopicIds.has(topicId)) ungrouped.push(...bucket);
  }
  if (ungrouped.length > 0)
    groups.push({ key: 'ungrouped', topic: null, prompts: ungrouped, score: null });
  for (const group of groups) {
    const measured = group.prompts
      .map((prompt) => scores.get(prompt.id))
      .filter((score): score is number => score !== undefined);
    group.score = measured.length
      ? Math.round(measured.reduce((sum, score) => sum + score, 0) / measured.length)
      : null;
  }
  return groups;
}

/**
 * Your Prompts (design.md §9.4, sidebar "Your Prompts"): the read-only,
 * score-annotated view of the ACTIVE prompt configuration, grouped by topic
 * with expandable rows. Editing, review (proposed/archived), and AI generation
 * live on `/prompt-research` — the banner links there.
 */
export function YourPrompts() {
  const project = useActiveProject();
  const { projectId, prompts, isLoading, isError } = usePromptSet();
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const topicsQuery = useQuery({
    queryKey: projectId ? queryKeys.topics.list(projectId) : ['topics', 'list', 'none'],
    queryFn: ({ signal }) => topicsApi.list(projectId as string, { signal }),
    enabled: Boolean(projectId),
  });

  // Latest-audit evidence window; a project with no completed audits returns
  // an empty list and every score renders as an em-dash.
  const evidenceQuery = useQuery({
    queryKey: projectId
      ? queryKeys.visibility.evidence(projectId, {})
      : ['visibility', 'evidence', 'none'],
    queryFn: ({ signal }) =>
      visibilityApi.getVisibilityEvidence(projectId as string, undefined, { signal }),
    enabled: Boolean(projectId),
    retry: false,
  });

  const activePrompts = useMemo(
    () => prompts.filter((prompt) => prompt.status === 'active'),
    [prompts],
  );
  const visiblePrompts = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return activePrompts;
    return activePrompts.filter((prompt) => prompt.text.toLowerCase().includes(query));
  }, [activePrompts, search]);

  const scores = useMemo(() => promptScores(evidenceQuery.data?.items ?? []), [evidenceQuery.data]);
  const groups = useMemo(
    () => groupByTopic(visiblePrompts, topicsQuery.data ?? [], scores),
    [visiblePrompts, topicsQuery.data, scores],
  );
  // The banner pairs the ACTIVE prompt total with its topic count, so the
  // count must come from the unfiltered set — not the search-filtered groups.
  const topicCount = useMemo(
    () =>
      groupByTopic(activePrompts, topicsQuery.data ?? [], scores).filter(
        (group) => group.topic !== null,
      ).length,
    [activePrompts, topicsQuery.data, scores],
  );

  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (!projectId) {
    return (
      <Alert tone="info">
        Select or create a project first — prompts belong to a project&apos;s prompt set.
      </Alert>
    );
  }

  // Wait for topics too: rendering groups before topics arrive would flash
  // every prompt as "Ungrouped" for a moment.
  if (isLoading || topicsQuery.isLoading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      {isError ? (
        <Alert tone="danger">Could not load prompts. Check your connection and try again.</Alert>
      ) : null}

      <div className="border-border bg-panel flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
        <p className="text-secondary text-sm">
          The {project?.brand_name ?? 'brand'} configuration includes{' '}
          <span className="text-foreground font-semibold">{activePrompts.length}</span> visibility{' '}
          {activePrompts.length === 1 ? 'prompt' : 'prompts'} across{' '}
          <span className="text-foreground font-semibold">{topicCount}</span>{' '}
          {topicCount === 1 ? 'topic' : 'topics'}, which are run on each audit.
        </p>
        <Link
          href="/prompt-research"
          className={buttonVariants({ variant: 'primary', size: 'sm' })}
        >
          Go to Prompt Research
        </Link>
      </div>

      <div className="relative max-w-sm">
        <Search
          className="text-muted pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          type="search"
          role="searchbox"
          aria-label="Search prompts"
          placeholder="Search prompts..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="pl-8"
        />
      </div>

      {activePrompts.length === 0 ? (
        <div className="border-border bg-panel grid place-items-center gap-3 rounded-lg border border-dashed px-6 py-16 text-center">
          <p className="text-foreground text-sm font-medium">No active prompts yet</p>
          <p className="text-secondary max-w-md text-sm">
            Head to Prompt Research to add prompts manually, import a CSV, or generate prompts and
            topics with AI.
          </p>
          <Link
            href="/prompt-research"
            className={buttonVariants({ variant: 'secondary', size: 'sm' })}
          >
            Go to Prompt Research
          </Link>
        </div>
      ) : visiblePrompts.length === 0 ? (
        <div className="border-border bg-panel text-secondary rounded-lg border border-dashed px-6 py-12 text-center text-sm">
          No prompts match your search.
        </div>
      ) : (
        <div className="border-border rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" aria-label="Expand" />
                <TableHead>Prompt</TableHead>
                <TableHead numeric>Visibility Score</TableHead>
                <TableHead numeric>Avg Position</TableHead>
                <TableHead numeric>Sentiment</TableHead>
                <TableHead>Topic</TableHead>
                <TableHead>Branded</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => {
                const isCollapsed = collapsed.has(group.key);
                const label = group.topic?.name ?? 'Ungrouped';
                return (
                  <Fragment key={group.key}>
                    <TableRow className="bg-background-alt/50">
                      <TableCell>
                        <button
                          type="button"
                          aria-expanded={!isCollapsed}
                          aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} topic ${label}`}
                          onClick={() => toggleGroup(group.key)}
                          className="focus-ring text-muted hover:text-foreground grid size-6 place-items-center rounded"
                        >
                          {isCollapsed ? (
                            <ChevronRight className="size-4" aria-hidden />
                          ) : (
                            <ChevronDown className="size-4" aria-hidden />
                          )}
                        </button>
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex items-center gap-2">
                          <Badge variant="neutral">{label}</Badge>
                          <span className="text-muted text-xs">
                            {group.prompts.length}{' '}
                            {group.prompts.length === 1 ? 'prompt' : 'prompts'}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell numeric>
                        <ScoreCell score={group.score} />
                      </TableCell>
                      <TableCell numeric>
                        <span className="text-subtle">—</span>
                      </TableCell>
                      <TableCell numeric>
                        <span className="text-subtle">—</span>
                      </TableCell>
                      <TableCell />
                      <TableCell />
                    </TableRow>
                    {!isCollapsed
                      ? group.prompts.map((prompt) => (
                          <TableRow key={prompt.id}>
                            <TableCell />
                            <TableCell className="max-w-[480px]">
                              <span className="text-foreground block truncate" title={prompt.text}>
                                {prompt.text}
                              </span>
                            </TableCell>
                            <TableCell numeric>
                              <ScoreCell score={scores.get(prompt.id) ?? null} />
                            </TableCell>
                            <TableCell numeric>
                              <span className="text-subtle">—</span>
                            </TableCell>
                            <TableCell numeric>
                              <span className="text-subtle">—</span>
                            </TableCell>
                            <TableCell>
                              {group.topic ? (
                                <Badge variant="neutral">{group.topic.name}</Badge>
                              ) : (
                                <span className="text-subtle">—</span>
                              )}
                            </TableCell>
                            <TableCell>
                              {prompt.branded ? (
                                <Badge variant="status" value="info">
                                  Branded
                                </Badge>
                              ) : (
                                <span className="text-subtle">—</span>
                              )}
                            </TableCell>
                          </TableRow>
                        ))
                      : null}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
