'use client';

import { useQuery } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import type { Project } from '@/lib/api/types';

const STORAGE_KEY = 'searchify.active-project-id';

type ProjectContextValue = {
  /** All projects the active workspace owns (empty while loading / none yet). */
  projects: Project[];
  /** The currently-selected project, or `null` when none is resolved. */
  activeProject: Project | null;
  /** The active project id, or `null`. Persisted to localStorage. */
  activeProjectId: string | null;
  /** Select a project by id (persists + stamps the workspace header). */
  setActiveProjectId: (projectId: string) => void;
  /** True while the project list is loading. */
  isLoading: boolean;
};

const ProjectContext = createContext<ProjectContextValue | null>(null);

/** Read the persisted active-project id (SSR-safe: null on the server). */
function readStoredProjectId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredProjectId(projectId: string | null) {
  if (typeof window === 'undefined') return;
  try {
    if (projectId) window.localStorage.setItem(STORAGE_KEY, projectId);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage failures (private mode / quota) — selection stays in memory.
  }
}

/**
 * ProjectProvider (F5) — the active-project context consumed by every authed
 * screen (F6–F10).
 *
 * It loads the workspace's projects via F2's `projects.ts`, tracks the selected
 * project id (persisted to localStorage so a reload keeps the selection), and
 * — critically — mirrors the active project's `workspace_id` into the API
 * client as the `X-Workspace-Id` header (see `lib/api/client.ts`). That header
 * is how the backend's `require_active_workspace` scopes flat routes to the
 * workspace the user is looking at; without it the backend falls back to the
 * user's default workspace.
 *
 * Selection resolution: a persisted id that still exists wins; otherwise the
 * first project is auto-selected. When there are no projects the context is
 * empty (the shell shows the Getting-Started card / setup flow).
 */
export function ProjectProvider({ children }: Readonly<{ children: ReactNode }>) {
  const { data: projects = [], isLoading } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: ({ signal }) => projectsApi.listProjects({ signal }),
  });

  const [selectedId, setSelectedId] = useState<string | null>(() => readStoredProjectId());

  // Resolve the effective active id: keep a valid selection, else default to
  // the first project, else null.
  const activeProjectId = useMemo(() => {
    if (projects.length === 0) return null;
    if (selectedId && projects.some((project) => project.id === selectedId)) {
      return selectedId;
    }
    return projects[0].id;
  }, [projects, selectedId]);

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );

  const setActiveProjectId = useCallback((projectId: string) => {
    setSelectedId(projectId);
    writeStoredProjectId(projectId);
  }, []);

  // Persist a resolved default (first project) so a reload is stable, and keep
  // the API client's workspace header in sync with the active project.
  useEffect(() => {
    if (activeProjectId && activeProjectId !== selectedId) {
      writeStoredProjectId(activeProjectId);
      setSelectedId(activeProjectId);
    }
  }, [activeProjectId, selectedId]);

  useEffect(() => {
    setActiveWorkspaceId(activeProject?.workspace_id ?? null);
  }, [activeProject]);

  const value = useMemo<ProjectContextValue>(
    () => ({
      projects,
      activeProject,
      activeProjectId,
      setActiveProjectId,
      isLoading,
    }),
    [projects, activeProject, activeProjectId, setActiveProjectId, isLoading],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

/** Access the active-project context. Throws if used outside `<ProjectProvider>`. */
export function useProjectContext(): ProjectContextValue {
  const context = useContext(ProjectContext);
  if (!context) {
    throw new Error('useProjectContext must be used within a <ProjectProvider>.');
  }
  return context;
}

/** Convenience accessor for just the active project (or null). */
export function useActiveProject(): Project | null {
  return useProjectContext().activeProject;
}
