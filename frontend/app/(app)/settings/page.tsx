'use client';

import { TooltipProvider } from '@/components/ui/tooltip';
import { SettingsScreen } from '@/components/settings/settings-screen';

/**
 * Settings page — tabbed settings reachable from the sidebar user dropdown:
 * Account (read-only session details + appearance), Provider Settings (BYOK
 * provider configuration, formerly `/providers`), and Danger Zone (project
 * deletion). The page title renders in the top bar (F5).
 */
export default function SettingsPage() {
  return (
    <TooltipProvider>
      <SettingsScreen />
    </TooltipProvider>
  );
}
