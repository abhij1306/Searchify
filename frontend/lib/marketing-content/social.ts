import { Github, type LucideIcon } from 'lucide-react';

/**
 * Social/contact content for the marketing chrome (footer social row, contact
 * CTAs). Only the GitHub and license URLs are grounded in the repo itself —
 * everything else is a user-fillable placeholder.
 */

/** Canonical repository URL (see README.md). */
export const GITHUB_URL = 'https://github.com/abhij1306/Searchify';

/** MIT license text (see LICENSE), linked from the footer legal row. */
export const LICENSE_URL = 'https://github.com/abhij1306/Searchify/blob/main/LICENSE';

export type SocialLink = {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
};

export const SOCIAL_LINKS: readonly SocialLink[] = [
  {
    key: 'github',
    label: 'GitHub',
    href: GITHUB_URL,
    icon: Github,
  },
];

// TODO(user): public contact email — CTAs fall back to href="#" while empty.
export const CONTACT_EMAIL = '';
