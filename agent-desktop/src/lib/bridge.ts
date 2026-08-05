import { invoke } from "@tauri-apps/api/core"
import { listen, type UnlistenFn } from "@tauri-apps/api/event"

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
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason: Error) => void }>()
  private listeners = new Set<Listener>()
  private unlisten: UnlistenFn[] = []
  private buffer = ""

  async start() {
    this.unlisten.push(await listen<string>("agent-bridge-message", ({ payload }) => this.consume(payload)))
    this.unlisten.push(await listen<string>("agent-bridge-stderr", ({ payload }) => {
      this.publish({ type: "event", event: "agent.log", data: { message: payload, level: "error" } })
    }))
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  async call(method: string, params: Record<string, unknown> = {}) {
    const id = crypto.randomUUID()
    const response = new Promise<unknown>((resolve, reject) => this.pending.set(id, { resolve, reject }))
    try {
      await this.write({ type: "request", id, method, params })
    } catch (error) {
      this.pending.delete(id)
      throw error
    }
    return response
  }

  async reply(id: string, result: Record<string, unknown>) {
    await this.write({ type: "response", id, result })
  }

  async notify(method: string, params: Record<string, unknown> = {}) {
    await this.write({ type: "request", id: crypto.randomUUID(), method, params })
  }

  async close() {
    try { await this.call("agent.shutdown") } catch { /* Agent core can already be stopped */ }
    await Promise.all(this.unlisten.splice(0).map((unlisten) => unlisten()))
  }

  private async write(payload: BridgeMessage) {
    await invoke("agent_bridge_write", { payload: JSON.stringify(payload) })
  }

  private consume(chunk: string) {
    this.buffer += chunk
    const lines = this.buffer.split(/\r?\n/)
    this.buffer = lines.pop() ?? ""
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        this.route(JSON.parse(line) as BridgeMessage)
      } catch {
        this.publish({ type: "event", event: "agent.log", data: { message: line, level: "error" } })
      }
    }
  }

  private route(message: BridgeMessage) {
    if (message.type === "response" && message.id) {
      const waiter = this.pending.get(message.id)
      if (!waiter) return
      this.pending.delete(message.id)
      if (message.ok) {
        waiter.resolve(message.result)
      } else {
        waiter.reject(new Error(message.error ?? "Agent core request failed"))
      }
      return
    }
    this.publish(message)
  }

  private publish(message: BridgeMessage) {
    this.listeners.forEach((listener) => listener(message))
  }
}

export const demoState = {
  config: {
    server_url: "",
    agent_id: "",
    agent_name: "This Windows Agent",
    allowed_folders: [] as string[],
    paused: false,
    dry_run_power: true,
    ui_theme: "light",
    enrolled: false,
  },
  status: "Preparing Agent",
  sessions: { screen: false, webcam: false, activity: false },
  logs: [] as { message: string; level: string }[],
}