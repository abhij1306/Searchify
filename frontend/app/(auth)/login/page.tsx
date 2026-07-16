'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { authApi } from '@/lib/api/auth';
import { queryKeys } from '@/lib/api/query-keys';
import type { SessionUser } from '@/lib/api/types';
import { authErrorMessage, loginFormSchema, type LoginFormValues } from '@/lib/auth/forms';

/**
 * Login page (F4). react-hook-form + zod client validation; on success the
 * `me` cache is primed and the user is sent to the authed landing (`/`), which
 * redirects on to `/visibility` or `/setup`. Any `ApiError` surfaces inline in
 * a danger alert above the form.
 */
export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginFormSchema),
    defaultValues: { email: '', password: '' },
  });

  const mutation = useMutation({
    mutationFn: (values: LoginFormValues) => authApi.login(values.email, values.password),
    onSuccess: (user: SessionUser) => {
      queryClient.setQueryData(queryKeys.auth.me(), user);
      router.replace('/');
    },
  });

  const onSubmit = handleSubmit((values) => mutation.mutateAsync(values).catch(() => undefined));

  return (
    <div className="grid gap-5">
      <div className="grid gap-1">
        <h1 className="text-lg font-semibold text-foreground">Sign in</h1>
        <p className="text-sm text-secondary">Welcome back — sign in to your workspace.</p>
      </div>

      {mutation.isError ? (
        <Alert tone="danger">{authErrorMessage(mutation.error)}</Alert>
      ) : null}

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

      <p className="text-center text-sm text-secondary">
        Don&apos;t have an account?{' '}
        <Link href="/register" className="focus-ring rounded-sm text-accent-text">
          Create one
        </Link>
      </p>
    </div>
  );
}
