import type { NextConfig } from 'next';

/**
 * Next.js config — same-origin API proxy (F2).
 *
 * The browser must only ever call `/api/...` **relative** (invariant 12). The
 * `rewrites()` below proxy `/api/:path*` to the server-only `BACKEND_ORIGIN`
 * environment variable, so the backend URL never reaches the client bundle and
 * there is no cross-origin request (gotcha 2: a cross-origin backend behind a
 * tunnel double-sets `Access-Control-Allow-Origin`; the same-origin proxy
 * avoids that entirely).
 *
 * Environment:
 *   BACKEND_ORIGIN — REQUIRED, server-only. The absolute origin of the FastAPI
 *     backend, e.g. `http://localhost:8000` in local dev or the internal
 *     service URL in production. It is read only in `next.config.ts` (build /
 *     server), is NOT prefixed with `NEXT_PUBLIC_`, and is therefore never
 *     exposed to the browser. Defaults to `http://localhost:8000` for local dev.
 */
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN ?? 'http://localhost:8000';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
