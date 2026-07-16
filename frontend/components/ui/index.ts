/**
 * UI primitives barrel (F3). Token-driven, bridged-token-only components.
 * Note: `trend-chart` is the single cross-run chart owner, now wired into the
 * `/visibility` Trend view — do not add a second chart component.
 */
export { Button, type ButtonProps } from './button';
export { buttonVariants } from './button-variants';
export { Badge, type BadgeProps } from './badge';
export {
  type StatusValue,
  type SentimentValue,
  type ClassificationValue,
  type RunStatusValue,
} from './badge-variants';
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from './card';
export {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from './table';
export { Input, Textarea } from './input';
export { Field } from './field';
export { Dialog } from './dialog';
export {
  Dropdown,
  DropdownTrigger,
  DropdownContent,
  DropdownItem,
  DropdownCheckboxItem,
  DropdownLabel,
  DropdownSeparator,
} from './dropdown';
export { Tooltip, TooltipProvider } from './tooltip';
export { Skeleton } from './skeleton';
export { PageTitle, SectionTitle, Subtitle, Label, Metric } from './typography';
export { Alert, type AlertProps } from './alert';
export { HistoryDrawer, type HistoryItem } from './history-drawer';
export { ScoreRing } from './score-ring';
export { Donut, type DonutSegment } from './donut';
export { TrendChart, type TrendPoint } from './trend-chart';
export { ThemeToggle } from './theme-toggle';
export {
  scoreBand,
  scoreBandStroke,
  scoreBandFill,
  scoreBandText,
  type ScoreBand,
} from './score-band';
