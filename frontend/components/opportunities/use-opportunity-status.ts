'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { opportunitiesMutations } from '@/lib/api/opportunities';
import { queryKeys } from '@/lib/api/query-keys';

/**
 * The one mutation the Opportunities surface allows (the human workflow
 * status) plus the invalidations every status change needs: the changed
 * row's detail, every catalog page/filter for the project, and the summary
 * counts all go stale together. Shared by the catalog row dropdown and the
 * evidence-drawer footer so the two controls can never drift.
 */
export function useUpdateOpportunityStatus(projectId: string, opportunityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...opportunitiesMutations.updateStatus(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.list(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.summary(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.detail(opportunityId),
        }),
      ]);
    },
  });
}
