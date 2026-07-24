/**
 * Compat facade (F2) — a single `api` object spreading the per-domain modules.
 *
 * This module owns **no transport**: it must never call `fetch`, import
 * `client`, or construct requests. It only re-exports the domain modules'
 * methods (which each own transport + `strictValidate`) plus the query key and
 * error surfaces. The architecture guard enforces the no-transport rule.
 */
import { authApi } from './auth';
import { contentApi } from './content';
import { opportunitiesApi } from './opportunities';
import { projectsApi } from './projects';
import { promptsApi } from './prompts';
import { providersApi } from './providers';
import { runsApi } from './runs';
import { siteHealthApi } from './site-health';
import { topicsApi } from './topics';
import { visibilityApi } from './visibility';

export const api = {
  ...authApi,
  ...contentApi,
  ...opportunitiesApi,
  ...projectsApi,
  ...promptsApi,
  ...providersApi,
  ...runsApi,
  ...siteHealthApi,
  ...topicsApi,
  ...visibilityApi,
};

export { authApi } from './auth';
export {
  contentApi,
  CONTENT_PROMPT_MAX_LEN,
  CONTENT_OUTPUT_TYPE_WEBSITE_PAGE,
  CONTENT_LIST_DEFAULT_LIMIT,
  CONTENT_LIST_POLL_MS,
  CONTENT_DETAIL_POLL_MS,
} from './content';
export { opportunitiesApi, opportunitiesMutations, opportunitiesQueries } from './opportunities';
export { projectsApi } from './projects';
export { promptsApi } from './prompts';
export { topicsApi } from './topics';
export { providersApi } from './providers';
export { runsApi } from './runs';
export { siteHealthApi, siteHealthQueries, siteHealthMutations } from './site-health';
export { visibilityApi } from './visibility';

export { queryKeys } from './query-keys';
export { createAppQueryClient, shouldRetryQuery } from './query-client';
export { ApiError, httpErrorStatus, isAbortError } from './errors';
export * from './types';
