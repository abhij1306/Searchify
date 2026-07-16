import { setupServer } from 'msw/node';

/**
 * Shared MSW server for frontend tests (F4). Handlers are registered per-test
 * via `server.use(...)`; the lifecycle hooks (listen/reset/close) are wired in
 * each test file so a suite that doesn't import this never starts a server.
 *
 * The API client calls the relative base `/api/v1`; jsdom resolves relative
 * URLs against the configured origin, so handlers match `/api/v1/...` paths.
 */
export const mswServer = setupServer();
