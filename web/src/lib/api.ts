import type { Agent, AuditEvent, Command } from "./types";

const DEFAULT_API_BASE = globalThis.location?.origin ?? "";
const API_BASE = (import.meta.env.VITE_API_BASE ?? DEFAULT_API_BASE).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function readError(response: Response, fallback: string): Promise<ApiError> {
  try {
    const data = await response.json();
    return new ApiError(data.detail ?? fallback, response.status);
  } catch {
    return new ApiError(fallback, response.status);
  }
}

export async function login(email: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) throw await readError(response, "Login failed");
  const data = await response.json();
  return data.access_token;
}

export async function apiGet<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw await readError(response, `${path} failed`);
  return response.json();
}

export async function createCommand(token: string, agentId: string, type: string, payload: Record<string, unknown> = {}): Promise<Command> {
  const response = await fetch(`${API_BASE}/api/commands`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ agent_id: agentId, type, payload }),
  });
  if (!response.ok) throw await readError(response, "Command failed");
  return response.json();
}

export const API_BASE_URL = API_BASE;

export async function createEnrollmentToken(token: string): Promise<{ token: string }> {
  const response = await fetch(`${API_BASE}/api/enrollment-tokens`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ label: "Dashboard enrollment", reusable: true }),
  });
  if (!response.ok) throw await readError(response, "Token creation failed");
  return response.json();
}

export async function deleteOfflineAgents(token: string): Promise<{ deleted: number }> {
  const response = await fetch(`${API_BASE}/api/agents/offline`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw await readError(response, "Offline agent cleanup failed");
  return response.json();
}

export async function deleteAgent(token: string, agentId: string): Promise<{ deleted: boolean; agent_id: string }> {
  const response = await fetch(`${API_BASE}/api/agents/${agentId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw await readError(response, "Agent removal failed");
  return response.json();
}

export async function loadDashboard(token: string): Promise<{ agents: Agent[]; commands: Command[]; audit: AuditEvent[] }> {
  const [agents, commands, audit] = await Promise.all([
    apiGet<Agent[]>("/api/agents", token),
    apiGet<Command[]>("/api/commands", token),
    apiGet<AuditEvent[]>("/api/audit", token),
  ]);
  return { agents, commands, audit };
}

export function dashboardWsUrl(): string {
  return API_BASE.replace(/^http/, "ws") + "/ws/dashboard";
}
