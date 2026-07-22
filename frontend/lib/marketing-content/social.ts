import { type LucideIcon } from 'lucide-react';

/**
 * Social/contact content for the marketing chrome (footer social row, contact
 * CTAs). All entries are user-fillable placeholders — no repo-grounded URLs
 * remain.
 */

export type SocialLink = {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
};

export const SOCIAL_LINKS: readonly SocialLink[] = [];

// TODO(user): public contact email — CTAs fall back to href="#" while empty.
export const CONTACT_EMAIL = '';
