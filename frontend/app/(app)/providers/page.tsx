import { redirect } from 'next/navigation';

/**
 * `/providers` — retired as a standalone page. BYOK provider configuration now
 * lives in Settings under the "Provider Settings" tab; this redirect keeps old
 * links and bookmarks working (and lands on that tab directly).
 */
export default function ProvidersPage() {
  redirect('/settings?tab=providers');
}
