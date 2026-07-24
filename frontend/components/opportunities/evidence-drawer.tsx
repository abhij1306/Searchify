'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import type { StatusValue } from '@/components/ui/badge-variants';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { opportunitiesMutations, opportunitiesQueries } from '@/lib/api/opportunities';
import { queryKeys } from '@/lib/api/query-keys';
import type {
  OpportunityDetail,
  OpportunityStatus,
  OpportunityType,
} from '@/lib/api/types';
import { severityBadgeValue, severityLabel } from '@/lib/site-health/issues';
import { formatAudited } from '@/lib/site-health/status';
import { cn } from '@/lib/utils';

/**
 * Opportunity evidence drawer (approved mockup: drilldown side drawer).
 *
 * NEW purpose-built component on the shared right-side Radix DialogPrimitive
 * shell pattern (bg-elevated panel, hairline left border, shadow-modal,
 * scrim — 448px per the mockup). It is NOT the HistoryDrawer: that contract
 * is `HistoryItem[]` run-history rows and cannot render an evidence bundle.
 *
 * Body sections (all rendered from the persisted detail projection — nothing
 * is recomputed client-side): title + badges, priority + formula, the
 * evidence bundle (prompt quote / target URL + kv rows + offending-value
 * chips), provenance (rule + versions + source row ids + detected-at),
 * remediation, and a status-workflow footer (the one mutation).
 */

const STATUS_LABEL: Record<OpportunityStatus, string> = {
  open: 'Open',
  in_progress: 'In progress',
  dismissed: 'Dismissed',
  resolved: 'Resolved',
};

const STATUS_BADGE: Record<OpportunityStatus, StatusValue | 'neutral'> = {
  open: 'info',
  in_progress: 'warning',
  dismissed: 'neutral',
  resolved: 'success',
};

const TYPE_LABEL: Record<OpportunityType, string> = {
  visibility: 'Visibility',
  site: 'Site',
  traffic: 'Traffic',
  topic: 'Topic',
};

