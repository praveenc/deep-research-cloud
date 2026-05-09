/**
 * API client for Deep Research Cloud.
 */
import { config } from './config';
import { getToken } from './auth';

export interface ResearchRequest {
  query: string;
  options?: {
    depth?: 'quick' | 'standard' | 'comprehensive';
    sources?: string[];
  };
}

export interface ResearchResponse {
  taskId: string;
  slug: string;
  status: string;
  statusUrl: string;
  message: string;
}

export interface ResearchStatus {
  taskId: string;
  slug: string;
  query: string;
  status: string;
  depth: string;
  createdAt: string;
  updatedAt: string;
  reportUrl: string | null;
  cost?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    estimatedCostUsd: number;
  };
}

async function authFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = await getToken();
  if (!token) throw new Error('Not authenticated');

  return fetch(`${config.apiUrl}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
}

export async function submitResearch(request: ResearchRequest): Promise<ResearchResponse> {
  const res = await authFetch('research', {
    method: 'POST',
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`Submit failed: ${res.status}`);
  return res.json();
}

export async function getResearchStatus(slug: string): Promise<ResearchStatus> {
  const res = await authFetch(`research/${slug}/status`);
  if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`);
  return res.json();
}
