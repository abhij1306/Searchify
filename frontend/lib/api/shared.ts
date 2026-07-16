/**
 * Small query-string helpers shared by the per-domain API modules (F2).
 * These own no transport — they only build relative paths.
 */
type QueryParamValue = string | number | boolean | null | undefined;

export function definedQuery<T extends Record<string, QueryParamValue>>(params?: T) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null) {
      query.set(key, String(value));
    }
  }
  return query;
}

export function withQuery(path: string, query: URLSearchParams) {
  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}
