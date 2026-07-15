import type { NextConfig } from 'next';

/**
 * Minimal Next.js config for the F1 scaffold.
 *
 * F2 adds the same-origin API proxy here:
 *   async rewrites() {
 *     return [{ source: '/api/:path*', destination: `${process.env.BACKEND_ORIGIN}/api/:path*` }];
 *   }
 * The browser must only ever call `/api/...` relative (invariant 12); the
 * server-only `BACKEND_ORIGIN` never reaches the client bundle.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
