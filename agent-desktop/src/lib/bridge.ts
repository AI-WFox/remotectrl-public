import { Command, type Child } from "@tauri-apps/plugin-shell"

export type BridgeMessage = {
  type: "event" | "request" | "response"
  id?: string
  event?: string
  method?: string
  data?: Record<string, unknown>
  params?: Record<string, unknown>
  result?: unknown
  ok?: boolean
  error?: string
}

type Listener = (message: BridgeMessage) => void

export class AgentBridge {
  private child: Child | null = null
  private command: Command<string> | null = null
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason: Error) => void }>()
  private listeners = new Set<Listener>()
  private buffer = ""
  private mock = false

  async start() {
    if (!("__TAURI_INTERNALS__" in window)) {
      this.mock = true
      window.setTimeout(() => this.publish({ type: "event", event: "agent.ready", data: { state: demoState } }), 120)
      return
    }
    const command = Command.sidecar("binaries/remotectrl-agent-core")
    this.command = command
    command.stdout.on("data", (chunk: string) => this.consume(chunk))
    command.stderr.on("data", (chunk: string) => this.publish({ type: "event", event: "agent.log", data: { message: chunk, level: "error" } }))
    command.on("close", () => this.publish({ type: "event", event: "agent.status", data: { status: "Disconnected" } }))
    this.child = await command.spawn()
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  async call(method: string, params: Record<string, unknown> = {}) {
    if (this.mock) return this.mockCall(method, params)
    const id = crypto.randomUUID()
    const response = new Promise<unknown>((resolve, reject) => this.pending.set(id, { resolve, reject }))
    await this.write({ type: "request", id, method, params })
    return response
  }

  async reply(id: string, result: Record<string, unknown>) {
    if (this.mock) return
    await this.write({ type: "response", id, result })
  }
  async notify(method: string, params: Record<string, unknown> = {}) {
    if (this.mock) return
    await this.write({ type: "request", id: crypto.randomUUID(), method, params })
  }

  async close() {
    try { await this.call("agent.shutdown") } catch { /* sidecar can already be stopped */ }
    this.child = null
    this.command = null
  }

  private async write(payload: BridgeMessage) {
    if (!this.child) throw new Error("Agent core is not running")
    await this.child.write(`${JSON.stringify(payload)}\n`)
  }

  private consume(chunk: string) {
    this.buffer += chunk
    const lines = this.buffer.split(/\r?\n/)
    this.buffer = lines.pop() ?? ""
    for (const line of lines) {
      if (!line.trim()) continue
      try { this.route(JSON.parse(line) as BridgeMessage) } catch { this.publish({ type: "event", event: "agent.log", data: { message: line, level: "error" } }) }
    }
  }

  private route(message: BridgeMessage) {
    if (message.type === "response" && message.id) {
      const waiter = this.pending.get(message.id)
      if (!waiter) return
      this.pending.delete(message.id)
      message.ok ? waiter.resolve(message.result) : waiter.reject(new Error(message.error ?? "Agent core request failed"))
      return
    }
    this.publish(message)
  }

  private publish(message: BridgeMessage) {
    this.listeners.forEach((listener) => listener(message))
  }

  private async mockCall(method: string, params: Record<string, unknown>) {
    if (method === "agent.get_state") return demoState
    if (method === "agent.update_config") {
      Object.assign(demoState.config, params)
      return demoState
    }
    if (method === "agent.pause_toggle") {
      demoState.config.paused = !demoState.config.paused
      demoState.status = demoState.config.paused ? "Paused" : "Connected"
      this.publish({ type: "event", event: "agent.status", data: { status: demoState.status, state: demoState } })
      return demoState
    }
    if (method === "agent.power_mode") {
      demoState.config.dry_run_power = !params.enabled
      return demoState
    }
    if (method === "agent.add_allowed_folder" && typeof params.path === "string") {
      demoState.config.allowed_folders.push(params.path)
      return demoState
    }
    if (method === "agent.remove_allowed_folder") {
      demoState.config.allowed_folders = demoState.config.allowed_folders.filter((path) => path !== params.path)
      return demoState
    }
    return demoState
  }
}

export const demoState = {
  config: {
    server_url: "https://remotectrl-public-demo.onrender.com",
    agent_id: "demo-agent-7f21",
    agent_name: "Design Lab Workstation",
    allowed_folders: ["C:\\Users\\Demo\\Documents", "D:\\Shared\\Support"],
    paused: false,
    dry_run_power: true,
    ui_theme: "light",
    enrolled: true,
  },
  status: "Connected",
  sessions: { screen: false, webcam: false, keycapture: false, activity: false },
  logs: [
    { message: "Agent is ready for local approval.", level: "info" },
    { message: "Gateway connected.", level: "info" },
  ],
}