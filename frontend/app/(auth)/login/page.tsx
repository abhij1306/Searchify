'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { useForm } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { authApi } from '@/lib/api/auth';
import { authErrorMessage, loginFormSchema, type LoginFormValues } from '@/lib/auth/forms';
import { useAuthMutation } from '@/lib/auth/use-auth-mutation';

/**
 * Login page (F4). react-hook-form + zod client validation; on success the
 * `me` cache is primed and the user is routed directly to `/setup` (no
 * projects yet) or `/visibility` — no marketing-landing bounce. Email is the
 * only sign-in path for now; the OAuth buttons stay in
 * `components/auth/oauth-buttons.tsx` until the backend providers are
 * configured. Any `ApiError` surfaces inline in a danger alert above the form.
 */
export default function LoginPage() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginFormSchema),
    defaultValues: { email: '', password: '' },
  });

  const { mutation, submit } = useAuthMutation((values: LoginFormValues) =>
    authApi.login(values.email, values.password),
  );

  const onSubmit = handleSubmit(submit);

  return (
    <div className="grid gap-5">
      <div className="grid gap-1">
        <h1 className="text-foreground text-lg font-semibold">Sign in</h1>
        <p className="text-secondary text-sm">Welcome back — sign in to your workspace.</p>
      </div>

      {mutation.isError ? <Alert tone="danger">{authErrorMessage(mutation.error)}</Alert> : null}

      <form noValidate onSubmit={onSubmit} className="grid gap-4">
        <Field label="Email" required error={errors.email?.message}>
          {(props) => (
            <Input
              {...props}
              {...register('email')}
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
            />
          )}
        </Field>

        <Field label="Password" required error={errors.password?.message}>
          {(props) => (
            <Input
              {...props}
              {...register('password')}
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
            />
          )}
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting || mutation.isPending}>
          {isSubmitting || mutation.isPending ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>

      <p className="text-secondary text-center text-sm">
        Don&apos;t have an account?{' '}
        <Link href="/register" className="focus-ring text-accent-text rounded-sm">
          Create one
        </Link>
      </p>
    </div>
  );
}
