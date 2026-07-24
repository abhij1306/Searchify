'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Package, Plus, Upload } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { IconChip } from '@/components/ui/icon-chip';
import { Skeleton } from '@/components/ui/skeleton';
import { displayHeadingLgClasses } from '@/components/ui/typography';
import { productsApi, type ProductInput } from '@/lib/api/products';
import { queryKeys } from '@/lib/api/query-keys';
import type { Product } from '@/lib/api/types';
import { useCatalogQueries } from '@/lib/products/use-products-screen';

import { CatalogTable } from './catalog-table';
import { ProductFormDialog } from './product-form-dialog';
import { ProductImportDialog } from './product-import-dialog';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Catalog tab container (agentic commerce). Owns the catalog query and every
 * CRUD + import mutation; renders the toolbar (Add product / Import CSV) above
 * the catalog table, with the midnight empty state when the catalog is empty.
 */
export function CatalogPanel({ projectId }: Readonly<{ projectId: string }>) {
  const queryClient = useQueryClient();
  const { productsQuery } = useCatalogQueries(projectId);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Product | undefined>(undefined);
  const [importOpen, setImportOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.products.list(projectId) });
  };

  const createMutation = useMutation({
    mutationFn: (input: ProductInput) => productsApi.create(projectId, input),
    onSuccess: async () => {
      await invalidate();
      setFormOpen(false);
      setEditing(undefined);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (vars: { id: string; input: ProductInput }) =>
      productsApi.update(vars.id, vars.input),
    onSuccess: async () => {
      await invalidate();
      setFormOpen(false);
      setEditing(undefined);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => productsApi.remove(id),
    onSettled: () => setBusyId(null),
    onSuccess: invalidate,
  });

  const importMutation = useMutation({
    mutationFn: (rows: ProductInput[]) => productsApi.importRows(projectId, rows),
    onSuccess: async () => {
      await invalidate();
      setImportOpen(false);
    },
  });

  if (productsQuery.isLoading) {
    return (
      <Card aria-hidden>
        <CardContent className="grid gap-3">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (productsQuery.isError) {
    return (
      <Alert tone="danger">
        Could not load the product catalog.{' '}
        <button type="button" className="underline" onClick={() => productsQuery.refetch()}>
          Retry
        </button>
      </Alert>
    );
  }

  const products = productsQuery.data ?? [];

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <p className="text-secondary text-sm">
          {products.length} product{products.length === 1 ? '' : 's'} in the catalog
        </p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => setImportOpen(true)}>
            <Upload className="size-4" aria-hidden />
            Import CSV
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus className="size-4" aria-hidden />
            Add product
          </Button>
        </div>
      </div>

      {deleteMutation.isError ? (
        <Alert tone="danger">{errorMessage(deleteMutation.error)}</Alert>
      ) : null}

      {products.length === 0 ? (
        <Card>
          <CardContent className="grid justify-items-center gap-4 py-12 text-center">
            <CardEyebrow>Catalog</CardEyebrow>
            <IconChip>
              <Package className="size-6" aria-hidden />
            </IconChip>
            <div className="grid gap-1">
              <h2 className={displayHeadingLgClasses}>No products yet</h2>
              <p className="text-secondary max-w-md text-sm">
                Add the products you sell — manually or via CSV — so audits can measure how AI
                answer engines rank and price them against competitor products.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="md" onClick={() => setImportOpen(true)}>
                Import CSV
              </Button>
              <Button variant="primary" size="md" onClick={() => setFormOpen(true)}>
                Add your first product
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <CatalogTable
          products={products}
          busyId={busyId}
          onEdit={(product) => {
            setEditing(product);
            setFormOpen(true);
          }}
          onDelete={(product) => {
            setBusyId(product.id);
            deleteMutation.mutate(product.id);
          }}
        />
      )}

      <ProductFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        product={editing}
        isSaving={createMutation.isPending || updateMutation.isPending}
        error={
          createMutation.isError
            ? errorMessage(createMutation.error)
            : updateMutation.isError
              ? errorMessage(updateMutation.error)
              : undefined
        }
        onSubmit={async (input) => {
          if (editing) {
            await updateMutation.mutateAsync({ id: editing.id, input });
          } else {
            await createMutation.mutateAsync(input);
          }
        }}
      />

      <ProductImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        isImporting={importMutation.isPending}
        error={importMutation.isError ? errorMessage(importMutation.error) : undefined}
        onImport={async (rows) => {
          await importMutation.mutateAsync(rows);
        }}
      />
    </div>
  );
}
