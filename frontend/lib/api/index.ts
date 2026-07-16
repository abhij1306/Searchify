/**
 * Compat facade (F2) — a single `api` object spreading the per-domain modules.
 *
 * This module owns **no transport**: it must never call `fetch`, import
 * `client`, or construct requests. It only re-exports the domain modules'
 * methods (which each own transport + `strictValidate`) plus the query key and
 * error surfaces. The architecture guard enforces the no-transport rule.
 */
import { authApi } from './auth';
import { projectsApi } from './projects';
import { promptsApi } from './prompts';
import { providersApi } from './providers';
import { runsApi } from './runs';
import { visibilityApi } from './visibility';

export const api = {
  ...authApi,
  ...projectsApi,
  ...promptsApi,
  ...providersApi,
  ...runsApi,
  ...visibilityApi,
};

export { authApi } from './auth';
export { projectsApi } from './projects';
export { promptsApi } from './prompts';
export { providersApi } from './providers';
export { runsApi } from './runs';
export { visibilityApi } from './visibility';

export { queryKeys } from './query-keys';
export { createAppQueryClient, shouldRetryQuery } from './query-client';
export { ApiError, httpErrorStatus, isAbortError } from './errors';
export * from './types';
