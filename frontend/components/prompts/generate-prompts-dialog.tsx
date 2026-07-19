'use client';

import { Sparkles } from 'lucide-react';
import { useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { httpErrorStatus } from '@/lib/api/errors';
import type { PromptGenerateInput } from '@/lib/api/prompts';
import type { PromptGenerateResponse, Topic } from '@/lib/api/types';

/**
 * AI generation dialog (Generate Prompts & Topics). Collects count + optional
 * target topic, and requires an explicit consent checkbox before the brand
 * profile (name, aliases, competitors, market) is sent to the configured
 * default agent — the backend independently enforces the same gate
 * (`confirm_send_evidence`). Suggestions fill the set-wide active pool first
 * and the rest land in Proposed; nothing beyond the pool is audit-eligible
 * until a human accepts it.
 */
export function GeneratePromptsDialog({
  open,
  onOpenChange,
  topics,
  defaultTopicId,
  onGenerate,
  isGenerating,
  error,
  result,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  topics: Topic[];
  /** Preselect the topic the user is currently viewing (null = all). */
  defaultTopicId?: string | null;
  onGenerate: (input: PromptGenerateInput) => Promise<void> | void;
  isGenerating?: boolean;
  error?: unknown;
  /** Set after a successful run so the dialog can summarize it. */
  result?: PromptGenerateResponse | null;
}>) {
  const [count, setCount] = useState('10');
  const [topicId, setTopicId] = useState<string>(defaultTopicId ?? '');
  const [confirmed, setConfirmed] = useState(false);

  const parsedCount = Number.parseInt(count, 10);
  const countValid = Number.isFinite(parsedCount) && parsedCount >= 1 && parsedCount <= 20;

  const handleOpenChange = (next: boolean) => {
    if (next) setTopicId(defaultTopicId ?? '');
    if (!next) setConfirmed(false);
    onOpenChange(next);
  };

  const submit = async () => {
    if (!confirmed || !countValid) return;
    await onGenerate({
      count: parsedCount,
      topic_id: topicId || undefined,
      confirm_send_evidence: true,
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Generate prompts & topics"
      description="Searchify drafts topic-organized prompt suggestions from your brand profile."
      className="w-[520px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            {result ? 'Close' : 'Cancel'}
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={isGenerating || !confirmed || !countValid}
          >
            <Sparkles className="size-4" aria-hidden />
            {isGenerating ? 'Generating…' : 'Generate'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {error ? <GenerateErrorAlert error={error} /> : null}
        {result && !error ? <GenerateResultAlert result={result} /> : null}

        <label className="grid gap-1.5">
          <span className="text-xs font-medium text-secondary">Number of prompts (1–20)</span>
          <Input
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(event) => setCount(event.target.value)}
            aria-label="Number of prompts"
            aria-invalid={!countValid}
          />
        </label>

        <label className="grid gap-1.5">
          <span className="text-xs font-medium text-secondary">Topic</span>
          <select
            value={topicId}
            onChange={(event) => setTopicId(event.target.value)}
            aria-label="Topic"
            className="focus-ring block w-full rounded-md border border-border-strong bg-panel px-2.5 py-1.5 text-sm text-foreground"
          >
            <option value="">Let AI propose topics</option>
            {topics.map((topic) => (
              <option key={topic.id} value={topic.id}>
                {topic.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-start gap-2 rounded-md border border-border bg-background-alt px-3 py-2.5 text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            aria-label="Confirm sending brand details to the AI provider"
            className="focus-ring mt-0.5 size-4 shrink-0 accent-accent"
          />
          <span className="text-secondary">
            I understand my brand profile (brand name, aliases, competitors, and market) will be
            sent to the configured AI provider to generate suggestions.
          </span>
        </label>
      </div>
    </Dialog>
  );
}

/**
 * Success summary. Generated prompts fill the set-wide active pool first, so a
 * run can land rows in Active and/or Proposed — report both counts (and any
 * dropped duplicates) and point the user at the tab(s) that received rows.
 */
function GenerateResultAlert({ result }: Readonly<{ result: PromptGenerateResponse }>) {
  const total = result.generated.length;
  const proposed = result.generated.filter((prompt) => prompt.status === 'proposed').length;
  const active = result.generated.filter((prompt) => prompt.status === 'active').length;

  // Count topics that actually received generated rows — i.e. the unique
  // non-null topic_id values on `generated` — rather than every topic the run
  // touched (`result.topics` also includes topics whose only change was a
  // dropped duplicate, so it can overstate where rows landed).
  const topicCount = new Set(
    result.generated
      .map((prompt) => prompt.topic_id)
      .filter((id): id is string => id != null),
  ).size;

  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

  const placements: string[] = [];
  if (active > 0) placements.push(`${plural(active, 'prompt')} added to Active`);
  if (proposed > 0) placements.push(`${plural(proposed, 'prompt')} proposed for review`);

  return (
    <Alert tone="success">
      Generated {plural(total, 'prompt')}
      {topicCount > 0 ? ` across ${plural(topicCount, 'topic')}` : ''}
      {result.dropped_duplicates > 0
        ? `; ${plural(result.dropped_duplicates, 'duplicate')} skipped`
        : ''}
      .{placements.length > 0 ? ` ${placements.join('; ')}.` : ''}
    </Alert>
  );
}

/** Map generation failures to actionable copy (503 config, 502 provider, 4xx). */
function GenerateErrorAlert({ error }: Readonly<{ error: unknown }>) {
  const status = httpErrorStatus(error);
  if (status === 503) {
    return (
      <Alert tone="warning">
        No AI provider is configured. Set <code>DEFAULT_AGENT_API_KEY</code> (and optionally{' '}
        <code>DEFAULT_AGENT_BASE_URL</code> / <code>DEFAULT_AGENT_MODEL</code>) in the backend
        environment, then try again.
      </Alert>
    );
  }
  if (status === 502) {
    return (
      <Alert tone="danger">
        The AI provider call failed or returned unusable output. Try again in a moment.
      </Alert>
    );
  }
  const message =
    error instanceof Error && error.message ? error.message : 'Generation failed. Please try again.';
  return <Alert tone="danger">{message}</Alert>;
}
