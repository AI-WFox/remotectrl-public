import type { Agent, AuditEvent, Command } from "./types";

export const mockAgents: Agent[] = [
  {
    id: "demo-agent-1",
    name: "Design Lab Workstation",
    hostname: "HCMUS-LAB-11",
    os: "Windows 11 Pro",
    status: "online",
    ip_address: "192.168.1.105",
    last_seen_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  },
  {
    id: "demo-agent-2",
    name: "QA Laptop",
    hostname: "QA-WIN-02",
    os: "Windows 10",
    status: "offline",
    ip_address: "192.168.1.118",
    created_at: new Date(Date.now() - 86400_000).toISOString(),
  },
];

export const mockCommands: Command[] = [
  {
    id: "cmd-1",
    agent_id: "demo-agent-1",
    type: "screen.screenshot",
    payload: {},
    requires_approval: true,
    status: "succeeded",
    created_by: "demo-operator@example.invalid",
    created_at: new Date(Date.now() - 60_000).toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: "cmd-2",
    agent_id: "demo-agent-1",
    type: "process.list",
    payload: {},
    requires_approval: false,
    status: "succeeded",
    created_by: "demo-operator@example.invalid",
    created_at: new Date(Date.now() - 120_000).toISOString(),
    updated_at: new Date(Date.now() - 100_000).toISOString(),
  },
];

export const mockAudit: AuditEvent[] = [
  {
    id: "audit-1",
    actor: "agent",
    action: "approval.response",
    agent_id: "demo-agent-1",
    command_id: "cmd-1",
    detail: { approved: true },
    created_at: new Date(Date.now() - 45_000).toISOString(),
  },
  {
    id: "audit-2",
    actor: "demo-operator@example.invalid",
    action: "command.created",
    agent_id: "demo-agent-1",
    command_id: "cmd-1",
    detail: { type: "screen.screenshot" },
    created_at: new Date(Date.now() - 60_000).toISOString(),
  },
];

