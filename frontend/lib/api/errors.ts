/**
 * API error types + helpers (F2).
 *
 * `ApiError` is thrown by the transport (`client.ts`) for any non-2xx response
 * or a JSON-contract violation. It carries the HTTP status, the raw response
 * body, and the correlating `X-Request-ID` so failures are traceable end to
 * end. `httpErrorStatus` / `isAbortError` are shared by the retry policy in
 * `query-client.ts` and the client's bounded network retry.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: string;
  readonly requestId?: string;

  constructor(message: string, status: number, body: string, requestId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.requestId = requestId;
  }
}

/** Extract an HTTP status from an ApiError or a duck-typed `{ status }` error. */
export function httpErrorStatus(error: unknown): number | undefined {
  if (error instanceof ApiError) return error.status;
  if (typeof error === 'object' && error !== null && 'status' in error) {
    const status = (error as { status: unknown }).status;
    return typeof status === 'number' && Number.isFinite(status) ? status : undefined;
  }
  return undefined;
}

/** True when the error originates from an aborted `AbortSignal`. */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError';
}
