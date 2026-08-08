/**
 * Site Health page-kind vocabulary + presentation helpers (v2 P1) — PURE.
 *
 * The SINGLE shared mapping from the backend `page_kind` classification
 * vocabulary to humanized labels — every badge, filter control, and the
 * dashboard per-type breakdown reads it from here (no duplicated maps).
 * No transport, no React.
 */
import { pageKindSchema } from '@/lib/api/schemas';
import type { PageKind, PageKindScoreSummary } from '@/lib/api/types';
import { titleCaseStatus } from '@/lib/utils';

/**
 * Every page type in stable display order (filter control + breakdown
 * table). Derived from the API-contract zod enum (the same derivation as
 * `lib/prompts/forms.ts` `intentValues`) so the vocabulary has exactly one
 * frontend owner.
 */
export const PAGE_KINDS: readonly PageKind[] = pageKindSchema.options;

/** Humanized label per page type — the one shared mapping. */
export const PAGE_KIND_LABELS: Record<PageKind, string> = {
  homepage: 'Homepage',
  article: 'Article',
  product: 'Product',
  category: 'Category',
  pricing: 'Pricing',
  docs: 'Docs',
  faq: 'FAQ',
  about_contact: 'About / Contact',
  service: 'Service',
  local: 'Local',
  guide: 'Guide',
  comparison: 'Comparison',
  case_study_review: 'Case Study / Review',
  trust_policy: 'Trust / Policy',
  other: 'Other',
};

/**
 * Display label for a page type. An unknown value (a vocabulary the frontend
 * has not caught up with) falls back to title-casing instead of rendering
 * blank — the same defensive fallback `issueTitle` applies to blank titles.
 */
export function pageKindLabel(pageKind: string): string {
  return PAGE_KIND_LABELS[pageKind as PageKind] ?? titleCaseStatus(pageKind);
}

/** One display row of the dashboard per-page-kind score breakdown. */
export type PageKindScoreRow = PageKindScoreSummary & { page_kind: string };

/**
 * Order a `score_summary.by_page_kind` map for display: the `PAGE_KINDS`
 * order first, then any unknown types alphabetically — stable and
 * deterministic, never dependent on the API's map insertion order.
 */
export function byPageKindRows(
  byPageKind: Record<string, PageKindScoreSummary>,
): PageKindScoreRow[] {
  const rank = new Map<string, number>(PAGE_KINDS.map((type, index) => [type, index]));
  return Object.entries(byPageKind)
    .map(([page_kind, scores]) => ({ page_kind, ...scores }))
    .sort((a, b) => {
      const aRank = rank.get(a.page_kind) ?? PAGE_KINDS.length;
      const bRank = rank.get(b.page_kind) ?? PAGE_KINDS.length;
      return aRank === bRank ? a.page_kind.localeCompare(b.page_kind) : aRank - bRank;
    });
}

/**
 * One ranked matched-signal entry of the persisted classifier evidence —
 * `{ signal, page_kind, weight, detail }` as the backend
 * `PageKindAssessment.to_evidence()` emits it (detail already truncated
 * server-side).
 */
export type PageKindEvidenceSignal = {
  signal: string;
  pageKind: string;
  weight: number;
  detail: string;
};

/**
 * The parsed, display-ready classifier evidence for one analyzed page (the
 * per-URL detail "why this type?" disclosure payload).
 */
export type PageKindEvidenceView = {
  classifierVersion: string;
  /** Name of the winning signal (`none` when nothing matched). */
  classifiedBy: string;
  /** What the structured-data signal alone would have suggested. */
  schemaSuggestedType: string | null;
  confidence: number;
  confidenceThreshold: number;
  signals: PageKindEvidenceSignal[];
  /**
   * True when the schema-suggested type disagrees with the page's final
   * type — the disclosure highlights the conflict (signals 1–3 outrank the
   * schema claim by design).
   */
  schemaConflict: boolean;
  /**
   * Non-winning candidate kinds with their aggregated confidence. Dropping
   * these hid the runner-up entirely, so a near-tie looked identical to a
   * decisive classification.
   */
  alternatives: PageKindEvidenceCandidate[];
  /** Signals that disagreed with the winner (the "why not X?" evidence). */
  conflicts: PageKindEvidenceConflict[];
  /**
   * Why the page fell back to `other`: `no_signals` (nothing matched) or
   * `below_threshold` (something matched but too weakly). Null when a kind
   * was chosen. Without it, a below-threshold page is indistinguishable from
   * one the classifier never found evidence for.
   */
  otherReason: string | null;
};

