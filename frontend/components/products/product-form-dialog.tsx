'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input, Textarea, inputClasses } from '@/components/ui/input';
import type { ProductInput } from '@/lib/api/products';
import type { Product } from '@/lib/api/types';
import {
  AVAILABILITY_OPTIONS,
  CURRENCY_OPTIONS,
  availabilityLabels,
  emptyProductForm,
  formValuesToProductInput,
  productFormSchema,
  productToFormValues,
  type ProductFormValues,
} from '@/lib/products/forms';

/**
 * Add / edit product dialog (agentic commerce, mirrors prompt-form-dialog).
 * react-hook-form + zod; the same form serves create (no `product`) and edit
 * (prefilled from `product`). Attribute fields feed the completeness badge.
 * Submit maps to the API `ProductInput` and delegates persistence to
 * `onSubmit`.
 */
export function ProductFormDialog({
  open,
  onOpenChange,
  product,
  onSubmit,
  isSaving,
  error,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  product?: Product;
  onSubmit: (input: ProductInput) => Promise<void> | void;
  isSaving?: boolean;
  error?: string;
}>) {
  const isEdit = Boolean(product);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ProductFormValues>({
    resolver: zodResolver(productFormSchema),
    values: product ? productToFormValues(product) : emptyProductForm,
  });

  const submit = handleSubmit(async (values) => {
    await onSubmit(formValuesToProductInput(values));
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) reset(product ? productToFormValues(product) : emptyProductForm);
    onOpenChange(next);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      title={isEdit ? 'Edit product' : 'Add product'}
      description="Add a single SKU to the catalog. Attributes feed the completeness score used in visibility audits."
      className="w-[720px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={isSaving}>
            {isSaving ? 'Saving…' : isEdit ? 'Save changes' : 'Add product'}
          </Button>
        </>
      }
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="grid gap-4"
      >
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Product name" required error={errors.name?.message}>
            {(props) => (
              <Input {...props} {...register('name')} placeholder="VoltCity Commuter 500" />
            )}
          </Field>
          <Field label="SKU" required error={errors.sku?.message}>
            {(props) => <Input {...props} {...register('sku')} placeholder="VC-EB500-GR" />}
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Variant" hint="Optional" error={errors.variant?.message}>
            {(props) => (
              <Input {...props} {...register('variant')} placeholder="Graphite / Standard" />
            )}
          </Field>
          <Field label="Category" error={errors.category?.message}>
            {(props) => <Input {...props} {...register('category')} placeholder="E-Bikes" />}
          </Field>
        </div>

        <Field
          label="Product URL"
          error={errors.url?.message}
          hint="The owned page audits compare extracted prices against."
        >
          {(props) => (
            <Input
              {...props}
              {...register('url')}
              placeholder="https://example.com/products/voltcity-500"
            />
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Price" error={errors.price?.message}>
            {(props) => (
              <Input
                {...props}
                {...register('price')}
                inputMode="decimal"
                placeholder="2499.00"
              />
            )}
          </Field>
          <Field label="Currency" error={errors.currency?.message}>
            {(props) => (
              <select {...props} {...register('currency')} className={inputClasses}>
                {CURRENCY_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Brand" error={errors.brand?.message}>
            {(props) => <Input {...props} {...register('brand')} placeholder="Voltaic" />}
          </Field>
          <Field label="GTIN / barcode" error={errors.gtin?.message}>
            {(props) => <Input {...props} {...register('gtin')} placeholder="09312345678901" />}
          </Field>
          <Field label="Availability" error={errors.availability?.message}>
            {(props) => (
              <select {...props} {...register('availability')} className={inputClasses}>
                {AVAILABILITY_OPTIONS.map((value) => (
                  <option key={value || 'unspecified'} value={value}>
                    {availabilityLabels[value]}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>

        <Field label="Aliases" hint="Comma-separated" error={errors.aliases?.message}>
          {(props) => (
            <Input {...props} {...register('aliases')} placeholder="VoltCity 500, VC 500" />
          )}
        </Field>

        <Field label="Description" error={errors.description?.message}>
          {(props) => (
            <Textarea
              {...props}
              {...register('description')}
              placeholder="Lightweight commuter e-bike with a 500 Wh battery."
            />
          )}
        </Field>
      </form>
    </Dialog>
  );
}
