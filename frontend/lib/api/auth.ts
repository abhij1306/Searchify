/**
 * Auth domain endpoints (F2). Cookie-session; `credentials:'include'` is set by
 * the transport. Every response passes through `strictValidate`.
 */
import { apiClient, type ApiRequestOptions } from './client';
import { sessionUserSchema, strictValidate } from './schemas';
import type { SessionUser } from './types';

export const authApi = {
  register: async (email: string, password: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<SessionUser>('/auth/register', { email, password }, options);
    return strictValidate(sessionUserSchema, res, 'auth.register');
  },
  login: async (email: string, password: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<SessionUser>('/auth/login', { email, password }, options);
    return strictValidate(sessionUserSchema, res, 'auth.login');
  },
  logout: (options?: ApiRequestOptions) => apiClient.post<void>('/auth/logout', undefined, options),
  me: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<SessionUser>('/auth/me', options);
    return strictValidate(sessionUserSchema, res, 'auth.me');
  },
};
