'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ProductEvidenceTable } from '@/components/products/product-evidence-table';
import { productsApi } from '@/lib/api/products';
import { queryKeys } from '@/lib/api/query-keys';
import { ApiError } from '@/lib/api/errors';

/**
 * Product drill-down (agentic commerce): the product header plus its
 * persisted mention evidence (engine / prompt / rank / price match / source
 * execution links). Reads persisted rows only.
 */
export default function ProductDetailPage() {
  const params = useParams<{ productId: string }>();
  const productId = params.productId;

  const productQuery = useQuery({
    queryKey: queryKeys.products.detail(productId),
    queryFn: ({ signal }) => productsApi.get(productId, { signal }),
  });

  if (productQuery.isLoading) {
    return (
      <div className="grid gap-4" aria-hidden>
        <Skeleton className="h-8 w-40" />
        <Card>
          <CardContent className="grid gap-3">
            <Skeleton className="h-10 w-80" />
            <Skeleton className="h-56 w-full" />
          </CardContent>
        </Card>
      </div>
    );
  }

  if (productQuery.isError || !productQuery.data) {
    const notFound = productQuery.error instanceof ApiError && productQuery.error.status === 404;
    return (
      <Card>
        <CardContent className="grid justify-items-center gap-3 py-10 text-center">
          <p className="text-secondary text-sm">
            {notFound
              ? 'This product is not in your workspace catalog.'
              : 'Could not load this product.'}
          </p>
          <Button asChild variant="ghost" size="sm">
            <Link href="/products">Back to Products</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <TooltipProvider>
      <ProductEvidenceTable product={productQuery.data} />
    </TooltipProvider>
  );
}
