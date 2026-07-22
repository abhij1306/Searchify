/**
 * Auth domain endpoints (F2). Cookie-session; `credentials:'include'` is set by
 * the transport. Every response passes through `strictValidate`.
 */
import { apiClient, type ApiRequestOptions } from './client';
import { authResponseSchema, oauthStartResponseSchema, strictValidate } from './schemas';
import type { AuthResponse, OAuthProvider, OAuthStartResponse } from './types';

export const authApi = {
  register: async (email: string, password: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<AuthResponse>('/auth/register', { email, password }, options);
    return strictValidate(authResponseSchema, res, 'auth.register').user;
  },
  login: async (email: string, password: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<AuthResponse>('/auth/login', { email, password }, options);
    return strictValidate(authResponseSchema, res, 'auth.login').user;
  },
  logout: (options?: ApiRequestOptions) => apiClient.post<void>('/auth/logout', undefined, options),
  // OAuth scaffold: a configured provider answers 200 with the authorize URL
  // to navigate to; an unconfigured one answers 503
  // (`detail.code = 'oauth_provider_not_configured'`), which callers surface
  // as a coming-soon notice rather than an error.
  oauthStart: async (provider: OAuthProvider, options?: ApiRequestOptions) => {
    const res = await apiClient.get<OAuthStartResponse>(`/auth/oauth/${provider}/start`, options);
    return strictValidate(oauthStartResponseSchema, res, 'auth.oauthStart');
  },
  me: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<AuthResponse>('/auth/me', options);
    return strictValidate(authResponseSchema, res, 'auth.me').user;
  },
};
