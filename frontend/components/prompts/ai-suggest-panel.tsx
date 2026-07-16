'use client';

import { Sparkles } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

/**
 * AI-suggest panel (F7, decision B-4).
 *
 * AI-suggested prompt generation is on the roadmap: the B3 `/generate` endpoint
 * is a 501 `not_implemented` stub. This panel renders that not-yet-enabled
 * state honestly — a disabled "Generate" action with a "coming soon" badge — and
 * never fakes a generation call. It exists so the surface is discoverable and
 * the wiring is ready for when the roadmap lands.
 */
export function AiSuggestPanel() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-accent-text">
            <Sparkles className="size-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">Generate prompts & topics</h3>
              <Badge variant="neutral">Coming soon</Badge>
            </div>
            <p className="mt-1 max-w-xl text-sm text-secondary">
              Let Searchify draft prompts and topics from your brand profile. AI-suggested
              generation is on the roadmap and not available yet.
            </p>
          </div>
        </div>
        <Button variant="secondary" disabled aria-disabled title="AI generation is coming soon">
          Generate
        </Button>
      </CardContent>
    </Card>
  );
}
