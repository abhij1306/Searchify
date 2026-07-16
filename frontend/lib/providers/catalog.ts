/**
 * Provider Settings view-model helpers (F8).
 *
 * Turns the raw `/provider-catalog` payload + the workspace's
 * `/provider-connections` into the per-engine card model the UI renders. The
 * catalog is the single source of truth for which (logical engine → transport →
 * model) routes are available (decision B-3): ChatGPT is OpenRouter-only at
 * MVP, and a disabled "Direct OpenAI — coming soon" option is surfaced from a
 * static reserved-route table (never from the catalog, which omits it).
 */
import type {
  LogicalEngine,
  ProviderCatalog,
  ProviderConnection,
  TransportProvider,
} from '@/lib/api/types';

/** Logical engines rendered as cards, in display order. */
export const ENGINE_ORDER: readonly LogicalEngine[] = ['chatgpt', 'gemini', 'claude'] as const;

/** Human display names for each logical engine. */
export const ENGINE_LABELS: Record<LogicalEngine, string> = {
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  claude: 'Claude',
};

/** Human display names for each transport provider. */
export const TRANSPORT_LABELS: Record<TransportProvider, string> = {
  anthropic: 'Anthropic',
  google: 'Google',
  openrouter: 'OpenRouter',
  openai: 'OpenAI',
};

/** A selectable route option on an engine card. */
export type EngineRouteOption = {
  transport_provider: TransportProvider;
  /** The catalog default model for this (engine, transport). */
  default_model: string;
  /** Short toggle label, e.g. "Direct (Google)" or "OpenRouter". */
  label: string;
  /** Disabled reserved fast-follow (e.g. direct OpenAI at MVP, B-3). */
  disabled: boolean;
  /** Reason shown when disabled, e.g. "coming soon". */
  disabledReason?: string;
};

/** The full view-model for one engine card. */
export type EngineCardModel = {
  logical_engine: LogicalEngine;
  label: string;
  /** Approved (enabled) + reserved (disabled) route options for this engine. */
  options: EngineRouteOption[];
  /** True when only one enabled route exists (no toggle needed, e.g. ChatGPT). */
  singleRoute: boolean;
};

/**
 * Reserved, DISABLED route options per engine (decision B-3). Kept static — the
 * live catalog never lists them — so the UI can render a "coming soon" affordance
 * without inventing state. Currently only ChatGPT's direct-OpenAI fast-follow.
 */
const RESERVED_OPTIONS: Partial<Record<LogicalEngine, EngineRouteOption[]>> = {
  chatgpt: [
    {
      transport_provider: 'openai',
      default_model: '',
      label: 'Direct OpenAI',
      disabled: true,
      disabledReason: 'coming soon',
    },
  ],
};

/** Toggle label for an approved (enabled) transport on a given engine. */
function approvedLabel(transport: TransportProvider): string {
  return transport === 'openrouter' ? 'OpenRouter' : `Direct (${TRANSPORT_LABELS[transport]})`;
}

/**
 * Build the ordered engine card models from the catalog. Enabled options come
 * from the catalog's approved routes; disabled reserved options are appended so
 * ChatGPT shows its greyed-out "Direct OpenAI — coming soon" entry.
 */
export function buildEngineCards(catalog: ProviderCatalog | undefined): EngineCardModel[] {
  const byEngine = new Map(catalog?.engines.map((e) => [e.logical_engine, e]) ?? []);
  return ENGINE_ORDER.map((engine) => {
    const approved = byEngine.get(engine)?.routes ?? [];
    const enabled: EngineRouteOption[] = approved.map((route) => ({
      transport_provider: route.transport_provider,
      default_model: route.default_model,
      label: approvedLabel(route.transport_provider),
      disabled: false,
    }));
    const reserved = RESERVED_OPTIONS[engine] ?? [];
    const options = [...enabled, ...reserved];
    return {
      logical_engine: engine,
      label: ENGINE_LABELS[engine],
      options,
      singleRoute: enabled.length <= 1,
    };
  });
}

/**
 * Find the connection that serves a given transport in this workspace. BYOK
 * connections are keyed by `transport_provider`, so one OpenRouter connection
 * can back several engines via its routes.
 */
export function connectionForTransport(
  connections: ProviderConnection[],
  transport: TransportProvider,
): ProviderConnection | undefined {
  return connections.find((c) => c.transport_provider === transport);
}

/** True when a connection exists for the transport AND has a stored key. */
export function isConfigured(connection: ProviderConnection | undefined): boolean {
  return Boolean(connection?.api_key_set);
}

/**
 * Merge a logical-engine route into a connection's existing routes for a
 * create/update payload. Preserves other engines' routes (a shared OpenRouter
 * connection) and stamps the catalog default model for the added engine.
 */
export function mergeRoutePayload(
  existing: ProviderConnection | undefined,
  logical_engine: LogicalEngine,
  default_model: string,
): { logical_engine: LogicalEngine; transport_model: string; is_default: boolean }[] {
  const routes = (existing?.routes ?? []).map((r) => ({
    logical_engine: r.logical_engine,
    transport_model: r.transport_model,
    is_default: r.is_default,
  }));
  if (!routes.some((r) => r.logical_engine === logical_engine)) {
    routes.push({ logical_engine, transport_model: default_model, is_default: false });
  }
  return routes;
}

/** All approved (engine, transport, model) tuples, for the discovery selector. */
export type DiscoveryModelOption = {
  logical_engine: LogicalEngine;
  transport_provider: TransportProvider;
  transport_model: string;
  label: string;
};

/** Flatten the catalog into discovery-model options (plumbing-only, F8). */
export function discoveryModelOptions(
  catalog: ProviderCatalog | undefined,
): DiscoveryModelOption[] {
  return (catalog?.engines ?? []).flatMap((engine) =>
    engine.routes.map((route) => ({
      logical_engine: engine.logical_engine,
      transport_provider: route.transport_provider,
      transport_model: route.default_model,
      label: `${ENGINE_LABELS[engine.logical_engine]} · ${TRANSPORT_LABELS[route.transport_provider]} · ${route.default_model}`,
    })),
  );
}
