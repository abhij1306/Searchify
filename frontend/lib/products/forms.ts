/**
 * Add/edit product form values (agentic commerce): zod schema + mappers
 * between the flat form shape and the API `ProductInput`. Attribute fields
 * (brand/category/gtin/availability/description) fold into
 * `ProductInput.attributes` — they feed the completeness badge.
 */
import { z } from 'zod';

import type { ProductInput } from '@/lib/api/products';
import type { Product } from '@/lib/api/types';

export const CURRENCY_OPTIONS = ['USD', 'EUR', 'GBP', 'CAD', 'AUD'] as const;

export const AVAILABILITY_OPTIONS = ['', 'in_stock', 'out_of_stock', 'preorder'] as const;

export const availabilityLabels: Record<string, string> = {
  '': 'Not specified',
  in_stock: 'In stock',
  out_of_stock: 'Out of stock',
  preorder: 'Preorder',
};

export const productFormSchema = z.object({
  name: z.string().trim().min(1, 'Product name is required.').max(255),
  sku: z.string().trim().min(1, 'SKU is required.').max(128),
  variant: z.string().trim().max(255),
  category: z.string().trim().max(255),
  url: z.string().trim().max(2048),
  price: z
    .string()
    .trim()
    .refine((value) => value === '' || Number.isFinite(Number(value)), {
      message: 'Price must be a number.',
    })
    .refine((value) => value === '' || Number(value) >= 0, {
      message: 'Price must not be negative.',
    }),
  currency: z.string().trim().max(3),
  brand: z.string().trim().max(255),
  gtin: z.string().trim().max(64),
  availability: z.string(),
  description: z.string().trim().max(2000),
  aliases: z.string().trim(),
});

export type ProductFormValues = z.infer<typeof productFormSchema>;

export const emptyProductForm: ProductFormValues = {
  name: '',
  sku: '',
  variant: '',
  category: '',
  url: '',
  price: '',
  currency: 'USD',
  brand: '',
  gtin: '',
  availability: '',
  description: '',
  aliases: '',
};

/** Prefill the form from an existing product (edit mode). */
export function productToFormValues(product: Product): ProductFormValues {
  const attributes = product.attributes ?? {};
  const attr = (key: string) => {
    const value = attributes[key];
    return typeof value === 'string' ? value : '';
  };
  return {
    name: product.name,
    sku: product.sku,
    variant: product.variants[0]?.name ?? '',
    category: attr('category'),
    url: product.url,
    price: product.price !== null ? String(product.price) : '',
    currency: product.currency || 'USD',
    brand: attr('brand'),
    gtin: attr('gtin'),
    availability: attr('availability'),
    description: attr('description'),
    aliases: product.aliases.join(', '),
  };
}

// Attribute keys the form owns (and may therefore overwrite or clear).
const FORM_ATTRIBUTE_KEYS = ['brand', 'category', 'gtin', 'availability', 'description'] as const;

/** Map validated form values to the API input. */
export function formValuesToProductInput(values: ProductFormValues): ProductInput {
  const attributes: Record<string, unknown> = {};
  for (const key of FORM_ATTRIBUTE_KEYS) {
    const value = values[key].trim();
    if (value) attributes[key] = value;
  }
  return {
    sku: values.sku,
    name: values.name,
    aliases: values.aliases
      ? values.aliases
          .split(',')
          .map((alias) => alias.trim())
          .filter(Boolean)
      : [],
    variants: values.variant ? [{ name: values.variant }] : [],
    price: values.price === '' ? null : Number(values.price),
    currency: values.currency.toUpperCase(),
    url: values.url,
    attributes,
  };
}

/**
 * Map validated form values to the API input for an EDIT of `product`.
 *
 * The form manages only a subset of the catalog row (one variant name and
 * the FORM_ATTRIBUTE_KEYS bag). The backend replaces `attributes`/`variants`
 * wholesale when they are sent, so an edit must merge: attribute keys and
 * variants the form doesn't own (e.g. from a CSV import) are preserved,
 * otherwise editing just the name would silently wipe them.
 */
export function formValuesToProductUpdate(
  product: Product,
  values: ProductFormValues,
): ProductInput {
  const input = formValuesToProductInput(values);
  const attributes: Record<string, unknown> = { ...(product.attributes ?? {}) };
  for (const key of FORM_ATTRIBUTE_KEYS) delete attributes[key];
  Object.assign(attributes, input.attributes);
  const existing = product.variants ?? [];
  const variants = values.variant.trim()
    ? [{ ...existing[0], name: values.variant.trim() }, ...existing.slice(1)]
    : existing;
  return { ...input, attributes, variants };
}
