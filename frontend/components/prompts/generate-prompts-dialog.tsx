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
 * (`confirm_send_evidence`). Suggestions land in the Proposed tab; nothing is
 * audit-eligible until a human accepts it.
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
        {result ? (
          <Alert tone="success">
            Added {result.generated.length} proposed prompt
            {result.generated.length === 1 ? '' : 's'}
            {result.topics.length > 0
              ? ` across ${result.topics.length} topic${result.topics.length === 1 ? '' : 's'}`
              : ''}
            {result.dropped_duplicates > 0
              ? `; ${result.dropped_duplicates} duplicate${
                  result.dropped_duplicates === 1 ? '' : 's'
                } skipped`
              : ''}
            . Review them in the Proposed tab.
          </Alert>
        ) : null}

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
            className="focus-ring mt-0.5 size-4 shrink-0 accent-[var(--color-accent,#4f46e5)]"
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
