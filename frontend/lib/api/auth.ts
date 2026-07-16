/**
 * Auth domain endpoints (F2). Cookie-session; `credentials:'include'` is set by
 * the transport. Every response passes through `strictValidate`.
 */
import { apiClient, type ApiRequestOptions } from './client';
import { authResponseSchema, strictValidate } from './schemas';
import type { AuthResponse } from './types';

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
  me: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<AuthResponse>('/auth/me', options);
    return strictValidate(authResponseSchema, res, 'auth.me').user;
  },
};
