/**
 * React Query key namespaces (F2).
 *
 * All ids are string UUIDs (workspace-scoped contract). One namespace per API
 * domain owner: auth, workspaces, projects, prompts, providers, runs (audits +
 * executions), visibility.
 */
type ListFilters = Readonly<Record<string, string | number | boolean | null | undefined>>;

export const queryKeys = {
  auth: {
    all: ['auth'] as const,
    me: () => ['auth', 'me'] as const,
  },
  workspaces: {
    all: ['workspaces'] as const,
    list: () => ['workspaces', 'list'] as const,
  },
  projects: {
    all: ['projects'] as const,
    list: () => ['projects', 'list'] as const,
    detail: (projectId: string) => ['projects', 'detail', projectId] as const,
  },
  prompts: {
    all: ['prompts'] as const,
    sets: (projectId: string) => ['prompts', 'sets', projectId] as const,
    set: (promptSetId: string) => ['prompts', 'set', promptSetId] as const,
    list: (promptSetId: string) => ['prompts', 'list', promptSetId] as const,
  },
  providers: {
    all: ['providers'] as const,
    connections: () => ['providers', 'connections'] as const,
    connection: (connectionId: string) => ['providers', 'connection', connectionId] as const,
    catalog: () => ['providers', 'catalog'] as const,
  },
  runs: {
    all: ['runs'] as const,
    list: (filters: ListFilters = {}) => ['runs', 'list', filters] as const,
    detail: (auditId: string) => ['runs', 'detail', auditId] as const,
    executions: (auditId: string) => ['runs', 'executions', auditId] as const,
    execution: (executionId: string) => ['runs', 'execution', executionId] as const,
  },
  visibility: {
    all: ['visibility'] as const,
    project: (projectId: string, auditId?: string, filters: ListFilters = {}) =>
      ['visibility', 'project', projectId, auditId ?? 'latest', filters] as const,
  },
} as const;
