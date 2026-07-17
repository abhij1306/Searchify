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
    // Cross-run trend series: every filter (engine, from, to, granularity)
    // participates in the key so switching a control re-derives the view.
    trends: (projectId: string, filters: ListFilters = {}) =>
      ['visibility', 'trends', projectId, filters] as const,
    // Shared execution-evidence dataset for the Mentions & Citations and Query
    // Fanout tabs. ONE identical key is used by both tabs so switching between
    // them reuses the cache instead of refetching. Every shared filter
    // (audit_id, prompt_id, engine, from, to, limit) participates in the key.
    evidence: (projectId: string, filters: ListFilters = {}) =>
      ['visibility', 'evidence', projectId, filters] as const,
  },
  // Site Health — isolated by project / crawl / filter so an inventory query
  // for one crawl (or one filter combination) never collides with another.
  siteHealth: {
    all: ['site-health'] as const,
    entitlements: () => ['site-health', 'entitlements'] as const,
    dashboard: (projectId: string, crawlId?: string) =>
      ['site-health', 'dashboard', projectId, crawlId ?? 'latest'] as const,
    crawls: (projectId: string, filters: ListFilters = {}) =>
      ['site-health', 'crawls', projectId, filters] as const,
    crawl: (crawlId: string) => ['site-health', 'crawl', crawlId] as const,
    inventory: (crawlId: string, filters: ListFilters = {}) =>
      ['site-health', 'inventory', crawlId, filters] as const,
    monitored: (projectId: string) => ['site-health', 'monitored', projectId] as const,
    pages: (crawlId: string, filters: ListFilters = {}) =>
      ['site-health', 'pages', crawlId, filters] as const,
    page: (crawlId: string, siteUrlId: string) =>
      ['site-health', 'page', crawlId, siteUrlId] as const,
    issueHistory: (crawlId: string, siteUrlId: string, filters: ListFilters = {}) =>
      ['site-health', 'issue-history', crawlId, siteUrlId, filters] as const,
    issues: (crawlId: string, filters: ListFilters = {}) =>
      ['site-health', 'issues', crawlId, filters] as const,
    issue: (crawlId: string, issueId: string) =>
      ['site-health', 'issue', crawlId, issueId] as const,
    events: (crawlId: string) => ['site-health', 'events', crawlId] as const,
  },
} as const;
