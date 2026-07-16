'use client';

import { useState } from 'react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { inputClasses } from '@/components/ui/input';
import { discoveryModelOptions, type DiscoveryModelOption } from '@/lib/providers/catalog';
import type { ProviderCatalog } from '@/lib/api/types';

const STORAGE_KEY = 'searchify.discovery-model';

function readStored(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

function writeStored(value: string) {
  if (typeof window === 'undefined') return;
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore storage failures
  }
}

function optionKey(option: DiscoveryModelOption): string {
  return `${option.logical_engine}:${option.transport_provider}:${option.transport_model}`;
}

/**
 * Discovery / analysis model selection (F8, plumbing-only, design.md §9.5).
 *
 * A separate control that maps to the roadmap `DiscoveryModelConfig`. The choice
 * is persisted locally so the plumbing is exercised, but it is NOT invoked at
 * MVP — the audit pipeline does not read it yet. Options are driven off the
 * catalog's approved routes.
 */
export function DiscoveryModelCard({
  catalog,
}: Readonly<{ catalog: ProviderCatalog | undefined }>) {
  const options = discoveryModelOptions(catalog);
  const [selected, setSelected] = useState<string>(() => readStored());

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Discovery / analysis model</CardTitle>
        <p className="text-xs text-secondary">
          Used for prompt discovery and answer analysis. Stored for later — not invoked at MVP.
        </p>
      </CardHeader>
      <CardContent className="grid gap-1.5">
        <label htmlFor="discovery-model" className="text-xs font-medium text-secondary">
          Model
        </label>
        <select
          id="discovery-model"
          className={inputClasses}
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value);
            writeStored(e.target.value);
          }}
        >
          <option value="">Use default</option>
          {options.map((option) => {
            const key = optionKey(option);
            return (
              <option key={key} value={key}>
                {option.label}
              </option>
            );
          })}
        </select>
      </CardContent>
    </Card>
  );
}
