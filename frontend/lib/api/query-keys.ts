/**
 * React Query key namespaces (F2).
 *
 * All ids are string UUIDs (workspace-scoped contract). One namespace per API
 * domain owner, each defined in its own module under `query-keys/`:
 *   - core.ts        — auth, workspaces, projects, prompts, providers
 *   - runs.ts        — runs (audits + executions), visibility
 *   - site-health.ts — site health (crawls, inventory, monitored, issues)
 *
 * This facade re-assembles them under the historical `queryKeys` shape so the
 * 20+ existing importers keep the single `@/lib/api/query-keys` entry point.
 */
import {
  authKeys,
  projectKeys,
  promptKeys,
  providerKeys,
  workspaceKeys,
} from './query-keys/core';
import { runKeys, visibilityKeys } from './query-keys/runs';
import { siteHealthKeys } from './query-keys/site-health';

export const queryKeys = {
  auth: authKeys,
  workspaces: workspaceKeys,
  projects: projectKeys,
  prompts: promptKeys,
  providers: providerKeys,
  runs: runKeys,
  visibility: visibilityKeys,
  siteHealth: siteHealthKeys,
} as const;
