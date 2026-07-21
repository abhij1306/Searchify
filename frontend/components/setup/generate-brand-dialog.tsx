'use client';

import { Sparkles } from 'lucide-react';
import { useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { httpErrorStatus } from '@/lib/api/errors';

/**
 * Shared AI suggestion dialog for the setup form (competitors and owned
 * domains). Requires an explicit consent checkbox before the brand profile
 * (name, website, aliases, market) is sent to the configured default agent —
 * the backend independently enforces the same gate (`confirm_send_evidence`).
 * Suggestions are appended into the form for review; nothing is persisted
 * until the user saves the project.
 */
export function GenerateBrandDialog({
  open,
  onOpenChange,
  title,
  description,
  onGenerate,
  isGenerating,
  error,
  resultSummary,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  onGenerate: () => Promise<void> | void;
  isGenerating?: boolean;
  error?: unknown;
  /** Set after a successful run so the dialog can summarize it. */
  resultSummary?: string | null;
}>) {
  const [confirmed, setConfirmed] = useState(false);

  const handleOpenChange = (next: boolean) => {
    if (!next) setConfirmed(false);
    onOpenChange(next);
  };

  const submit = async () => {
    if (!confirmed) return;
    await onGenerate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      title={title}
      description={description}
      className="w-[520px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            {resultSummary ? 'Close' : 'Cancel'}
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={isGenerating || !confirmed}
          >
            <Sparkles className="size-4" aria-hidden />
            {isGenerating ? 'Generating…' : 'Generate'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {error ? <SuggestErrorAlert error={error} /> : null}
        {resultSummary && !error ? <Alert tone="success">{resultSummary}</Alert> : null}

        <label className="border-border bg-background-alt flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            aria-label="Confirm sending brand details to the AI provider"
            className="focus-ring accent-accent mt-0.5 size-4 shrink-0"
          />
          <span className="text-secondary">
            I understand my brand profile (brand name, website, aliases, and market) will be sent to
            the configured AI provider to generate suggestions.
          </span>
        </label>
      </div>
    </Dialog>
  );
}

/** Map suggestion failures to actionable copy (503 config, 502 provider, 4xx). */
function SuggestErrorAlert({ error }: Readonly<{ error: unknown }>) {
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
    error instanceof Error && error.message
      ? error.message
      : 'Generation failed. Please try again.';
  return <Alert tone="danger">{message}</Alert>;
}
