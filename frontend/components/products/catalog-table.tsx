'use client';

import Link from 'next/link';
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownSeparator,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TablePagination, useTablePage } from '@/components/ui/table-pagination';
import { Tooltip } from '@/components/ui/tooltip';
import type { Product, ProductCompleteness } from '@/lib/api/types';
import { formatPrice } from '@/lib/products/catalog';

/** Rows per page on the catalog table (client-side; the list arrives whole). */
const PAGE_SIZE = 10;

/**
 * Catalog table (agentic commerce). Dense SKU table with columns product
 * (name + first variant), sku, price, variants count, completeness badge
 * (missing attributes in a tooltip), and origin, plus per-row edit/delete
 * actions. The product name links to the `/products/[productId]` evidence
 * drill-down. Purely presentational — CRUD is delegated to callbacks owned by
 * the catalog panel.
 */
export function CatalogTable({
  products,
  onEdit,
  onDelete,
  busyId,
}: Readonly<{
  products: Product[];
  onEdit: (product: Product) => void;
  onDelete: (product: Product) => void;
  busyId?: string | null;
}>) {
  const { page, setPage, pageCount, from, to } = useTablePage(products.length, PAGE_SIZE);
  const pagedProducts = products.slice(from - 1, to);

  return (
    <div className="border-border bg-panel shadow-card overflow-hidden rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead>SKU</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Variants</TableHead>
            <TableHead>Attributes</TableHead>
            <TableHead>Origin</TableHead>
            <TableHead className="w-16 text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pagedProducts.map((product) => (
            <TableRow key={product.id}>
              <TableCell className="max-w-[320px] min-w-[200px]">
                <div className="grid gap-0.5">
                  <Link
                    href={`/products/${product.id}`}
                    className="text-foreground hover:text-accent-text truncate font-medium transition-colors"
                  >
                    {product.name}
                  </Link>
                  {product.variants[0]?.name ? (
                    <span className="text-muted truncate text-xs">{product.variants[0].name}</span>
                  ) : null}
                </div>
              </TableCell>
              <TableCell className="text-secondary font-mono text-xs">{product.sku}</TableCell>
              <TableCell numeric className="text-secondary">
                {formatPrice(product.price, product.currency)}
              </TableCell>
              <TableCell numeric className="text-secondary">
                {product.variants.length > 0 ? product.variants.length : '—'}
              </TableCell>
              <TableCell>
                <CompletenessBadge completeness={product.completeness} />
              </TableCell>
              <TableCell>
                <Badge variant="neutral">{product.origin}</Badge>
              </TableCell>
              <TableCell className="text-right">
                <Dropdown>
                  <DropdownTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label={`Actions for ${product.name}`}>
                      <MoreHorizontal className="size-4" aria-hidden />
                    </Button>
                  </DropdownTrigger>
                  <DropdownContent align="end">
                    <DropdownItem onSelect={() => onEdit(product)}>
                      <Pencil className="size-4" aria-hidden />
                      Edit
                    </DropdownItem>
                    <DropdownSeparator />
                    <DropdownItem
                      disabled={busyId === product.id}
                      onSelect={() => onDelete(product)}
                      className="text-danger-text data-[highlighted]:bg-danger-bg"
                    >
                      <Trash2 className="size-4" aria-hidden />
                      Delete
                    </DropdownItem>
                  </DropdownContent>
                </Dropdown>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination
        page={page}
        pageCount={pageCount}
        from={from}
        to={to}
        total={products.length}
        noun="products"
        onPageChange={setPage}
      />
    </div>
  );
}

/**
 * The data-quality badge: `12/12` (success when complete, neutral otherwise)
 * with the missing attribute list on hover — the badge is never color-only
 * (the `N missing` text carries the meaning).
 */
export function CompletenessBadge({
  completeness,
}: Readonly<{ completeness: ProductCompleteness }>) {
  const complete = completeness.missing.length === 0;
  const label = `${completeness.present}/${completeness.total}`;
  const badge = complete ? (
    <Badge variant="status" value="success">
      {label} · Complete
    </Badge>
  ) : (
    <Badge variant="status" value="warning">
      {label} · {completeness.missing.length} missing
    </Badge>
  );
  if (complete) return badge;
  return <Tooltip content={`Missing: ${completeness.missing.join(', ')}`}>{badge}</Tooltip>;
}
