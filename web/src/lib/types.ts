export type Agent = {
  id: string;
  name: string;
  hostname: string;
  os: string;
  status: "online" | "offline" | string;
  ip_address?: string | null;
  last_seen_at?: string | null;
  created_at: string;
};

export type Command = {
  id: string;
  agent_id: string;
  type: string;
  payload: Record<string, unknown>;
  requires_approval: boolean;
  status: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type AuditEvent = {
  id: string;
  actor: string;
  action: string;
  agent_id?: string | null;
  command_id?: string | null;
  detail: Record<string, unknown>;
  created_at: string;
};

