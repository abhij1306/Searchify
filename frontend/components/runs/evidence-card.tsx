'use client';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/typography';
import { engineLabel, transportLabel } from '@/lib/providers/catalog';
import type { ExecutionEvidence } from '@/lib/api/types';
import { classificationBadgeValue, classificationLabel } from '@/lib/runs/status';

/** Humanize a `score` dict key: `owned_domain_cited` → `Owned domain cited`. */
function formatScoreKey(key: string): string {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Render a `score` dict entry value as a compact, human-readable string. */
function formatScoreValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'string') return value.trim() || '—';
  if (Array.isArray(value)) {
    return value.length === 0 ? '—' : value.map((item) => String(item)).join(', ');
  }
  return JSON.stringify(value);
}

/**
 * Execution evidence card (F10, design.md §9.7).
 *
 * Renders one execution's persisted evidence: the answer text, the `search_used`
 * grounding badge, the classified citations (owned / competitor / third-party;
 * the backend's `unintended` class folds onto the owned visual), brand +
 * competitor mention chips, and the per-response `score` dict (mono key/value).
 * Sentiment is present but not computed at MVP, so it shows the `—` placeholder.
 *
 * The evidence endpoint (`GET /executions/{id}`) is projection-only and does not
 * carry the raw answer text — that lives on the execution/queue row
 * (`AuditTaskResponse`), which shares the same id space — so the answer text is
 * passed in separately by the page.
 */
export function EvidenceCard({
  evidence,
  answerText,
}: Readonly<{ evidence: ExecutionEvidence; answerText?: string | null }>) {
  const scoreEntries = Object.entries(evidence.score ?? {});

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Answer</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="neutral">
              {engineLabel(evidence.logical_engine)} · {transportLabel(evidence.transport_provider)}
            </Badge>
            <Badge variant="status" value={evidence.search_used ? 'info' : 'warning'}>
              {evidence.search_used ? 'Search used' : 'No search'}
            </Badge>
            <Badge variant="status" value={evidence.brand_mentioned ? 'success' : 'danger'}>
              {evidence.brand_mentioned ? 'Brand mentioned' : 'Brand not mentioned'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-foreground text-sm leading-relaxed whitespace-pre-wrap">
            {answerText?.trim() ? (
              answerText
            ) : (
              <span className="text-muted">No answer text was captured for this execution.</span>
            )}
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="min-w-0 overflow-hidden">
          <CardHeader>
            <CardTitle className="text-base">Citations</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            {evidence.citations.length === 0 ? (
              <p className="text-muted text-sm">No citations were captured.</p>
            ) : (
              <ul className="grid gap-2.5">
                {evidence.citations.map((citation) => (
                  <li
                    key={`${citation.ordinal}-${citation.url}`}
                    className="border-border-subtle flex items-start justify-between gap-3 border-b pb-2.5 last:border-0 last:pb-0"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground truncate text-sm">
                        {citation.title?.trim() || citation.domain || citation.url}
                      </p>
                      <p className="text-muted truncate text-xs">
                        {citation.domain || citation.url}
                      </p>
                    </div>
                    <Badge
                      className="shrink-0"
                      variant="classification"
                      value={classificationBadgeValue(citation.classification)}
                    >
                      {classificationLabel(citation.classification)}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <div className="grid min-w-0 gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Mentions</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              <div className="grid gap-1.5">
                <Label>Brand</Label>
                {evidence.brand_mentioned ? (
                  <Badge className="justify-self-start" variant="status" value="success">
                    Mentioned
                  </Badge>
                ) : (
                  <span className="text-muted text-sm">Not mentioned</span>
                )}
              </div>
              <div className="grid gap-1.5">
                <Label>Competitors</Label>
                {evidence.competitors_mentioned.length === 0 ? (
                  <span className="text-muted text-sm">None mentioned</span>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {evidence.competitors_mentioned.map((name) => (
                      <Badge key={name} variant="classification" value="competitor">
                        {name}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
              <div className="grid gap-1.5">
                <Label>Sentiment</Label>
                <span className="mono text-muted text-sm">{evidence.sentiment ?? '—'}</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Score</CardTitle>
            </CardHeader>
            <CardContent>
              {scoreEntries.length === 0 ? (
                <p className="text-muted text-sm">No score recorded.</p>
              ) : (
                <dl className="grid gap-2">
                  {scoreEntries.map(([key, value]) => (
                    <div
                      key={key}
                      className="flex items-baseline justify-between gap-3"
                    >
                      <dt className="text-secondary min-w-0 truncate text-xs">
                        {formatScoreKey(key)}
                      </dt>
                      <dd className="mono text-foreground min-w-0 text-right text-xs break-words">
                        {formatScoreValue(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
