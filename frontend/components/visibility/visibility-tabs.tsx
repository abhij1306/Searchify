'use client';

import { useRef, type KeyboardEvent, type ReactNode } from 'react';

import { cn } from '@/lib/utils';
import { VISIBILITY_TABS, type VisibilityTab } from '@/lib/visibility/dashboard';

const TAB_ID = (tab: VisibilityTab) => `visibility-tab-${tab}`;
const PANEL_ID = (tab: VisibilityTab) => `visibility-panel-${tab}`;

/**
 * Accessible four-tab navigation for the Visibility workspace (WAI-ARIA tabs).
 *
 * Exposes EXACTLY Overview, Trends, Mentions & Citations, and Query Fanout (in
 * that order). The tablist implements roving tabindex, `aria-selected`, and
 * keyboard Arrow/Home/End navigation with focus transfer + automatic
 * activation; only the active panel is rendered as the primary section, wired
 * to its tab via `aria-controls` / `aria-labelledby`.
 *
 * URL synchronization (`?tab=`) and per-tab query orchestration live in the
 * parent `visibility-dashboard.tsx`; this component is a controlled view.
 *
 * On narrow viewports the tablist becomes a horizontally scrollable single row
 * (`overflow-x-auto` + `flex-nowrap`) with visible focus/selection states.
 */
export function VisibilityTabs({
  activeTab,
  onSelectTab,
  panel,
}: Readonly<{
  activeTab: VisibilityTab;
  onSelectTab: (tab: VisibilityTab) => void;
  /** The rendered content of the active panel (the parent owns composition). */
  panel: ReactNode;
}>) {
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const activeIndex = VISIBILITY_TABS.findIndex((tab) => tab.id === activeTab);

  function focusTab(index: number) {
    const tab = VISIBILITY_TABS[index];
    if (!tab) return;
    onSelectTab(tab.id);
    // Move DOM focus to the newly selected tab (roving tabindex + focus xfer).
    tabRefs.current[tab.id]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const last = VISIBILITY_TABS.length - 1;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault();
        focusTab(activeIndex >= last ? 0 : activeIndex + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault();
        focusTab(activeIndex <= 0 ? last : activeIndex - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusTab(0);
        break;
      case 'End':
        event.preventDefault();
        focusTab(last);
        break;
      default:
        break;
    }
  }

  return (
    <div className="grid gap-5">
      <div
        role="tablist"
        aria-label="Visibility views"
        aria-orientation="horizontal"
        className="border-border flex [scrollbar-width:none] flex-nowrap gap-0 overflow-x-auto border-b-2 [&::-webkit-scrollbar]:hidden"
      >
        {VISIBILITY_TABS.map((tab) => {
          const selected = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[tab.id] = node;
              }}
              type="button"
              role="tab"
              id={TAB_ID(tab.id)}
              aria-selected={selected}
              aria-controls={PANEL_ID(tab.id)}
              tabIndex={selected ? 0 : -1}
              onClick={() => onSelectTab(tab.id)}
              onKeyDown={onKeyDown}
              className={cn(
                'focus-ring -mb-0.5 shrink-0 border-b-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors',
                selected
                  ? 'border-accent text-foreground font-semibold'
                  : 'text-secondary hover:text-foreground border-transparent',
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={PANEL_ID(activeTab)}
        aria-labelledby={TAB_ID(activeTab)}
        tabIndex={0}
        className="focus-ring outline-none"
      >
        {panel}
      </div>
    </div>
  );
}
