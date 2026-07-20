'use client';

import { TooltipProvider } from '@/components/ui/tooltip';
import { SettingsScreen } from '@/components/settings/settings-screen';

/**
 * Settings page — a basic, read-only account view reachable from the sidebar
 * user dropdown. Shows the current session user's account details, an
 * appearance/theme control, and links to the existing Providers and Setup
 * screens. No account-mutation endpoints exist, so nothing here is editable.
 */
export default function SettingsPage() {
  return (
    <TooltipProvider>
      <SettingsScreen />
    </TooltipProvider>
  );
}
