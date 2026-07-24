'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ChevronLeft, Info } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { productsApi } from '@/lib/api/products';
import { queryKeys } from '@/lib/api/query-keys';
import type { Product, ProductEvidenceItem } from '@/lib/api/types';
import { formatPrice, type ProductEngineFilter } from '@/lib/products/catalog';
import { engineLabel } from '@/lib/providers/catalog';

import { EngineFilterDropdown } from './engine-filter-dropdown';

/** Newest-window size for the evidence request (backend max 500). */
const EVIDENCE_LIMIT = 100;

/**
 * Product evidence drill-down (`/products/[productId]`): the persisted
 * mention evidence for one catalog product — engine, frozen prompt text,
 * rank, extracted price vs catalog (match badge), excerpt offset, and a link
 * to the source execution at `/runs/[runId]/executions/[executionId]`.
 * Bounded newest-first list with a truncation notice (mirrors
 * `mentions-citations.tsx`).
 */
export function ProductEvidenceTable({
  product,
  backHref = '/products',
}: Readonly<{ product: Product; backHref?: string }>) {
  const [engine, setEngine] = useState<ProductEngineFilter>('all');
  const engineParam = engine === 'all' ? undefined : engine;

  const evidenceQuery = useQuery({
    queryKey: queryKeys.products.evidence(product.id, {
      engine: engineParam ?? null,
      limit: EVIDENCE_LIMIT,
    }),
    queryFn: ({ signal }) =>
      productsApi.getProductEvidence(
        product.id,
        { engine: engineParam, limit: EVIDENCE_LIMIT },
        { signal },
      ),
  });

  return (
    <div className="grid gap-4">
      <div>
        <Button asChild variant="ghost" size="sm">
          <Link href={backHref}>
            <ChevronLeft className="size-4" aria-hidden />
            Products
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3">
          <div className="grid gap-1">
            <CardEyebrow>Product</CardEyebrow>
            <CardTitle>{product.name}</CardTitle>
            <p className="text-secondary text-sm">
              <span className="font-mono text-xs">{product.sku}</span>
              {' · '}
              {formatPrice(product.price, product.currency)}
              {' · '}
              {product.completeness.present}/{product.completeness.total} attributes
            </p>
          </div>
          <EngineFilterDropdown engine={engine} onChange={setEngine} />
        </CardHeader>

        <CardContent className="p-0">
          {evidenceQuery.isLoading ? (
            <div className="grid gap-3 p-[var(--card-padding)]" aria-hidden>
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-2/3" />
            </div>
          ) : evidenceQuery.isError ? (
            <div className="p-[var(--card-padding)]">
              <Alert tone="danger">
                Could not load this product&apos;s evidence.{' '}
                <button
                  type="button"
                  className="underline"
                  onClick={() => evidenceQuery.refetch()}
                >
                  Retry
                </button>
              </Alert>
            </div>
          ) : (evidenceQuery.data?.items ?? []).length === 0 ? (
            <p className="text-secondary p-[var(--card-padding)] text-sm">
              {engineParam
                ? `No persisted mentions of this product on ${engineLabel(engineParam)} yet.`
                : 'No persisted mentions of this product yet. Once a run completes, every mention appears here.'}
            </p>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Engine</TableHead>
                    <TableHead className="min-w-[220px]">Prompt</TableHead>
                    <TableHead>Rank</TableHead>
                    <TableHead>Price mentioned</TableHead>
                    <TableHead>vs catalog</TableHead>
                    <TableHead>Offset</TableHead>
                    <TableHead className="text-right">Execution</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(evidenceQuery.data?.items ?? []).map((item) => (
                    <EvidenceRow key={item.mention_id} item={item} product={product} />
                  ))}
                </TableBody>
              </Table>
              {evidenceQuery.data?.truncated ? (
                <div className="border-border-subtle text-muted flex items-center gap-2 border-t px-4 py-2.5 text-xs">
                  <Info className="size-3.5 shrink-0" aria-hidden />
                  <span>
                    Showing newest {EVIDENCE_LIMIT} mentions; refine filters to narrow results.
                  </span>
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function EvidenceRow({
  item,
  product,
}: Readonly<{ item: ProductEvidenceItem; product: Product }>) {
  return (
    <TableRow>
      <TableCell>
        <Badge variant="neutral">{engineLabel(item.logical_engine)}</Badge>
      </TableCell>
      <TableCell className="max-w-[320px]">
        <span className="text-foreground line-clamp-2 block text-sm">{item.prompt_text}</span>
        <span className="text-muted text-xs">
          #{item.prompt_index} · rep {item.repetition}
        </span>
      </TableCell>
      <TableCell numeric className="text-secondary">
        {item.rank_position !== null ? `#${item.rank_position}` : '—'}
      </TableCell>
      <TableCell numeric className="text-secondary">
        {item.price_value !== null ? (
          <span title={item.price_text}>{formatPrice(item.price_value, item.price_currency)}</span>
        ) : (
          '—'
        )}
      </TableCell>
      <TableCell>
        {item.price_matches_catalog === null ? (
          <span className="text-subtle">—</span>
        ) : item.price_matches_catalog ? (
          <Badge variant="status" value="success">
            Match
          </Badge>
        ) : (
          <Badge
            variant="status"
            value="warning"
          >{`catalog ${formatPrice(product.price, product.currency)}`}</Badge>
        )}
      </TableCell>
      <TableCell numeric className="text-secondary">
        {item.first_offset !== null ? item.first_offset : '—'}
      </TableCell>
      <TableCell className="text-right">
        <Link
          href={`/runs/${item.audit_id}/executions/${item.task_id}`}
          className="text-accent-text text-sm hover:underline"
        >
          View
        </Link>
      </TableCell>
    </TableRow>
  );
}
