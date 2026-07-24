'use client';

import { useRef, type KeyboardEvent, type ReactNode } from 'react';

import { PRODUCTS_TABS, type ProductsTab } from '@/lib/products/catalog';
import { cn } from '@/lib/utils';

const TAB_ID = (tab: ProductsTab) => `products-tab-${tab}`;
const PANEL_ID = (tab: ProductsTab) => `products-panel-${tab}`;

/**
 * Accessible two-tab navigation for the Products workspace (WAI-ARIA tabs,
 * mirrors `visibility-tabs.tsx`): Catalog | Visibility with roving tabindex,
 * `aria-selected`, and Arrow/Home/End keyboard navigation with focus transfer
 * + automatic activation. Only the active panel is rendered, wired to its tab
 * via `aria-controls` / `aria-labelledby`. URL sync (`?tab=`) lives in the
 * parent; this is a controlled view.
 */
export function ProductsTabs({
  activeTab,
  onSelectTab,
  panel,
}: Readonly<{
  activeTab: ProductsTab;
  onSelectTab: (tab: ProductsTab) => void;
  /** The rendered content of the active panel (the parent owns composition). */
  panel: ReactNode;
}>) {
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const activeIndex = PRODUCTS_TABS.findIndex((tab) => tab.id === activeTab);

  function focusTab(index: number) {
    const tab = PRODUCTS_TABS[index];
    if (!tab) return;
    onSelectTab(tab.id);
    tabRefs.current[tab.id]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const last = PRODUCTS_TABS.length - 1;
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
    <div className="grid gap-4">
      <div
        role="tablist"
        aria-label="Products views"
        aria-orientation="horizontal"
        className="border-border flex [scrollbar-width:none] flex-nowrap gap-0 overflow-x-auto border-b-2 [&::-webkit-scrollbar]:hidden"
      >
        {PRODUCTS_TABS.map((tab) => {
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
                'focus-ring -mb-0.5 shrink-0 border-b-2 px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors',
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
