/** Auth + workspaces + projects + prompts + providers query-key namespaces. */

export const authKeys = {
  all: ['auth'] as const,
  me: () => ['auth', 'me'] as const,
};

export const workspaceKeys = {
  all: ['workspaces'] as const,
  list: () => ['workspaces', 'list'] as const,
};

export const projectKeys = {
  all: ['projects'] as const,
  list: () => ['projects', 'list'] as const,
  detail: (projectId: string) => ['projects', 'detail', projectId] as const,
};

export const promptKeys = {
  all: ['prompts'] as const,
  sets: (projectId: string) => ['prompts', 'sets', projectId] as const,
  set: (promptSetId: string) => ['prompts', 'set', promptSetId] as const,
  list: (promptSetId: string) => ['prompts', 'list', promptSetId] as const,
};

export const providerKeys = {
  all: ['providers'] as const,
  connections: () => ['providers', 'connections'] as const,
  connection: (connectionId: string) => ['providers', 'connection', connectionId] as const,
  catalog: () => ['providers', 'catalog'] as const,
};
