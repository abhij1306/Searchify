'use client';

import { useState, type ReactNode } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { authApi } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/errors';
import type { OAuthProvider } from '@/lib/api/types';
import { assignLocation } from '@/lib/navigate';

/**
 * OAuthSection — social sign-in buttons (Google / GitHub / Apple) stacked
 * above the "or continue with email" divider on the auth screens.
 *
 * Wired to the Phase B backend scaffold: `GET /auth/oauth/{provider}/start`
 * answers 503 (`oauth_provider_not_configured`) until real credentials exist,
 * which surfaces here as an inline info notice ("coming soon") rather than an
 * error. A configured provider answers `{ authorize_url, state }` and the
 * browser navigates straight to it. Nothing is fetched on mount.
 *
 * Glyphs are inline monochrome SVGs (`fill="currentColor"`) so the file stays
 * hex-free (token-escape guard) and inherits the button text color.
 */

type ProviderDef = {
  id: OAuthProvider;
  label: string;
  glyph: ReactNode;
};

const glyphProps = {
  viewBox: '0 0 24 24',
  fill: 'currentColor',
  'aria-hidden': true,
  className: 'size-4 shrink-0',
} as const;

const OAUTH_PROVIDERS: readonly ProviderDef[] = [
  {
    id: 'google',
    label: 'Google',
    glyph: (
      <svg {...glyphProps}>
        <path d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.47c-.29 1.48-1.14 2.73-2.4 3.58v3h3.86c2.26-2.09 3.56-5.17 3.56-8.82zM12 24c3.24 0 5.95-1.08 7.93-2.91l-3.86-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09C3.26 21.3 7.31 24 12 24zM5.27 14.29c-.25-.72-.38-1.49-.38-2.29s.14-1.57.38-2.29V6.62H1.29C.47 8.24 0 10.06 0 12s.47 3.76 1.29 5.38l3.98-3.09zM12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75z" />
      </svg>
    ),
  },
  {
    id: 'github',
    label: 'GitHub',
    glyph: (
      <svg {...glyphProps}>
        <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12z" />
      </svg>
    ),
  },
  {
    id: 'apple',
    label: 'Apple',
    glyph: (
      <svg {...glyphProps}>
        <path d="M16.365 1.43c0 1.14-.47 2.2-1.23 3.02-.79.86-2.08 1.52-3.13 1.44-.13-1.1.5-2.26 1.21-3.02.83-.9 2.25-1.55 3.15-1.44zM20.94 17.14c-.57 1.3-.84 1.88-1.57 3.03-1.02 1.61-2.46 3.61-4.25 3.63-1.59.02-2-1.04-4.16-1.03-2.16.01-2.61 1.05-4.2 1.03-1.79-.02-3.16-1.83-4.18-3.44C.72 17.42.36 13.07 1.9 10.6c1.1-1.77 2.84-2.81 4.47-2.81 1.66 0 2.7 1.05 4.08 1.05 1.34 0 2.16-1.05 4.09-1.05 1.46 0 3 .79 4.1 2.17-3.6 1.97-3.02 7.1 2.3 5.18z" />
      </svg>
    ),
  },
];

type Notice = { kind: 'coming-soon' | 'error'; providerLabel: string };

export function OAuthSection() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pending, setPending] = useState<OAuthProvider | null>(null);

  async function startOAuth(provider: ProviderDef) {
    setNotice(null);
    setPending(provider.id);
    try {
      const { authorize_url } = await authApi.oauthStart(provider.id);
      // Hard navigation to the provider (via the lib/navigate seam — jsdom
      // can't stub Location#assign; see lib/navigate.ts).
      assignLocation(authorize_url);
    } catch (error) {
      setNotice(
        error instanceof ApiError && error.status === 503
          ? { kind: 'coming-soon', providerLabel: provider.label }
          : { kind: 'error', providerLabel: provider.label },
      );
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="grid gap-4">
      <div className="grid gap-2.5">
        {OAUTH_PROVIDERS.map((provider) => (
          <Button
            key={provider.id}
            variant="secondary"
            size="lg"
            className="w-full gap-2.5"
            disabled={pending !== null}
            onClick={() => void startOAuth(provider)}
          >
            {provider.glyph}
            Continue with {provider.label}
          </Button>
        ))}
      </div>

      {notice?.kind === 'coming-soon' ? (
        <Alert tone="info">{notice.providerLabel} sign-in is coming soon — use email below.</Alert>
      ) : null}
      {notice?.kind === 'error' ? (
        <Alert tone="danger">
          We couldn&apos;t start {notice.providerLabel} sign-in — try again or use email below.
        </Alert>
      ) : null}

      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="bg-border h-px flex-1" />
        <span className="text-muted text-2xs font-mono tracking-[0.14em] uppercase">
          or continue with email
        </span>
        <span aria-hidden="true" className="bg-border h-px flex-1" />
      </div>
    </div>
  );
}