/** One non-winning candidate kind. */
export type PageKindEvidenceCandidate = {
  pageKind: string;
  confidence: number;
  signals: string[];
};

/** One signal that disagreed with the winning kind. */
export type PageKindEvidenceConflict = {
  winnerPageKind: string;
  conflictingPageKind: string;
  signal: string;
  detail: string;
};

/** Bounded, shape-checked parse of the `alternatives` array. */
function readAlternatives(value: unknown): PageKindEvidenceCandidate[] {
  if (!Array.isArray(value)) return [];
  const out: PageKindEvidenceCandidate[] = [];
  for (const raw of value) {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue;
    const entry = raw as Record<string, unknown>;
    if (typeof entry.page_kind !== 'string' || typeof entry.confidence !== 'number') continue;
    out.push({
      pageKind: entry.page_kind,
      confidence: entry.confidence,
      signals: Array.isArray(entry.signals)
        ? entry.signals.filter((item): item is string => typeof item === 'string')
        : [],
    });
  }
  return out;
}

/** Bounded, shape-checked parse of the `conflicts` array. */
function readConflicts(value: unknown): PageKindEvidenceConflict[] {
  if (!Array.isArray(value)) return [];
  const out: PageKindEvidenceConflict[] = [];
  for (const raw of value) {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue;
    const entry = raw as Record<string, unknown>;
    if (
      typeof entry.winner_page_kind !== 'string' ||
      typeof entry.conflicting_page_kind !== 'string' ||
      typeof entry.signal !== 'string'
    ) {
      continue;
    }
    out.push({
      winnerPageKind: entry.winner_page_kind,
      conflictingPageKind: entry.conflicting_page_kind,
      signal: entry.signal,
      detail: typeof entry.detail === 'string' ? entry.detail : '',
    });
  }
  return out;
}

/**
 * Narrow the untyped `page_kind_evidence` record (zod `z.unknown()` values)
 * into the display view. Returns null for absent or malformed evidence — the
 * disclosure hides itself rather than rendering partial guesses. Malformed
 * signal entries are skipped individually so one bad entry cannot sink the
 * whole panel.
 *
 * `finalPageKind` is the analysis's own `page_kind`: the evidence dict
 * records the schema suggestion but not the final type, so the conflict flag
 * is derived here.
 */
export function readPageKindEvidence(
  evidence: unknown,
  finalPageKind: string | null,
): PageKindEvidenceView | null {
  if (typeof evidence !== 'object' || evidence === null || Array.isArray(evidence)) {
    return null;
  }
  const record = evidence as Record<string, unknown>;
  const classifiedBy = typeof record.classified_by === 'string' ? record.classified_by : null;
  const confidence = typeof record.confidence === 'number' ? record.confidence : null;
  const confidenceThreshold =
    typeof record.confidence_threshold === 'number' ? record.confidence_threshold : null;
  if (classifiedBy === null || confidence === null || confidenceThreshold === null) {
    return null;
  }
  const signals: PageKindEvidenceSignal[] = [];
  if (Array.isArray(record.signals)) {
    for (const raw of record.signals) {
      if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue;
      const entry = raw as Record<string, unknown>;
      if (
        typeof entry.signal !== 'string' ||
        typeof entry.page_kind !== 'string' ||
        typeof entry.weight !== 'number'
      ) {
        continue;
      }
      signals.push({
        signal: entry.signal,
        pageKind: entry.page_kind,
        weight: entry.weight,
        detail: typeof entry.detail === 'string' ? entry.detail : '',
      });
    }
  }
  const schemaSuggestedType =
    typeof record.schema_suggested_type === 'string' ? record.schema_suggested_type : null;
  return {
    classifierVersion:
      typeof record.classifier_version === 'string' ? record.classifier_version : '',
    classifiedBy,
    schemaSuggestedType,
    confidence,
    confidenceThreshold,
    signals,
    alternatives: readAlternatives(record.alternatives),
    conflicts: readConflicts(record.conflicts),
    otherReason: typeof record.other_reason === 'string' ? record.other_reason : null,
    schemaConflict:
      schemaSuggestedType !== null &&
      finalPageKind !== null &&
      schemaSuggestedType !== finalPageKind,
  };
}
