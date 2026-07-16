import { describe, expect, it } from 'vitest';

import { ApiError } from './errors';
import { createAppQueryClient, shouldRetryQuery } from './query-client';

describe('shouldRetryQuery', () => {
  it('retries network / unknown errors up to the cap', () => {
    expect(shouldRetryQuery(0, new Error('offline'))).toBe(true);
    expect(shouldRetryQuery(1, new Error('offline'))).toBe(true);
    expect(shouldRetryQuery(2, new Error('offline'))).toBe(false);
  });

  it('retries 408 / 429 / 5xx', () => {
    expect(shouldRetryQuery(0, new ApiError('timeout', 408, ''))).toBe(true);
    expect(shouldRetryQuery(0, new ApiError('rate', 429, ''))).toBe(true);
    expect(shouldRetryQuery(0, new ApiError('down', 500, ''))).toBe(true);
    expect(shouldRetryQuery(0, new ApiError('down', 503, ''))).toBe(true);
  });

  it('does not retry ordinary 4xx', () => {
    expect(shouldRetryQuery(0, new ApiError('bad', 400, ''))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError('unauth', 401, ''))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError('forbidden', 403, ''))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError('missing', 404, ''))).toBe(false);
  });

  it('never retries aborts', () => {
    expect(shouldRetryQuery(0, new DOMException('Aborted', 'AbortError'))).toBe(false);
  });

  it('caps retries at 2 regardless of error kind', () => {
    expect(shouldRetryQuery(2, new ApiError('down', 503, ''))).toBe(false);
  });
});

describe('createAppQueryClient', () => {
  it('installs the retry policy and stale defaults', () => {
    const client = createAppQueryClient();
    const queries = client.getDefaultOptions().queries;
    expect(queries?.retry).toBe(shouldRetryQuery);
    expect(queries?.staleTime).toBe(15_000);
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  });
});
