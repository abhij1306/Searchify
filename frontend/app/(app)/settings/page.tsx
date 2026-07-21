'use client';

import { Suspense } from 'react';

import { TooltipProvider } from '@/components/ui/tooltip';
import { SettingsScreen } from '@/components/settings/settings-screen';

/**
 * Settings page — tabbed settings reachable from the sidebar user dropdown:
 * Account (read-only session details + appearance), Provider Settings (BYOK
 * provider configuration, formerly `/providers`), and Danger Zone (project
 * deletion). The page title renders in the top bar (F5).
 *
 * `<SettingsScreen>` reads `useSearchParams` (deep-linkable `?tab=`), so it
 * sits under `<Suspense>` per Next's CSR-bailout requirement.
 */
export default function SettingsPage() {
  return (
    <TooltipProvider>
      <Suspense>
        <SettingsScreen />
      </Suspense>
    </TooltipProvider>
  );
}
