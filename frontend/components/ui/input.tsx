import type { ComponentPropsWithoutRef, Ref } from 'react';

import { cn } from '@/lib/utils';

/**
 * 34px control-height input (§8); focus = blue --focus-ring via `.focus-ring`.
 * Midnight well treatment (Phase D2): bg-well inset surface + hairline border.
 * Native <select> controls consume `inputClasses` too, so the same midnight
 * well/focus treatment flows to every select.
 */
export const inputClasses =
  'focus-ring h-[var(--control-height)] w-full rounded-md border border-border bg-well px-2.5 text-sm leading-normal text-foreground transition-[border-color,box-shadow] placeholder:text-subtle hover:border-accent-border focus:border-accent disabled:cursor-not-allowed disabled:opacity-50';

const textareaClasses =
  'focus-ring min-h-[96px] w-full resize-y rounded-md border border-border bg-well px-3 py-2 text-sm leading-normal text-foreground transition-[border-color,box-shadow] placeholder:text-subtle hover:border-accent-border focus:border-accent disabled:cursor-not-allowed disabled:opacity-50';

export function Input({
  className,
  ref,
  ...props
}: Readonly<ComponentPropsWithoutRef<'input'> & { ref?: Ref<HTMLInputElement> }>) {
  return <input ref={ref} className={cn(inputClasses, className)} {...props} />;
}

export function Textarea({
  className,
  ref,
  ...props
}: Readonly<ComponentPropsWithoutRef<'textarea'> & { ref?: Ref<HTMLTextAreaElement> }>) {
  return <textarea ref={ref} className={cn(textareaClasses, className)} {...props} />;
}
