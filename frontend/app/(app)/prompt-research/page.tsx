import { redirect } from 'next/navigation';

/**
 * `/prompt-research` — retired as a standalone page. The prompt management
 * workspace now lives on `/prompts` as an in-page "Manage prompts" mode; this
 * redirect keeps old links and bookmarks working (and lands in manage mode
 * directly when the deep link asks for it).
 */
export default async function PromptResearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { mode } = await searchParams;
  redirect(mode === 'manage' ? '/prompts?mode=manage' : '/prompts');
}
