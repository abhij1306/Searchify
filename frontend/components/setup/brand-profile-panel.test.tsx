import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import type { BrandProfile } from '@/lib/api/types';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { BrandProfilePanel } from './brand-profile-panel';

const projectId = '55555555-5555-4555-8555-555555555555';
const suggestionId = '77777777-7777-4777-8777-777777777777';

const profile: BrandProfile = {
  id: '11111111-1111-4111-8111-111111111111',
  workspace_id: '66666666-6666-4666-8666-666666666666',
  project_id: projectId,
  brand_id: '22222222-2222-4222-8222-222222222222',
  description: '',
  positioning: '',
  products_services: [],
  target_audience: '',
  sources: {
    description: null,
    positioning: null,
    products_services: null,
    target_audience: null,
  },
  source_artifact_ids: {
    description: null,
    positioning: null,
    products_services: null,
    target_audience: null,
  },
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('BrandProfilePanel', () => {
  it('saves direct edits as manual knowledge', async () => {
    const user = userEvent.setup();
    let requestBody: unknown;
    mswServer.use(
      http.put(`/api/v1/projects/${projectId}/brand-profile`, async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({
          ...profile,
          description: 'A value-focused family retailer.',
          sources: { ...profile.sources, description: 'manual' },
        });
      }),
    );

    renderWithProviders(<BrandProfilePanel projectId={projectId} profile={profile} />);
    await user.type(screen.getByLabelText('Description'), 'A value-focused family retailer.');
    await user.type(screen.getByLabelText('Products and services'), 'Clothing,');
    expect(screen.getByLabelText('Products and services')).toHaveValue('Clothing,');
    await user.click(screen.getByRole('button', { name: /save brand knowledge/i }));

    expect(await screen.findByText(/brand knowledge saved/i)).toBeInTheDocument();
    expect(requestBody).toMatchObject({
      description: 'A value-focused family retailer.',
      products_services: ['Clothing'],
    });
  });

  it('loads an AI draft for review and separates edited fields on acceptance', async () => {
    const user = userEvent.setup();
    let acceptBody: Record<string, unknown> | null = null;
    const draft = {
      description: 'Australian family retailer.',
      positioning: 'Value-priced everyday family basics.',
      products_services: ['Clothing', 'Homewares'],
      target_audience: 'Budget-conscious families.',
    };
    mswServer.use(
      http.post(`/api/v1/projects/${projectId}/brand-profile/suggest`, () =>
        HttpResponse.json({
          id: suggestionId,
          workspace_id: profile.workspace_id,
          project_id: projectId,
          brand_id: profile.brand_id,
          draft,
          model_identity: { transport_host: 'agent.test', transport_model: 'mistral-small' },
          prompt_template_version: 'brand-profile-suggest-v1',
          created_at: '2026-07-21T00:00:00Z',
        }),
      ),
      http.post(
        `/api/v1/projects/${projectId}/brand-profile/suggestions/${suggestionId}/accept`,
        async ({ request }) => {
          acceptBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({
            profile: {
              ...profile,
              ...draft,
              description: 'User-edited description.',
              sources: {
                description: 'manual',
                positioning: 'ai_suggested',
                products_services: 'ai_suggested',
                target_audience: 'ai_suggested',
              },
              source_artifact_ids: {
                description: null,
                positioning: suggestionId,
                products_services: suggestionId,
                target_audience: suggestionId,
              },
            },
            accepted_fields: ['positioning', 'products_services', 'target_audience'],
            skipped_manual_fields: [],
          });
        },
      ),
    );

    renderWithProviders(<BrandProfilePanel projectId={projectId} profile={profile} />);
    await user.click(screen.getByRole('button', { name: /draft with ai/i }));
    await user.click(screen.getByLabelText(/confirm sending brand details/i));
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    const description = await screen.findByLabelText('Description');
    expect(description).toHaveValue(draft.description);
    await user.clear(description);
    await user.type(description, 'User-edited description.');
    await user.click(screen.getByRole('button', { name: /apply reviewed draft/i }));

    await waitFor(() => expect(acceptBody).not.toBeNull());
    expect(acceptBody).toMatchObject({
      accepted_fields: ['positioning', 'products_services', 'target_audience'],
      manual_overrides: { description: 'User-edited description.' },
    });
  });

  it('preserves existing values for empty suggestions and can discard the draft', async () => {
    const user = userEvent.setup();
    const existingProfile: BrandProfile = {
      ...profile,
      description: 'Existing description.',
      positioning: 'Existing positioning.',
      products_services: ['Existing product'],
      target_audience: 'Existing audience.',
    };
    mswServer.use(
      http.post(`/api/v1/projects/${projectId}/brand-profile/suggest`, () =>
        HttpResponse.json({
          id: suggestionId,
          workspace_id: profile.workspace_id,
          project_id: projectId,
          brand_id: profile.brand_id,
          draft: {
            description: '',
            positioning: 'Suggested positioning.',
            products_services: [],
            target_audience: '',
          },
          model_identity: { transport_host: 'agent.test', transport_model: 'mistral-small' },
          prompt_template_version: 'brand-profile-suggest-v1',
          created_at: '2026-07-21T00:00:00Z',
        }),
      ),
    );

    renderWithProviders(
      <BrandProfilePanel projectId={projectId} profile={existingProfile} />,
    );
    await user.click(screen.getByRole('button', { name: /draft with ai/i }));
    await user.click(screen.getByLabelText(/confirm sending brand details/i));
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    expect(await screen.findByLabelText('Description')).toHaveValue('Existing description.');
    expect(screen.getByLabelText('Positioning')).toHaveValue('Suggested positioning.');
    expect(screen.getByLabelText('Products and services')).toHaveValue('Existing product');

    await user.click(screen.getByRole('button', { name: /discard draft/i }));
    expect(screen.queryByRole('button', { name: /apply reviewed draft/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save brand knowledge/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Positioning')).toHaveValue('Existing positioning.');
  });
});
