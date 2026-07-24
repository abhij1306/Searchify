import {
  AlertCircle,
  BookOpen,
  Bot,
  Check,
  FileText,
  Gauge,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  OctagonAlert,
  Package,
  Settings,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  TriangleAlert,
  Wrench,
} from 'lucide-react';

/**
 * Canonical icon map — the single source of truth for which lucide glyph
 * represents each product concept. Nav and shared UI import icons by concept
 * (`ICONS.warning`) instead of picking glyph names ad hoc.
 *
 * Conventions:
 * - spinner = `LoaderCircle` (only)
 * - warning = `TriangleAlert` (only)
 * - danger = `AlertCircle` (only)
 * - success = `Check` (only)
 * - ai = `Sparkles` — reserved for AI-generation buttons, never nav.
 *
 * Nav notes: issues uses `OctagonAlert`, NOT `CircleAlert` (an alias of
 * `AlertCircle` — the same glyph as danger). `Settings` is for the user menu
 * only; the Setup nav concept uses `Wrench`.
 *
 * lucide-react ships alias pairs with identical glyphs (legacy names exist
 * for the spinner and warning glyphs); this module canonicalizes one name
 * per pair so call sites stay consistent and grep-able.
 */
export const ICONS = {
  // Nav concepts.
  visibility: Gauge,
  analytics: Bot,
  traffic: TrendingUp,
  prompts: MessageSquareText,
  products: Package,
  runs: ListChecks,
  content: FileText,
  siteHealth: ShieldCheck,
  issues: OctagonAlert,
  knowledgeBase: BookOpen,
  setup: Wrench,
  settings: Settings,
  // Shared UI concepts.
  spinner: LoaderCircle,
  warning: TriangleAlert,
  danger: AlertCircle,
  success: Check,
  ai: Sparkles,
} as const;