/** Status badge (mockup palette: open=info, in-progress=warning, resolved=success). */
export function OpportunityStatusBadge({ status }: Readonly<{ status: OpportunityStatus }>) {
  const value = STATUS_BADGE[status];
  if (value === 'neutral') {
    return <Badge>{STATUS_LABEL[status]}</Badge>;
  }
  return (
    <Badge variant="status" value={value}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/** Type badge (mockup palette: visibility=accent, site=info, topic/traffic=third-party). */
export function OpportunityTypeBadge({ type }: Readonly<{ type: OpportunityType }>) {
  return (
    <Badge
      className={cn(
        type === 'visibility' && 'text-accent-text',
        type === 'site' && 'text-info-text',
        (type === 'topic' || type === 'traffic') && 'text-citation-third-party-text',
      )}
    >
      {TYPE_LABEL[type]}
    </Badge>
  );
}

/** One labeled evidence/provenance row (mono label + wrapped value). */
function KvRow({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <span className="text-2xs text-muted shrink-0 font-mono tracking-[0.08em] uppercase">
        {label}
      </span>
      <span className="text-secondary text-right text-sm break-words">{value}</span>
    </div>
  );
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

function shortId(id: string): string {
  return id.length > 13 ? `${id.slice(0, 4)}…${id.slice(-4)}` : id;
}

/** Evidence bundle section — prompt quote / target URL + kv rows + chips. */
function EvidenceSection({ detail }: Readonly<{ detail: OpportunityDetail }>) {
  const evidence = detail.evidence;
  const promptText = asString(evidence.prompt_text);
  const url = asString(evidence.url) ?? detail.target_url;
  const theme = asString(evidence.prompt_theme) ?? detail.target_theme;
  const intent = asString(evidence.prompt_intent);
  const engines = asStringList(evidence.engines);
  const competitors = asStringList(evidence.competitor_names);
  const ownedDomains = asStringList(evidence.owned_domains);
  const repetitions =
    typeof evidence.repetitions === 'number' ? evidence.repetitions : null;
  const ownedCitationCount =
    typeof evidence.owned_citation_count === 'number'
      ? evidence.owned_citation_count
      : null;
  const issueRuleId = asString(evidence.issue_rule_id);
  const category = asString(evidence.category);

  return (
    <section className="grid gap-2">
      <Label className="font-mono tracking-[0.08em]">Evidence</Label>
      {promptText ? (
        <blockquote className="border-accent-border bg-accent-subtle rounded-lg border-l-2 px-3 py-2">
          <p className="text-foreground text-sm">“{promptText}”</p>
        </blockquote>
      ) : null}
      {url ? (
        <p className="mono text-accent-text bg-background-alt rounded-lg px-3 py-2 text-xs break-all">
          {url}
        </p>
      ) : null}
      <div className="divide-border-subtle divide-y">
        {theme ? <KvRow label="Theme" value={theme} /> : null}
        {intent ? <KvRow label="Intent" value={intent} /> : null}
        {engines.length > 0 ? (
          <KvRow
            label="Engines × reps"
            value={`${engines.join(' · ')}${repetitions !== null ? ` × ${repetitions}` : ''}`}
          />
        ) : null}
        {ownedCitationCount !== null ? (
          <KvRow label="Owned citations" value={`${ownedCitationCount}`} />
        ) : null}
        {issueRuleId ? <KvRow label="Site rule" value={issueRuleId} /> : null}
        {category ? <KvRow label="Category" value={category} /> : null}
      </div>
      {competitors.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {competitors.map((name) => (
            <Badge key={name} variant="classification" value="competitor">
              {name}
            </Badge>
          ))}
        </div>
      ) : null}
      {ownedDomains.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {ownedDomains.map((domain) => (
            <Badge key={domain} variant="classification" value="owned">
              {domain}
            </Badge>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** Provenance section — rule + versions + source rows + detected-at. */
function ProvenanceSection({ detail }: Readonly<{ detail: OpportunityDetail }>) {
  const sourceCounts = [
    detail.source_analysis_ids.length > 0
      ? `${detail.source_analysis_ids.length} analyses`
      : null,
    detail.source_issue_ids.length > 0 ? `${detail.source_issue_ids.length} issues` : null,
    detail.source_metric_ids.length > 0
      ? `${detail.source_metric_ids.length} snapshot`
      : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const sourceIds = [
    ...detail.source_analysis_ids,
    ...detail.source_issue_ids,
    ...detail.source_metric_ids,
  ];

  return (
    <section className="grid gap-2">
      <Label className="font-mono tracking-[0.08em]">Provenance</Label>
      <div className="divide-border-subtle divide-y">
        <KvRow label="Rule" value={detail.rule_id} />
        <KvRow label="Rule version" value={detail.rule_version} />
        <KvRow label="Analyzer" value={detail.analyzer_version} />
        <KvRow label="Formula" value={detail.formula_version} />
        {sourceCounts ? <KvRow label="Source rows" value={sourceCounts} /> : null}
        {sourceIds.length > 0 ? (
          <KvRow label="Source ids" value={sourceIds.map(shortId).join(' · ')} />
        ) : null}
        <KvRow label="Detected" value={formatAudited(detail.created_at)} />
      </div>
    </section>
  );
}

/** Status-workflow footer — the ONLY mutation the surface allows. */
function StatusFooter({
  detail,
  projectId,
}: Readonly<{ detail: OpportunityDetail; projectId: string }>) {
  const queryClient = useQueryClient();
  const updateStatus = useMutation({
    ...opportunitiesMutations.updateStatus(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.list(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.summary(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.detail(detail.id),
        }),
      ]);
    },
  });

  const change = (status: OpportunityStatus) => {
    updateStatus.mutate({ opportunityId: detail.id, status });
  };

  return (
    <footer className="border-border-subtle grid gap-2 border-t px-4 py-3">
      {updateStatus.isError ? (
        <Alert tone="danger">
          Could not update the status — the opportunity may have been superseded by a newer
          recompute.
        </Alert>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-2xs text-muted font-mono tracking-[0.08em] uppercase">
            Status
          </span>
          <OpportunityStatusBadge status={detail.status} />
        </div>
        <div className="flex items-center gap-2">
          {detail.status === 'open' ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                disabled={updateStatus.isPending}
                onClick={() => change('dismissed')}
              >
                Dismiss
              </Button>
              <Button
                size="sm"
                disabled={updateStatus.isPending}
                onClick={() => change('in_progress')}
              >
                Mark in progress
              </Button>
            </>
          ) : null}
          {detail.status === 'in_progress' ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                disabled={updateStatus.isPending}
                onClick={() => change('dismissed')}
              >
                Dismiss
              </Button>
              <Button
                size="sm"
                disabled={updateStatus.isPending}
                onClick={() => change('resolved')}
              >
                Mark resolved
              </Button>
            </>
          ) : null}
          {detail.status === 'dismissed' || detail.status === 'resolved' ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={updateStatus.isPending}
              onClick={() => change('open')}
            >
              Reopen
            </Button>
          ) : null}
        </div>
      </div>
    </footer>
  );
}

export function EvidenceDrawer({
  opportunityId,
  projectId,
  open,
  onOpenChange,
}: Readonly<{
  opportunityId: string | null;
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}>) {
  const detailQuery = useQuery({
    ...opportunitiesQueries.detail(opportunityId ?? ''),
    enabled: open && opportunityId !== null,
  });
  const detail = detailQuery.data ?? null;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="bg-overlay-scrim fixed inset-0 z-[100]" />
        <DialogPrimitive.Content
          className={cn(
            'border-border bg-elevated shadow-modal-value fixed top-0 right-0 z-[101] flex h-full w-[448px] max-w-full flex-col border-l focus:outline-none',
          )}
        >
          <header className="border-border-subtle flex items-center justify-between gap-2 border-b px-4 py-3">
            <DialogPrimitive.Title className="text-foreground truncate text-base font-semibold">
              Opportunity detail
            </DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close drawer">
                <X className="size-4" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
            {detailQuery.isError ? (
              <Alert tone="danger">Could not load this opportunity. Please try again.</Alert>
            ) : detailQuery.isLoading || !detail ? (
              <div className="grid gap-3">
                <Skeleton className="h-8 w-3/4" />
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-32 w-full" />
              </div>
            ) : (
              <div className="grid gap-5">
                <div className="grid gap-2">
                  <h2 className="text-foreground text-lg font-semibold">{detail.title}</h2>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="status" value={severityBadgeValue(detail.severity)}>
                      {severityLabel(detail.severity)}
                    </Badge>
                    <OpportunityTypeBadge type={detail.opportunity_type} />
                    <OpportunityStatusBadge status={detail.status} />
                  </div>
                  <p className="text-secondary text-sm">
                    <span className="mono text-foreground text-base font-semibold">
                      {detail.priority_score.toFixed(1)}
                    </span>{' '}
                    priority · deterministic formula {detail.formula_version}
                  </p>
                </div>
                <EvidenceSection detail={detail} />
                <ProvenanceSection detail={detail} />
                {detail.remediation ? (
                  <section className="grid gap-2">
                    <Label className="font-mono tracking-[0.08em]">Remediation</Label>
                    <div className="border-border-subtle bg-background-alt rounded-lg border p-3">
                      <p className="text-secondary text-sm whitespace-pre-line">
                        {detail.remediation}
                      </p>
                    </div>
                  </section>
                ) : null}
              </div>
            )}
          </div>
          {detail ? <StatusFooter detail={detail} projectId={projectId} /> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
