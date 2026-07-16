import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('apiClient', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls a relative same-origin /api/v1 base URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.get('/ping');

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/v1/ping');
  });

  it('throws ApiError with status and request id on 4xx', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('Bad Request', {
        status: 400,
        headers: { 'x-request-id': 'req-abc' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient, ApiError } = await import('./client');
    await expect(apiClient.get('/thing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      requestId: 'req-abc',
    });
    // 4xx is not retried.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(ApiError).toBeDefined();
  });

  it('throws ApiError on 5xx', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('Boom', { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(apiClient.get('/thing')).rejects.toMatchObject({ status: 503 });
  });

  it('retries idempotent GET network failures then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(apiClient.get('/ping', { retryNetworkFailures: true })).resolves.toEqual({
      ok: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retry ordinary GET network failures', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('offline'));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(apiClient.get('/ping')).rejects.toThrow('offline');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not retry mutation network failures', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('offline'));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(apiClient.post('/audits', { project_id: 'x' })).rejects.toThrow('offline');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('forwards AbortSignal and explicit request id', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.get('/ping', { signal: controller.signal, requestId: 'req-test' });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
    expect(init.credentials).toBe('include');
    expect(init.cache).toBe('no-store');
    expect(new Headers(init.headers).get('X-Request-ID')).toBe('req-test');
  });

  it('rejects when an aborted signal fires during a retry backoff', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementation(() => {
      controller.abort();
      return Promise.reject(new Error('offline'));
    });
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(
      apiClient.get('/ping', { retryNetworkFailures: true, signal: controller.signal }),
    ).rejects.toBeTruthy();
  });

  it('keeps ordinary GET requests header-free to avoid a CORS preflight', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.get('/ping');

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get('X-Request-ID')).toBeNull();
  });

  it('stamps a generated request id on mutations', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.post('/audits', { project_id: 'x' });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get('X-Request-ID')).toBeTruthy();
  });

  it('throws ApiError when a 2xx response is not JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('<html>ok</html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await expect(apiClient.get('/thing')).rejects.toThrow('Expected JSON response from API.');
  });

  it('httpErrorStatus reads status from ApiError and duck-typed errors', async () => {
    const { ApiError, httpErrorStatus } = await import('./client');
    expect(httpErrorStatus(new ApiError('x', 403, '{}'))).toBe(403);
    expect(httpErrorStatus({ status: 401 })).toBe(401);
    expect(httpErrorStatus(new Error('no'))).toBeUndefined();
  });
});
