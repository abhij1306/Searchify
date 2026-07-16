/**
 * Provider Settings view-model helpers (F8, v2 direct-provider retirement).
 *
 * Turns the raw `/provider-catalog` payload + the workspace's
 * `/provider-connections` into the per-engine card model the UI renders. The
 * catalog is the single source of truth for which (logical engine → transport →
 * model) routes are available. After the direct-provider retirement each
 * logical engine has exactly ONE direct transport — ChatGPT/OpenAI,
 * Gemini/Google, Claude/Anthropic — so each card renders a single fixed direct
 * route with no toggle and no reserved "coming soon" option.
 */
import type {
  HistoricalTransportProvider,
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

/**
 * Human display names for each transport provider. Keyed by the historical
 * transport space so read-only provenance (e.g. a legacy `openrouter` audit
 * row) still labels, even though only the three direct transports are writable.
 */
export const TRANSPORT_LABELS: Record<HistoricalTransportProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openrouter: 'OpenRouter',
};

/** Human label for an engine key (falls back to the raw key). */
export function engineLabel(key: string): string {
  return ENGINE_LABELS[key as LogicalEngine] ?? key;
}

/** Human label for a transport key (falls back to the raw key). */
export function transportLabel(key: string): string {
  return TRANSPORT_LABELS[key as HistoricalTransportProvider] ?? key;
}

/** The single fixed direct route on an engine card. */
export type EngineRouteOption = {
  transport_provider: TransportProvider;
  /** The catalog default model for this (engine, transport). */
  default_model: string;
  /** Toggle-free label, e.g. "Direct (OpenAI)". */
  label: string;
};

/** The full view-model for one engine card. */
export type EngineCardModel = {
  logical_engine: LogicalEngine;
  label: string;
  /** The single direct route for this engine (null if the catalog omits it). */
  route: EngineRouteOption | null;
};

/** Display label for a direct transport route. */
function directLabel(transport: TransportProvider): string {
  return `Direct (${TRANSPORT_LABELS[transport]})`;
}

/**
 * Build the ordered engine card models from the catalog. Each engine exposes a
 * single direct route (the first approved route the catalog lists for it).
 */
export function buildEngineCards(catalog: ProviderCatalog | undefined): EngineCardModel[] {
  const byEngine = new Map(catalog?.engines.map((e) => [e.logical_engine, e]) ?? []);
  return ENGINE_ORDER.map((engine) => {
    const approved = byEngine.get(engine)?.routes ?? [];
    const first = approved[0];
    const route: EngineRouteOption | null = first
      ? {
          transport_provider: first.transport_provider,
          default_model: first.default_model,
          label: directLabel(first.transport_provider),
        }
      : null;
    return {
      logical_engine: engine,
      label: ENGINE_LABELS[engine],
      route,
    };
  });
}

/**
 * Find the connection that serves a given transport in this workspace. BYOK
 * connections are keyed by `transport_provider`.
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
 * create/update payload. Preserves other engines' routes on the same direct
 * connection and stamps the catalog default model for the added engine.
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
