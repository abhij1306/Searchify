'use client';

import { AlertTriangle, Check, Copy, Loader2, RefreshCw, Sparkles, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import type { RunStatusValue } from '@/components/ui/badge-variants';
import { CONTENT_PROMPT_MAX_LEN } from '@/lib/api/content';
import { ApiError, httpErrorStatus } from '@/lib/api/errors';
import type {
  ContentGenerationDetail,
  ContentGenerationListItem,
  ContentGenerationStatus,
} from '@/lib/api/types';
import {
  isTerminalContentStatus,
  useContentGenerations,
} from '@/lib/content/use-content-generations';
import { ContentMarkdown } from '@/lib/content/markdown';
import { useActiveProject } from '@/lib/project/project-context';
import { cn } from '@/lib/utils';

/** Map an action failure to the user-facing message (409s are specific,
 * everything else generic). */
function actionErrorMessage(error: unknown): string {
  if (httpErrorStatus(error) === 409) {
    const body = error instanceof ApiError ? error.body : '';
    if (body.includes('provider_not_configured')) {
      return 'Content generation is not configured yet — the provider API key is missing. Contact your administrator.';
    }
    if (body.includes('cancel_not_allowed')) {
      return 'This generation already finished, so it can no longer be cancelled.';
    }
    if (body.includes('idempotency_conflict')) {
      return 'A different request was already submitted with this key. Please try again.';
    }
  }
  return 'Something went wrong while generating your content. You can try again.';
}

/** Content statuses rendered through the existing run-status badge family. */
const STATUS_BADGE: Record<ContentGenerationStatus, RunStatusValue> = {
  queued: 'queued',
  leased: 'queued',
  running: 'running',
  retry_wait: 'running',
  succeeded: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};

function historyLabel(item: ContentGenerationListItem): string {
  return item.prompt_preview || 'Untitled generation';
}

/**
 * Content screen (prompt-box-first, designs `content-*.html`): one composer
 * with an output-type chip + default-on Website-context toggle, and four
 * states — ready, generating (locked composer + Cancel), result (sanitised
 * Markdown + provenance + truncation warning + Copy/Regenerate), error
 * (editable prompt + Try again + Dismiss preserving prompt + toggle).
 */
export function ContentScreen() {
  const activeProject = useActiveProject();
  const projectId = activeProject?.id ?? null;

  if (!projectId) {
    return (
      <Card>
        <CardContent className="flex flex-col items-start gap-3 py-8">
          <p className="text-sm text-secondary">
            Create a project first — content generation needs a project and its website.
          </p>
          <Link href="/setup" className="text-sm font-medium text-accent-text underline underline-offset-4">
            Go to Setup
          </Link>
        </CardContent>
      </Card>
    );
  }

  // `key` remounts on project switch so all transient state (prompt, toggle,
  // selection, mutation surfaces) resets — no cross-project bleed-through.
  return (
    <ProjectContentScreen
      key={projectId}
      projectId={projectId}
      projectName={activeProject?.name ?? 'project'}
    />
  );
}

function ProjectContentScreen({
  projectId,
  projectName,
}: Readonly<{ projectId: string; projectName: string }>) {
  const [prompt, setPrompt] = useState('');
  const [websiteContextEnabled, setWebsiteContextEnabled] = useState(true);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);

  const {
    listQuery,
    detailQuery,
    selectedId,
    setSelectedId,
    enqueueMutation,
    regenerateMutation,
    tryAgainMutation,
    cancelMutation,
  } = useContentGenerations(projectId);

  const detail: ContentGenerationDetail | null = detailQuery.data ?? null;
  const generating = Boolean(
    (detail && !isTerminalContentStatus(detail.status)) || enqueueMutation.isPending,
  );
  const failed = detail?.status === 'failed';
  const succeeded = detail?.status === 'succeeded';

  const mutationError =
    enqueueMutation.error ?? regenerateMutation.error ?? tryAgainMutation.error ?? null;
  const showErrorPanel = !generating && (Boolean(mutationError) || failed);

  useEffect(() => {
    if (showErrorPanel) promptRef.current?.focus();
  }, [showErrorPanel]);

  const trimmed = prompt.trim();
  const canGenerate = trimmed.length > 0 && trimmed.length <= CONTENT_PROMPT_MAX_LEN && !generating;

  const handleGenerate = () => {
    if (!canGenerate) return;
    cancelMutation.reset();
    enqueueMutation.mutate({ prompt: trimmed, websiteContextEnabled });
  };

  const handleDismiss = () => {
    // Clears only the transient error surface; prompt text + toggle survive.
    enqueueMutation.reset();
    regenerateMutation.reset();
    tryAgainMutation.reset();
    cancelMutation.reset();
    setSelectedId(null);
  };

  const handleCopy = async () => {
    if (!detail?.output_text) return;
    try {
      // Clipboard access can be denied (permissions policy, insecure
      // context) — surface a visible failure instead of an unhandled reject.
      await navigator.clipboard.writeText(detail.output_text);
      setCopyFailed(false);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setCopyFailed(true);
      setTimeout(() => setCopyFailed(false), 2000);
    }
  };

  let copyLabel = 'Copy';
  if (copied) copyLabel = 'Copied';
  else if (copyFailed) copyLabel = 'Copy failed';

  return (
    <div className="flex flex-col gap-6">
      {/* Composer */}
      <Card data-component-id="content-prompt-box">
        <CardContent className="flex flex-col gap-3 py-5">
          <h2 className="text-lg font-semibold text-foreground">What can I help you create?</h2>
          <Textarea
            ref={promptRef}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            disabled={generating}
            maxLength={CONTENT_PROMPT_MAX_LEN}
            rows={4}
            aria-label="Describe the website content you want to create"
            placeholder="Describe the website content you want to create…"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Badge data-component-id="content-output-type" aria-label="Output type: Website page">
              Website page
            </Badge>
            <button
              type="button"
              data-component-id="content-website-context-toggle"
              role="switch"
              aria-checked={websiteContextEnabled}
              aria-label={`Website context, ${projectName}, ${websiteContextEnabled ? 'on' : 'off'}`}
              disabled={generating}
              onClick={() => setWebsiteContextEnabled((value) => !value)}
              className={cn(
                'focus-ring inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                websiteContextEnabled
                  ? 'border-accent-border bg-accent-subtle text-accent-text'
                  : 'border-border bg-background-alt text-secondary',
              )}
            >
              <Sparkles className="size-3" aria-hidden />
              Website context {websiteContextEnabled ? 'on' : 'off'}
            </button>
            <div className="ml-auto">
              <Button
                data-component-id="content-generate-button"
                disabled={!canGenerate}
                onClick={handleGenerate}
              >
                Generate
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Generating */}
      {generating ? (
        <Card data-component-id="content-generating-panel">
          <CardContent className="flex items-center gap-4 py-6">
            <div role="status" aria-label="Generating content" className="flex items-center gap-3">
              <Loader2 className="size-5 animate-spin text-accent" aria-hidden />
              <span className="text-sm text-secondary">Generating your content…</span>
            </div>
            <div className="ml-auto">
              <Button
                variant="secondary"
                data-component-id="content-cancel-button"
                disabled={!selectedId || cancelMutation.isPending}
                onClick={() => selectedId && cancelMutation.mutate(selectedId)}
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Error */}
      {showErrorPanel ? (
        <Card data-component-id="content-error-panel">
          <CardContent className="flex flex-col gap-3 py-5">
            <div role="alert" className="flex items-start gap-2 text-sm text-danger-text">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                {mutationError
                  ? actionErrorMessage(mutationError)
                  : 'Generation failed. You can edit your prompt and try again.'}
              </span>
            </div>
            <div className="flex gap-2">
              {failed && detail ? (
                <Button
                  data-component-id="content-retry-button"
                  disabled={tryAgainMutation.isPending}
                  onClick={() => tryAgainMutation.mutate(detail.id)}
                >
                  <RefreshCw className="mr-1.5 size-4" aria-hidden />
                  Try again
                </Button>
              ) : null}
              <Button
                variant="secondary"
                data-component-id="content-dismiss-button"
                onClick={handleDismiss}
              >
                <X className="mr-1.5 size-4" aria-hidden />
                Dismiss
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Result */}
      {!generating && succeeded && detail?.output_text ? (
        <Card data-component-id="content-result-card">
          <CardContent className="flex flex-col gap-4 py-5">
            {detail.output_truncated ? (
              <div data-component-id="content-truncation-warning">
                <Alert tone="warning">
                  The output hit the length limit and may be incomplete. Regenerate or shorten your
                  prompt for a complete result.
                </Alert>
              </div>
            ) : null}
            <div data-component-id="content-result-body">
              <ContentMarkdown markdown={detail.output_text} />
            </div>
            <p data-component-id="content-ai-disclaimer" className="text-xs text-secondary">
              AI-generated content — review before publishing.
            </p>
            <div
              data-component-id="content-result-provenance"
              className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-3 text-xs text-secondary"
            >
              <span>Requested model: {detail.requested_model}</span>
              {detail.returned_model ? <span>Returned model: {detail.returned_model}</span> : null}
              <span>
                Website context:{' '}
                {detail.website_context_status === 'included'
                  ? `${detail.website_context_summary?.page_count ?? 0} pages`
                  : detail.website_context_status}
              </span>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                data-component-id="content-copy-button"
                onClick={handleCopy}
              >
                {copied ? (
                  <Check className="mr-1.5 size-4" aria-hidden />
                ) : (
                  <Copy className="mr-1.5 size-4" aria-hidden />
                )}
                {copyLabel}
              </Button>
              <Button
                variant="secondary"
                data-component-id="content-regenerate-button"
                disabled={regenerateMutation.isPending}
                onClick={() => regenerateMutation.mutate(detail.id)}
              >
                <RefreshCw className="mr-1.5 size-4" aria-hidden />
                Regenerate
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* History */}
      <section data-component-id="content-history" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-secondary">Recent generations</h2>
        {listQuery.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (listQuery.data ?? []).length === 0 ? (
          <p className="text-sm text-secondary">No generations yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {(listQuery.data ?? []).map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className={cn(
                    'focus-ring flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-background-alt',
                    item.id === selectedId
                      ? 'border-accent-border bg-background-alt'
                      : 'border-border',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate text-foreground">
                    {historyLabel(item)}
                  </span>
                  <Badge variant="run-status" value={STATUS_BADGE[item.status]}>
                    {item.status.replace('_', ' ')}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
