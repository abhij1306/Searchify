'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { useForm } from 'react-hook-form';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { authApi } from '@/lib/api/auth';
import { authErrorMessage, registerFormSchema, type RegisterFormValues } from '@/lib/auth/forms';
import { useAuthMutation } from '@/lib/auth/use-auth-mutation';

/**
 * Register page (F4). Mirrors the login page: react-hook-form + zod client
 * validation (with a confirm-password match rule), inline `ApiError`, and — on
 * success — priming the `me` cache and redirecting to the authed landing.
 */
export default function RegisterPage() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFormValues>({
    resolver: zodResolver(registerFormSchema),
    defaultValues: { email: '', password: '', confirmPassword: '' },
  });

  const { mutation, submit } = useAuthMutation((values: RegisterFormValues) =>
    authApi.register(values.email, values.password),
  );

  const onSubmit = handleSubmit(submit);

  return (
    <div className="grid gap-5">
      <div className="grid gap-1">
        <h1 className="text-lg font-semibold text-foreground">Create your account</h1>
        <p className="text-sm text-secondary">Start measuring your AI search visibility.</p>
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
              autoComplete="new-password"
              placeholder="At least 8 characters"
            />
          )}
        </Field>

        <Field label="Confirm password" required error={errors.confirmPassword?.message}>
          {(props) => (
            <Input
              {...props}
              {...register('confirmPassword')}
              type="password"
              autoComplete="new-password"
              placeholder="Re-enter your password"
            />
          )}
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting || mutation.isPending}>
          {isSubmitting || mutation.isPending ? 'Creating account…' : 'Create account'}
        </Button>
      </form>

      <p className="text-center text-sm text-secondary">
        Already have an account?{' '}
        <Link href="/login" className="focus-ring rounded-sm text-accent-text">
          Sign in
        </Link>
      </p>
    </div>
  );
}
