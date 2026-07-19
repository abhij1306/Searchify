# AI Content and Settings handoff

This directory contains the approved implementation plan, finalized HTML design artifacts, and original reference images for the AI Content workspace and basic Settings hub.

## Product decisions

- Content is an AI website-content generation workspace, not a scratchpad or manual draft CRUD screen.
- The AI provider is environment-driven; the default is Mistral (`mistral-small-latest`) and can be replaced behind a provider-neutral boundary.
- The only v1 tool is Website context from Searchify's persisted crawl evidence.
- Website context prioritizes the homepage, then active monitored pages, then stable URL order.
- Generated content renders as sanitized Markdown with raw HTML disabled.
- GitHub, Notion, CMS publishing, attachments, and additional content types are deferred.
- Settings is a basic read-only account/appearance/configuration hub and appears directly above Sign out.

## Files

- `summary.md` — approved product and architecture summary.
- `implementation-plan.md` — detailed implementation tasks, contracts, test requirements, dependencies, and risks.
- `designs/design-plan.json` — design manifest.
- `designs/*.html` — self-contained light/dark mockups for ready, generating, result, error, Settings, and the user dropdown.
- `references/*.png` — source reference images supplied for hierarchy and product intent.

## Branch status at handoff

The Settings frontend slice is implemented in this branch and has focused test/lint/typecheck/policy evidence from the build agent. The AI Content backend and frontend implementation has **not** started; the plan and designs are ready for the next implementation agent. Full simplify/review and final real-stack Content verification remain outstanding.
