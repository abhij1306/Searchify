/**
 * Products domain endpoints (agentic commerce): catalog CRUD, CSV/JSON import,
 * competitor products, and the product visibility projections — the
 * selected-audit dashboard, per-product mention evidence, and the CSV export
 * URL. Projections read persisted rows only (backend invariant 7) and default
 * to the project's latest completed audit when `audit_id` is omitted. Every
 * JSON response passes through `strictValidate`.
 */
import { z } from 'zod';

import { API_BASE_URL, apiClient, type ApiRequestOptions } from './client';
import {
  competitorProductSchema,
  productEvidenceResponseSchema,
  productSchema,
  productVisibilitySchema,
  strictValidate,
} from './schemas';
import { definedQuery, withQuery } from './shared';
import type {
  CompetitorProduct,
  Product,
  ProductEvidenceResponse,
  ProductVisibility,
} from './types';

const productListSchema = z.array(productSchema);
const competitorProductListSchema = z.array(competitorProductSchema);

/** `POST /projects/{id}/products` body (backend `ProductInput`). */
export type ProductInput = {
  sku: string;
  name: string;
  aliases?: string[];
  variants?: { name: string; sku?: string; price?: number | null }[];
  price?: number | null;
  // ISO-4217; the backend normalizes to uppercase.
  currency?: string;
  url?: string;
  attributes?: Record<string, unknown>;
};

export type ProductUpdateInput = Partial<ProductInput>;

/** `POST /projects/{id}/competitor-products` body (backend `CompetitorProductInput`). */
export type CompetitorProductInput = {
  competitor_id: string;
  name: string;
  aliases?: string[];
  price?: number | null;
  currency?: string;
  url?: string;
};

export type CompetitorProductUpdateInput = Partial<Omit<CompetitorProductInput, 'competitor_id'>>;

/** Filters for the product mention-evidence request (all optional). */
export type ProductEvidenceParams = {
  /** Restrict to one audit in the authorized project. */
  audit_id?: string;
  /** Logical engine slice (`chatgpt` | `gemini` | `claude`); omit for all. */
  engine?: string;
  /** Newest-window size (backend default 100, max 500). */
  limit?: number;
};

export const productsApi = {
  list: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Product[]>(`/projects/${projectId}/products`, options);
    return strictValidate(productListSchema, res, 'products.list');
  },
  create: async (projectId: string, input: ProductInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<Product>(`/projects/${projectId}/products`, input, options);
    return strictValidate(productSchema, res, 'products.create');
  },
  get: async (productId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Product>(`/products/${productId}`, options);
    return strictValidate(productSchema, res, 'products.get');
  },
  update: async (productId: string, input: ProductUpdateInput, options?: ApiRequestOptions) => {
    const res = await apiClient.patch<Product>(`/products/${productId}`, input, options);
    return strictValidate(productSchema, res, 'products.update');
  },
  remove: (productId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/products/${productId}`, options),
  /** Multipart CSV import; returns the full refreshed catalog (dupes dropped). */
  importCsv: async (projectId: string, file: File, options?: ApiRequestOptions) => {
    const form = new FormData();
    form.append('file', file);
    const res = await apiClient.postForm<Product[]>(
      `/projects/${projectId}/products/import`,
      form,
      options,
    );
    return strictValidate(productListSchema, res, 'products.importCsv');
  },
  /**
   * Persist browser-parsed rows through the same `/import` endpoint (the
   * backend accepts a JSON body of `{ products: [...] }`); returns the full
   * refreshed catalog with `origin='imported'` on new rows.
   */
  importRows: async (projectId: string, rows: ProductInput[], options?: ApiRequestOptions) => {
    const res = await apiClient.post<Product[]>(
      `/projects/${projectId}/products/import`,
      { products: rows },
      options,
    );
    return strictValidate(productListSchema, res, 'products.importRows');
  },
  listCompetitorProducts: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<CompetitorProduct[]>(
      `/projects/${projectId}/competitor-products`,
      options,
    );
    return strictValidate(competitorProductListSchema, res, 'products.listCompetitorProducts');
  },
  createCompetitorProduct: async (
    projectId: string,
    input: CompetitorProductInput,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<CompetitorProduct>(
      `/projects/${projectId}/competitor-products`,
      input,
      options,
    );
    return strictValidate(competitorProductSchema, res, 'products.createCompetitorProduct');
  },
  updateCompetitorProduct: async (
    competitorProductId: string,
    input: CompetitorProductUpdateInput,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.patch<CompetitorProduct>(
      `/competitor-products/${competitorProductId}`,
      input,
      options,
    );
    return strictValidate(competitorProductSchema, res, 'products.updateCompetitorProduct');
  },
  removeCompetitorProduct: (competitorProductId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/competitor-products/${competitorProductId}`, options),
  /**
   * Selected-audit product dashboard (defaults to the latest product audit).
   * `engine` slices entries to their persisted per-engine aggregate.
   */
  getProductVisibility: async (
    projectId: string,
    params?: { audit_id?: string; engine?: string },
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.get<ProductVisibility>(
      withQuery(`/projects/${projectId}/products/visibility`, definedQuery(params)),
      options,
    );
    return strictValidate(productVisibilitySchema, res, 'products.getProductVisibility');
  },
  /** Persisted mention evidence for one product (bounded, newest-first). */
  getProductEvidence: async (
    productId: string,
    params?: ProductEvidenceParams,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.get<ProductEvidenceResponse>(
      withQuery(`/products/${productId}/visibility/evidence`, definedQuery(params)),
      options,
    );
    return strictValidate(productEvidenceResponseSchema, res, 'products.getProductEvidence');
  },
  /** Same-origin export URL (browser navigation / download link). */
  exportCsvUrl: (projectId: string, auditId?: string) =>
    withQuery(
      `${API_BASE_URL}/projects/${projectId}/products/visibility/export.csv`,
      definedQuery({ audit_id: auditId }),
    ),
};
