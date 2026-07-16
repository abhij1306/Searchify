/**
 * Client-side auth form schemas (F4).
 *
 * These validate the login/register forms in the browser (react-hook-form +
 * zod) BEFORE a request is made. They are intentionally separate from the API
 * contract schemas in `lib/api/schemas.ts` (which validate backend responses):
 * these describe form *input*, the API schemas describe server *output*.
 */
import { z } from 'zod';

/** Shared email + password rules reused by both forms. */
const email = z.string().trim().min(1, 'Email is required.').email('Enter a valid email address.');
const password = z.string().min(8, 'Password must be at least 8 characters.');

export const loginFormSchema = z.object({
  email,
  password: z.string().min(1, 'Password is required.'),
});

export const registerFormSchema = z
  .object({
    email,
    password,
    confirmPassword: z.string().min(1, 'Confirm your password.'),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  });

export type LoginFormValues = z.infer<typeof loginFormSchema>;
export type RegisterFormValues = z.infer<typeof registerFormSchema>;

/**
 * Best-effort human message from a thrown mutation error. The transport already
 * unwraps a JSON `{ detail }` body into `ApiError.message`, so we surface that
 * directly and fall back to a generic message for anything else.
 */
export function authErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}
