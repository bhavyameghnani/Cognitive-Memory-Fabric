import type { AskResult, BrainData, ClientSummary, MemoryDetail } from './types';

// Empty by default: requests go to /api on the same origin and the Vite dev proxy forwards them (VITE_API_PROXY_TARGET).
const BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '').replace(/\/$/, '');

// Signed Bearer token, held in module memory only: never logged, never written to storage.
let token = '';
export function setToken(value: string) { token = value.trim(); }

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(BASE + url, { ...init, headers });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).detail ?? msg; } catch { /* keep status text */ }
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  clients: () => call<ClientSummary[]>('/api/clients'),
  brain: (clientId: string) => call<BrainData>(`/api/clients/${clientId}/brain`),
  memory: (id: string) => call<MemoryDetail>(`/api/memories/${id}`),
  ask: (question: string) =>
    call<AskResult>('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) }),
  lifecycle: (id: string, body: { new_status: string; reason: string; actor: string; approval_status?: string }) =>
    call<{ memory_id: string; from_status: string; to_status: string }>(`/api/memories/${id}/lifecycle`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
};
