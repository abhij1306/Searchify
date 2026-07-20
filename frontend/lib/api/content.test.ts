import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import {
  CONTENT_LIST_DEFAULT_LIMIT,
  CONTENT_LIST_POLL_MS,
  CONTENT_DETAIL_POLL_MS,
  contentApi,
} from './content';
import { queryKeys } from './query-keys';
import {
  contentGenerationDetailSchema,
  contentGenerationListItemSchema,
  strictValidate,
} from './schemas';
import { mswServer } from '@/test/msw-server';

const UUID = '11111111-1111-4111-8111-111111111111';
const UUID2 = '22222222-2222-4222-8222-222222222222';

const listItem = {
  id: UUID,
  project_id: UUID2,
  status: 'queued' as const,
  output_type: 'website_page' as const,
  website_context_status: 'included' as const,
  requested_model: 'mistral-small-latest',
  returned_model: null,
  provider: 'mistral',
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
  completed_at: null,
  error_code: '',
  prompt_preview: 'Write a landing page',
};

const detail = {
  ...listItem,
  prompt: 'Write a landing page for Acme.',
  website_context_enabled: true,
  website_context_summary: {
    crawl_id: UUID2,
    crawl_completed_at: '2026-07-14T00:00:00Z',
    extractor_version: 'ex-v1',
    analyzer_version: 'an-v1',
    page_count: 3,
    char_count: 1200,
    site_url_ids: [UUID],
    artifact_ids: [UUID2],
    content_hashes: ['abc123'],
  },
  finish_reason: null,
  output_truncated: false,
  output_text: null,
  usage: null,
  latency_ms: null,
  error_detail: '',
  generator_version: 'content-v1',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('contentApi.listGenerations', () => {
  it('sends project_id + the default limit and validates the bounded list', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get('/api/v1/content/generations', ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json([listItem]);
      }),
    );
    const items = await contentApi.listGenerations(UUID2);
    expect(items).toHaveLength(1);
    expect(items[0].prompt_preview).toBe('Write a landing page');
    const url = new URL(seenUrl);
    expect(url.searchParams.get('project_id')).toBe(UUID2);
    expect(url.searchParams.get('limit')).toBe(String(CONTENT_LIST_DEFAULT_LIMIT));
  });

  it('sends an explicit limit when provided', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get('/api/v1/content/generations', ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json([]);
      }),
    );
    await contentApi.listGenerations(UUID2, 10);
    expect(new URL(seenUrl).searchParams.get('limit')).toBe('10');
  });

  it('fails loud when a list item carries output_text (bounded-list drift)', async () => {
    mswServer.use(
      http.get('/api/v1/content/generations', () =>
        HttpResponse.json([{ ...listItem, output_text: 'leaked' }]),
      ),
    );
    await expect(contentApi.listGenerations(UUID2)).rejects.toThrow(/content.listGenerations/);
  });
});

describe('contentApi.enqueueGeneration', () => {
  it('posts the body with the Idempotency-Key header and validates the detail', async () => {
    let seenKey: string | null = null;
    let seenBody: unknown;
    mswServer.use(
      http.post('/api/v1/content/generations', async ({ request }) => {
        seenKey = request.headers.get('idempotency-key');
        seenBody = await request.json();
        return HttpResponse.json(detail, { status: 201 });
      }),
    );
    const created = await contentApi.enqueueGeneration(
      { project_id: UUID2, prompt: 'Write a landing page for Acme.' },
      'idem-key-1',
    );
    expect(created.id).toBe(UUID);
    expect(created.website_context_summary?.page_count).toBe(3);
    expect(seenKey).toBe('idem-key-1');
    expect(seenBody).toEqual({ project_id: UUID2, prompt: 'Write a landing page for Acme.' });
  });

  it('omits the header when no key is given', async () => {
    let seenKey: string | null = 'sentinel';
    mswServer.use(
      http.post('/api/v1/content/generations', ({ request }) => {
        seenKey = request.headers.get('idempotency-key');
        return HttpResponse.json(detail, { status: 201 });
      }),
    );
    await contentApi.enqueueGeneration({ project_id: UUID2, prompt: 'p' });
    expect(seenKey).toBeNull();
  });
});

describe('contentApi detail + actions', () => {
  it('getGeneration validates the full detail', async () => {
    mswServer.use(
      http.get(`/api/v1/content/generations/${UUID}`, () =>
        HttpResponse.json({
          ...detail,
          status: 'succeeded',
          returned_model: 'mistral-small-latest',
          finish_reason: 'stop',
          output_text: '# Hello',
          usage: { total_tokens: 30 },
          latency_ms: 420,
          completed_at: '2026-07-15T00:01:00Z',
        }),
      ),
    );
    const got = await contentApi.getGeneration(UUID);
    expect(got.status).toBe('succeeded');
    expect(got.output_text).toBe('# Hello');
    expect(got.output_truncated).toBe(false);
  });

  it('cancel/regenerate/try-again post to the action routes', async () => {
    const seen: string[] = [];
    const record = (suffix: string) =>
      http.post(`/api/v1/content/generations/${UUID}/${suffix}`, () => {
        seen.push(suffix);
        return HttpResponse.json(
          suffix === 'cancel'
            ? { ...detail, status: 'cancelled', error_code: 'cancelled' }
            : detail,
          { status: suffix === 'cancel' ? 200 : 201 },
        );
      });
    mswServer.use(record('cancel'), record('regenerate'), record('try-again'));
    const cancelled = await contentApi.cancelGeneration(UUID);
    expect(cancelled.status).toBe('cancelled');
    await contentApi.regenerateGeneration(UUID);
    await contentApi.tryAgainGeneration(UUID);
    expect(seen).toEqual(['cancel', 'regenerate', 'try-again']);
  });
});

describe('content schemas (drift policy)', () => {
  it('rejects a numeric id', () => {
    expect(() =>
      strictValidate(contentGenerationListItemSchema, { ...listItem, id: 123 }, 'test'),
    ).toThrow(/test/);
  });

  it('rejects a detail missing output_truncated or requested_model', () => {
    const { output_truncated: _t, ...noTruncated } = detail;
    expect(() => strictValidate(contentGenerationDetailSchema, noTruncated, 'test')).toThrow();
    const { requested_model: _m, ...noModel } = detail;
    expect(() => strictValidate(contentGenerationDetailSchema, noModel, 'test')).toThrow();
  });

  it('rejects a generic model field (provenance is requested/returned only)', () => {
    expect(() =>
      strictValidate(contentGenerationDetailSchema, { ...detail, model: 'gpt-4o' }, 'test'),
    ).toThrow();
  });
});

describe('content query keys + poll constants', () => {
  it('list key includes projectId + limit; detail key includes the id', () => {
    expect(queryKeys.content.list(UUID2, 50)).toEqual(['content', 'list', UUID2, 50]);
    expect(queryKeys.content.detail(UUID)).toEqual(['content', 'detail', UUID]);
  });

  it('poll cadences are the plan-specified values', () => {
    expect(CONTENT_LIST_POLL_MS).toBe(3000);
    expect(CONTENT_DETAIL_POLL_MS).toBe(2000);
  });
});
