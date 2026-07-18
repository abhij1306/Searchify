/**
 * Typed HTTP transport (F2).
 *
 * Same-origin only: the browser always calls a **relative** base (`/api/v1`);
 * Next.js `rewrites()` proxies `/api/:path*` to the server-only `BACKEND_ORIGIN`
 * (invariant 12). The browser never sees a cross-origin backend URL, so there is
 * no CORS preflight and no cross-origin cookie handling.
 *
 * Guarantees:
 *   - `ApiError` on any non-2xx response, carrying status + body + request id.
 *   - `X-Request-ID` header on mutations (and GETs that opt in) for tracing.
 *   - `AbortSignal` forwarding.
 *   - `credentials: 'include'` (HttpOnly JWT cookie) and `cache: 'no-store'`.
 *   - bounded network-failure retry (max 2 attempts) for GET / idempotent calls
 *     only — never for ordinary mutations.
 *   - JSON enforcement: a 2xx response that is not JSON is a contract violation.
 */
import { ApiError, isAbortError } from './errors';

/** Relative API base. Same-origin; proxied to BACKEND_ORIGIN by next.config rewrites. */
export const API_BASE_URL = '/api/v1';

/**
 * Active workspace id, stamped as `X-Workspace-Id` on every request when set.
 *
 * The backend's `require_active_workspace` (B3) reads this header to scope flat
 * (non-path) routes to the selected workspace, falling back to the caller's
 * default workspace when it is absent (deps.py). The shell's project context
 * (F5) calls `setActiveWorkspaceId(project.workspace_id)` whenever the active
 * project changes, so downstream project/prompt/provider/run queries are scoped
 * to the workspace the user is looking at. Same-origin proxy means a custom
 * header never triggers a CORS preflight.
 */
let activeWorkspaceId: string | null = null;

/** Set (or clear with `null`) the workspace id sent on subsequent requests. */
export function setActiveWorkspaceId(workspaceId: string | null) {
  activeWorkspaceId = workspaceId;
}

/** Current workspace id stamped on requests, or `null` (backend default). */
export function getActiveWorkspaceId() {
  return activeWorkspaceId;
}

export type ApiRequestOptions = {
  signal?: AbortSignal;
  headers?: HeadersInit;
  requestId?: string;
  idempotencyKey?: string;
  retryNetworkFailures?: boolean;
};

type ResponseKind = 'json' | 'text' | 'blob';
type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

type InternalRequestOptions = ApiRequestOptions & {
  method: RequestMethod;
  body?: BodyInit;
};

function createRequestId() {
  return (
    globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

function buildHeaders(options: InternalRequestOptions, requestId: string) {
  const headers = new Headers(options.headers);
  // Keep ordinary GETs "simple" (no custom header) to avoid a CORS preflight;
  // stamp a request id on mutations and any GET that explicitly opts in.
  if (options.method !== 'GET' || options.requestId) {
    headers.set('X-Request-ID', requestId);
  }
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey);
  // Scope flat routes to the active workspace when one is selected; the backend
  // falls back to the caller's default workspace when this header is absent.
  if (activeWorkspaceId && !headers.has('X-Workspace-Id')) {
    headers.set('X-Workspace-Id', activeWorkspaceId);
  }
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

async function fetchResponse(path: string, options: InternalRequestOptions, requestId: string) {
  return fetch(`${API_BASE_URL}${path}`, {
    method: options.method,
    body: options.body,
    signal: options.signal,
    cache: 'no-store',
    credentials: 'include',
    headers: buildHeaders(options, requestId),
  });
}

function canRetryNetworkFailure(options: InternalRequestOptions) {
  return (
    Boolean(options.retryNetworkFailures) &&
    (options.method === 'GET' || Boolean(options.idempotencyKey))
  );
}

async function requestResponse(path: string, options: InternalRequestOptions) {
  const requestId = options.requestId ?? createRequestId();
  const maxAttempts = canRetryNetworkFailure(options) ? 2 : 1;
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetchResponse(path, options, requestId);
      if (response.ok) return { response, requestId };
      const body = await readErrorBody(response);
      throw new ApiError(
        body || response.statusText || 'Request failed',
        response.status,
        body,
        response.headers.get('x-request-id') ?? requestId,
      );
    } catch (error) {
      lastError = error;
      if (
        error instanceof ApiError ||
        isAbortError(error) ||
        attempt >= maxAttempts ||
        !canRetryNetworkFailure(options)
      ) {
        throw error;
      }
      await delay(150 * attempt, options.signal);
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Failed to reach API.');
}

async function parseResponse<T>(response: Response, kind: ResponseKind): Promise<T> {
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as T;
  }
  if (kind === 'text') return response.text() as Promise<T>;
  if (kind === 'blob') return response.blob() as Promise<T>;
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) {
    const text = await response.text();
    if (!text.trim()) return undefined as T;
    throw new ApiError('Expected JSON response from API.', response.status, text);
  }
  return response.json() as Promise<T>;
}

async function request<T>(
  method: RequestMethod,
  path: string,
  kind: ResponseKind,
  body: unknown,
  options: ApiRequestOptions = {},
) {
  const encodedBody =
    body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body);
  const { response } = await requestResponse(path, { ...options, method, body: encodedBody });
  return parseResponse<T>(response, kind);
}

export const apiClient = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    request<T>('GET', path, 'json', undefined, options),
  getText: (path: string, options?: ApiRequestOptions) =>
    request<string>('GET', path, 'text', undefined, options),
  getBlob: (path: string, options?: ApiRequestOptions) =>
    request<Blob>('GET', path, 'blob', undefined, options),
  post: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>('POST', path, 'json', body, options),
  postForm: <T>(path: string, body: FormData, options?: ApiRequestOptions) =>
    request<T>('POST', path, 'json', body, options),
  put: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>('PUT', path, 'json', body, options),
  patch: <T>(path: string, body: unknown, options?: ApiRequestOptions) =>
    request<T>('PATCH', path, 'json', body, options),
  delete: <T>(path: string, options?: ApiRequestOptions) =>
    request<T>('DELETE', path, 'json', undefined, options),
};

async function readErrorBody(response: Response) {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    try {
      const payload = await response.json();
      if (payload && typeof payload === 'object') {
        const detail = (payload as Record<string, unknown>).detail;
        if (typeof detail === 'string') return detail;
      }
      return JSON.stringify(payload);
    } catch {
      return response.statusText;
    }
  }
  try {
    return (await response.text()).trim();
  } catch {
    return response.statusText;
  }
}

function delay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
}

export { ApiError, httpErrorStatus } from './errors';
