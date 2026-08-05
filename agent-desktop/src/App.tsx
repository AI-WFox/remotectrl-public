import { useEffect, useRef, useState } from "react"
import { emit, emitTo, listen } from "@tauri-apps/api/event"
import { getAllWebviewWindows, getCurrentWebviewWindow, WebviewWindow } from "@tauri-apps/api/webviewWindow"
import { open } from "@tauri-apps/plugin-dialog"
import {
  Activity, Camera, ChevronRight, FolderOpen, HardDrive, Info,
  LaptopMinimal, Monitor, Moon, PanelLeft, RefreshCw, ShieldCheck,
  Sun, Unplug, Wifi, XCircle,
} from "lucide-react"
import { toast } from "sonner"
import { AgentBridge, type BridgeMessage, demoState } from "@/lib/bridge"
import { LocalWebcam } from "@/lib/webcam"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail } from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"

type Page = "overview" | "privacy" | "activity" | "settings"
type AgentState = typeof demoState

type ApprovalPayload = { id: string; message: { command_type?: string; payload?: Record<string, unknown> } }

const approvalSlots = ["approval-slot-1", "approval-slot-2", "approval-slot-3", "approval-slot-4"]
const approvalSlotsByKey = new Map<string, string>()

function releaseApprovalSlot(label: string) {
  for (const [key, value] of approvalSlotsByKey) if (value === label) approvalSlotsByKey.delete(key)
  localStorage.removeItem(`approval:${label}`)
}

const navItems: { id: Page; label: string; icon: typeof LaptopMinimal }[] = [
  { id: "overview", label: "Overview", icon: LaptopMinimal },
  { id: "privacy", label: "Access & Privacy", icon: ShieldCheck },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "settings", label: "Settings", icon: HardDrive },
]

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message
  if (typeof error === "string" && error.trim()) return error
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message
  }
  return "Agent request failed. Open Activity for the local error details."
}

function statusKind(status: string) {
  const value = status.toLowerCase()
  if (value.startsWith("connected")) return "online"
  if (value.startsWith("paused")) return "paused"
  if (value.startsWith("connecting") || value.startsWith("reconnecting")) return "connecting"
  return "offline"
}

function compactStatus(status: string) {
  const value = status.toLowerCase()
  if (value.startsWith("connected")) return "Connected"
  if (value.startsWith("connecting")) return "Connecting"
  if (value.startsWith("reconnecting")) return "Reconnecting"
  if (value.startsWith("paused")) return "Paused"
  if (value.startsWith("disconnected")) return "Disconnected"
  return "Not connected"
}

function StatusPill({ status }: { status: string }) {
  const kind = statusKind(status)
  const styles = {
    online: "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    paused: "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    connecting: "border-blue-500/25 bg-blue-500/10 text-blue-700 dark:text-blue-300",
    offline: "border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  }
  const dot = { online: "bg-emerald-500", paused: "bg-amber-500", connecting: "bg-blue-500", offline: "bg-rose-500" }
  return <Badge variant="outline" className={`h-7 gap-2 px-2.5 font-medium ${styles[kind]}`}><span className={`size-2 rounded-full ${dot[kind]}`} /><span title={status}>{compactStatus(status)}</span></Badge>
}

function SessionCard({ label, active, icon: Icon, description }: { label: string; active: boolean; icon: typeof LaptopMinimal; description: string }) {
  return <Card className="rounded-lg border-border/80 shadow-none"><CardContent className="flex min-h-30 items-start gap-3 p-4"><div className={`grid size-10 shrink-0 place-items-center rounded-lg ${active ? "bg-emerald-500/10 text-emerald-600" : "bg-muted text-muted-foreground"}`}><Icon className="size-5" /></div><div className="min-w-0"><div className="flex items-center gap-2"><p className="font-semibold">{label}</p><span className={`size-1.5 rounded-full ${active ? "bg-emerald-500" : "bg-muted-foreground/50"}`} /></div><p className="mt-1 text-xs leading-5 text-muted-foreground">{active ? "Visible session active" : description}</p></div></CardContent></Card>
}

function App() {
  const query = new URLSearchParams(window.location.search)
  if (query.get("view") === "approval") return <ApprovalWindow requestId={query.get("request") ?? ""} />
  if (query.get("view") === "activity") return <ActivityWindow />
  if (query.get("view") === "session") return <SessionIndicatorWindow kind={query.get("kind") === "webcam" ? "webcam" : "screen"} />
  return <AgentConsole />
}

function AgentConsole() {
  const bridge = useRef<AgentBridge | null>(null)
  const webcam = useRef<LocalWebcam | null>(null)
  const [state, setState] = useState<AgentState>(demoState)
  const [page, setPage] = useState<Page>("overview")
  const [ready, setReady] = useState(false)
  const [theme, setTheme] = useState<"light" | "dark">((demoState.config.ui_theme as "light" | "dark") ?? "light")
  const [gateway, setGateway] = useState(state.config.server_url)
  const [agentName, setAgentName] = useState(state.config.agent_name)
  const [token, setToken] = useState("")

  const applyState = (next: unknown) => {
    if (!next || typeof next !== "object") return
    const candidate = next as AgentState
    if (candidate.config && candidate.sessions) {
      void syncSessionIndicatorWindows(candidate.sessions)
      setState(candidate)
      setGateway(candidate.config.server_url)
      setAgentName(candidate.config.agent_name)
      const nextTheme = candidate.config.ui_theme === "dark" ? "dark" : "light"
      setTheme(nextTheme)
      document.documentElement.classList.toggle("dark", nextTheme === "dark")
    }
  }

  useEffect(() => {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("approval:")) localStorage.removeItem(key)
    }
    const localBridge = new AgentBridge()
    bridge.current = localBridge
    webcam.current = new LocalWebcam(localBridge)
    const unsubscribe = localBridge.subscribe((message: BridgeMessage) => handleBridgeMessage(message, localBridge, applyState, webcam.current!))
    const unlistenPromise = listen<ApprovalPayload>("approval-response", ({ payload }) => localBridge.reply(payload.id, payload).catch(() => undefined))
    const approvalFinishedPromise = listen<{ label: string }>("approval-finished", ({ payload }) => releaseApprovalSlot(payload.label))
    const activityStopPromise = listen("activity-local-stop", () => localBridge.call("agent.activity_stop_local").then(applyState).catch((error: Error) => toast.error(error.message)))
    const sessionStopPromise = listen<{ kind: "screen" | "webcam" }>("session-local-stop", ({ payload }) => {
      const stopLocally = async () => {
        const localCaptureStopped = payload.kind === "webcam"
        if (localCaptureStopped) webcam.current?.stop()
        try {
          const nextState = await localBridge.call("agent." + payload.kind + "_stop_local", { local_capture_stopped: localCaptureStopped })
          applyState(nextState)
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "Local session stop failed")
          void localBridge.call("agent.get_state").then(applyState).catch(() => undefined)
        }
      }
      void stopLocally()
    })
    const trayPromise = listen<string>("tray-command", ({ payload }) => {
      if (payload === "agent.pause_toggle") localBridge.call("agent.pause_toggle").then(applyState).catch((error: Error) => toast.error(error.message))
      if (payload === "agent.reset_approvals") localBridge.call("agent.reset_approvals").then(() => toast.success("Session approvals reset"))
    })
    const syncState = () => localBridge.call("agent.get_state").then(applyState).catch(() => undefined)
    const onVisibilityChange = () => {
      if (!document.hidden) syncState()
    }
    window.addEventListener("focus", syncState)
    document.addEventListener("visibilitychange", onVisibilityChange)
    localBridge.start().then(syncState).finally(() => setReady(true))
    return () => {
      unsubscribe()
      unlistenPromise.then((unlisten) => unlisten())
      approvalFinishedPromise.then((unlisten) => unlisten())
      activityStopPromise.then((unlisten) => unlisten())
      sessionStopPromise.then((unlisten) => unlisten())
      trayPromise.then((unlisten) => unlisten())
      window.removeEventListener("focus", syncState)
      document.removeEventListener("visibilitychange", onVisibilityChange)
      localBridge.close()
    }
  }, [])

  const call = async (method: string, params: Record<string, unknown> = {}) => {
    try {
      const result = await bridge.current?.call(method, params)
      applyState(result)
      return result
    } catch (error) {
      toast.error(errorMessage(error))
      throw error
    }
  }

  const switchTheme = async () => {
    const next = theme === "light" ? "dark" : "light"
    document.documentElement.classList.toggle("dark", next === "dark")
    setTheme(next)
    await call("agent.set_theme", { theme: next })
  }

  const chooseFolder = async () => {
    const folder = await open({ directory: true, multiple: false, title: "Allow folder for Web Files" })
    if (typeof folder === "string" && folder.trim()) await call("agent.add_allowed_folder", { path: folder })
  }

  const isEnrolled = Boolean(state.config.agent_id || state.config.enrolled)
  const content = {
    overview: <Overview state={state} ready={ready} onConnect={() => call("agent.connect")} onDisconnect={() => call("agent.disconnect").then(() => toast.success("Disconnected from gateway"))} />,
    privacy: <Privacy state={state} onChooseFolder={chooseFolder} onRemove={(path) => call("agent.remove_allowed_folder", { path })} onReset={() => call("agent.reset_approvals").then(() => toast.success("Session approvals reset"))} onPower={(enabled) => call("agent.power_mode", { enabled })} />,
    activity: <ActivityPage state={state} onConnect={() => call("agent.connect")} />,
    settings: <Settings gateway={gateway} agentName={agentName} token={token} enrolled={isEnrolled} onGateway={setGateway} onName={setAgentName} onToken={setToken} onSave={() => call("agent.update_config", { server_url: gateway, agent_name: agentName })} onEnroll={() => call("agent.update_config", { server_url: gateway, agent_name: agentName }).then(() => call("agent.enroll", { enrollment_token: token })).then(() => { setToken(""); setPage("overview"); toast.success("Device enrolled") })} />,
  }[page]

  return <TooltipProvider><SidebarProvider defaultOpen>
    <Sidebar collapsible="none" className="border-r border-border/80">
      <SidebarHeader className="px-4 pt-5 pb-4"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm"><ShieldCheck className="size-5" /></div><div><p className="font-semibold tracking-tight">RemoteCtrl</p><p className="text-xs text-muted-foreground">Consent-first agent</p></div></div></SidebarHeader>
      <SidebarContent className="px-2"><SidebarGroup><p className="px-2 pb-2 text-[11px] font-semibold tracking-wide text-muted-foreground">AGENT CONSOLE</p><SidebarGroupContent><SidebarMenu>{navItems.map((item) => { const Icon = item.icon; return <SidebarMenuItem key={item.id}><SidebarMenuButton isActive={page === item.id} onClick={() => setPage(item.id)} tooltip={item.label} aria-current={page === item.id ? "page" : undefined} className={`relative h-11 rounded-lg px-3 text-[15px] transition-colors ${page === item.id ? "border border-primary/30 bg-primary/12 font-semibold text-primary shadow-sm before:absolute before:inset-y-2 before:left-0 before:w-0.75 before:rounded-r before:bg-primary [&_svg]:text-primary" : "border border-transparent text-muted-foreground hover:border-border hover:bg-muted/70 hover:text-foreground"}`}><Icon /><span>{item.label}</span></SidebarMenuButton></SidebarMenuItem> })}</SidebarMenu></SidebarGroupContent></SidebarGroup></SidebarContent>
      <SidebarFooter className="gap-3 px-4 pb-5"><div className="rounded-lg border border-border bg-muted/45 p-3"><p className="text-[11px] font-semibold tracking-wide text-muted-foreground">LOCAL ENDPOINT</p><p className="mt-1 truncate text-sm font-medium">{state.config.agent_name}</p><div className="mt-2"><StatusPill status={state.status} /></div></div><DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" className="w-full justify-start">{theme === "light" ? <Sun /> : <Moon />}Theme<ChevronRight className="ml-auto" /></Button></DropdownMenuTrigger><DropdownMenuContent align="start" className="w-44"><DropdownMenuItem onClick={() => { if (theme !== "light") switchTheme() }}><Sun />Light</DropdownMenuItem><DropdownMenuItem onClick={() => { if (theme !== "dark") switchTheme() }}><Moon />Dark</DropdownMenuItem></DropdownMenuContent></DropdownMenu></SidebarFooter><SidebarRail />
    </Sidebar>
    <SidebarInset className="min-h-screen bg-background"><header className="flex h-16 items-center justify-between border-b border-border/80 bg-card px-7"><div className="flex items-center gap-3"><PanelLeft className="size-4 text-muted-foreground" /><div><p className="text-sm font-semibold">{navItems.find((item) => item.id === page)?.label}</p><p className="text-xs text-muted-foreground">This Windows endpoint</p></div></div><div className="flex items-center gap-3"><Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" onClick={() => call("agent.get_state")} aria-label="Refresh state"><RefreshCw className="size-4" /></Button></TooltipTrigger><TooltipContent>Refresh Agent state</TooltipContent></Tooltip><StatusPill status={state.status} /></div></header>
      <main className="mx-auto w-full max-w-6xl p-7">{content}</main>
    </SidebarInset>
  </SidebarProvider><Toaster position="bottom-right" richColors closeButton /></TooltipProvider>
}

function Overview({ state, ready, onConnect, onDisconnect }: { state: AgentState; ready: boolean; onConnect: () => void; onDisconnect: () => void }) {
  return <div className="space-y-6"><section className="flex flex-wrap items-start justify-between gap-5"><div><p className="text-xs font-semibold tracking-wide text-primary">WINDOWS ENDPOINT</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">{state.config.agent_name}</h1><p className="mt-2 max-w-2xl text-sm text-muted-foreground">{state.config.server_url || "Gateway is not configured"}</p></div><div className="flex flex-wrap gap-2"><Dialog><DialogTrigger asChild><Button variant="outline"><Info />Privacy model</Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>Consent-first by design</DialogTitle><DialogDescription>Protected dashboard requests are presented to this Windows user. “Allow for this session” is temporary and resets on disconnect, restart, or a local reset.</DialogDescription></DialogHeader></DialogContent></Dialog><Button variant="outline" className="text-destructive hover:text-destructive" onClick={onDisconnect} disabled={!ready || statusKind(state.status) === "offline"}><Unplug />Disconnect</Button><Button onClick={onConnect} disabled={!ready}><Wifi />{ready ? "Reconnect" : "Preparing Agent"}</Button></div></section>
    <Card className="rounded-lg border-primary/15 bg-primary/[0.035] shadow-none"><CardContent className="flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><LaptopMinimal className="size-5" /></div><div><p className="font-semibold">Endpoint identity</p><p className="text-xs text-muted-foreground">{state.config.agent_id ? `Agent ID: ${state.config.agent_id}` : "This device has not been enrolled"}</p></div></div><div className="h-8 border-l border-border" /><p className="text-sm text-muted-foreground">Every remote action is shown to the person using this Windows device.</p></CardContent></Card>
    <section><div className="mb-3 flex items-end justify-between"><div><h2 className="text-base font-semibold">Sensitive sessions</h2><p className="text-sm text-muted-foreground">Local indicators stay visible while a session is active.</p></div><Badge variant="outline" className="font-medium">{[state.sessions.screen, state.sessions.webcam, state.sessions.activity].filter(Boolean).length} active</Badge></div><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><SessionCard label="Screen" active={state.sessions.screen} icon={LaptopMinimal} description="Screen viewing is idle" /><SessionCard label="Webcam" active={state.sessions.webcam} icon={Wifi} description="Camera sharing is idle" /><SessionCard label="Activity Capture" active={state.sessions.activity} icon={Activity} description="Activity capture is idle" /></div></section>
  </div>
}

function Privacy({ state, onChooseFolder, onRemove, onReset, onPower }: { state: AgentState; onChooseFolder: () => void; onRemove: (path: string) => void; onReset: () => void; onPower: (enabled: boolean) => Promise<unknown> }) {
  const [powerDialogOpen, setPowerDialogOpen] = useState(false)
  const [localPowerEnabled, setLocalPowerEnabled] = useState<boolean | null>(null)
  const reportedPowerEnabled = !state.config.dry_run_power
  const realPowerEnabled = localPowerEnabled ?? reportedPowerEnabled

  useEffect(() => setLocalPowerEnabled(null), [reportedPowerEnabled])

  const changePowerMode = async (enabled: boolean) => {
    if (enabled) {
      setPowerDialogOpen(true)
      return
    }
    setPowerDialogOpen(false)
    setLocalPowerEnabled(false)
    try {
      await onPower(false)
    } catch {
      setLocalPowerEnabled(null)
      // call() already displays the local bridge failure.
    }
  }

  const confirmPowerMode = async () => {
    setLocalPowerEnabled(true)
    setPowerDialogOpen(false)
    try {
      await onPower(true)
    } catch {
      setLocalPowerEnabled(null)
      // call() already displays the local bridge failure.
    }
  }

  return <div className="space-y-6">
    <section>
      <p className="text-xs font-semibold tracking-wide text-primary">LOCAL CONTROLS</p>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">Access & Privacy</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">The Agent user owns these permissions. Web operators cannot lower consent requirements or browse folders that were not allowed here.</p>
    </section>

    <Card className="rounded-lg shadow-none">
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Allowed folders</CardTitle>
          <CardDescription className="mt-1.5">Only these folders can appear in Web Files after a local approval.</CardDescription>
        </div>
        <Button onClick={onChooseFolder}><FolderOpen />Allow folder</Button>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border rounded-lg border border-border">
          {state.config.allowed_folders.length ? state.config.allowed_folders.map((folder) => <div key={folder} className="flex items-center justify-between gap-4 px-4 py-3">
            <div className="flex min-w-0 items-center gap-3"><FolderOpen className="size-4 shrink-0 text-primary" /><span className="truncate font-mono text-xs">{folder}</span></div>
            <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => onRemove(folder)}>Remove</Button>
          </div>) : <div className="px-4 py-10 text-center text-sm text-muted-foreground">No folder has been allowed on this device.</div>}
        </div>
      </CardContent>
    </Card>

    <div className="grid gap-5 lg:grid-cols-2">
      <Card className="rounded-lg shadow-none">
        <CardHeader>
          <CardTitle>Session approvals</CardTitle>
          <CardDescription>"Allow for this session" resets when the Agent disconnects, restarts, or you reset it.</CardDescription>
        </CardHeader>
        <CardContent><Button variant="outline" onClick={onReset}><RefreshCw />Reset session approvals</Button></CardContent>
      </Card>

      <Card className="rounded-lg shadow-none">
        <CardHeader>
          <CardTitle>Power safety</CardTitle>
          <CardDescription>Keep real power actions off for a safe demo. Approval alone never enables a shutdown, restart, or sleep action.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-5">
            <div>
              <p className="text-sm font-medium">Allow real power actions</p>
              <p className={`mt-1 text-xs ${realPowerEnabled ? "font-medium text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>Currently {realPowerEnabled ? "enabled on this device" : "dry-run only"}</p>
            </div>
            <Switch checked={realPowerEnabled} onCheckedChange={changePowerMode} aria-label="Allow real power actions" />
          </div>
          {powerDialogOpen && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <p className="max-w-sm text-xs leading-5 text-muted-foreground">Confirm that this device may perform real shutdown, restart, and sleep actions. Every request will still need local approval.</p>
            <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setPowerDialogOpen(false)}>Keep dry-run</Button><Button size="sm" className="bg-emerald-600 text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-400" onClick={confirmPowerMode}>Enable real mode</Button></div>
          </div>}
        </CardContent>
      </Card>
    </div>

  </div>
}

function ActivityPage({ state, onConnect }: { state: AgentState; onConnect: () => void }) {
  const logs = [...state.logs].reverse()
  return <div className="space-y-6"><section className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-wide text-primary">LOCAL TIMELINE</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Activity</h1><p className="mt-2 text-sm text-muted-foreground">Connection changes, approvals, and local safety events stay on this endpoint.</p></div><Button variant="outline" onClick={onConnect}><Wifi />Reconnect</Button></section><Card className="rounded-lg shadow-none"><CardHeader><CardTitle>Agent events</CardTitle><CardDescription>Latest 1,000 local events are retained while this Agent is running.</CardDescription></CardHeader><CardContent><ScrollArea className="h-[460px] rounded-lg border border-border"><div className="divide-y divide-border">{logs.length ? logs.map((entry, index) => <div className="flex items-start gap-3 px-4 py-3" key={`${entry.message}-${index}`}><span className={`mt-1.5 size-2 rounded-full ${entry.level === "error" ? "bg-rose-500" : "bg-emerald-500"}`} /><div><p className="text-sm">{entry.message}</p><p className="mt-1 text-xs text-muted-foreground">Local Agent event</p></div></div>) : <div className="px-4 py-14 text-center text-sm text-muted-foreground">No local events yet.</div>}</div></ScrollArea></CardContent></Card></div>
}

function Settings({ gateway, agentName, token, enrolled, onGateway, onName, onToken, onSave, onEnroll }: { gateway: string; agentName: string; token: string; enrolled: boolean; onGateway: (value: string) => void; onName: (value: string) => void; onToken: (value: string) => void; onSave: () => void; onEnroll: () => void }) {
  return <div className="space-y-6"><section><p className="text-xs font-semibold tracking-wide text-primary">DEVICE CONFIGURATION</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Settings</h1><p className="mt-2 text-sm text-muted-foreground">Connection settings live only on this Windows device.</p></section>{!enrolled && <Card className="rounded-lg border-amber-500/30 bg-amber-500/5 shadow-none"><CardContent className="flex items-start gap-3 p-5"><Info className="mt-0.5 size-5 text-amber-600" /><div><p className="font-semibold">First-time setup</p><p className="mt-1 text-sm text-muted-foreground">Create an enrollment token from the dashboard, then paste it below to link this device.</p></div></CardContent></Card>}<Card className="rounded-lg shadow-none"><CardHeader><CardTitle>{enrolled ? "Gateway connection" : "Enroll this device"}</CardTitle><CardDescription>Changing the gateway does not expose any service on your local network.</CardDescription></CardHeader><CardContent className="max-w-2xl space-y-5"><label className="grid gap-2 text-sm font-medium">Gateway URL<Input value={gateway} onChange={(event) => onGateway(event.target.value)} placeholder="https://your-gateway.example" /></label><label className="grid gap-2 text-sm font-medium">Agent name<Input value={agentName} onChange={(event) => onName(event.target.value)} placeholder="Design Lab Workstation" /></label><label className="grid gap-2 text-sm font-medium">Enrollment token<Input type="password" value={token} onChange={(event) => onToken(event.target.value)} placeholder="enroll_..." /><span className="text-xs font-normal text-muted-foreground">Only needed for first-time setup or re-enrollment. Tokens are single-use: create a new one in the dashboard for every re-enrollment.</span></label><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={onSave}>Save settings</Button><Button onClick={onEnroll} disabled={!gateway || !agentName || !token}>{enrolled ? "Re-enroll device" : "Enroll this device"}<ChevronRight /></Button></div></CardContent></Card></div>
}

async function handleBridgeMessage(message: BridgeMessage, bridge: AgentBridge, applyState: (state: unknown) => void, webcam: LocalWebcam) {
  if (message.type === "event") {
    if (message.event === "agent.ready") applyState(message.data?.state)
    if (message.event === "agent.status") {
      applyState(message.data?.state)
      const status = String(message.data?.status ?? "")
      if (status.toLowerCase().startsWith("reconnecting")) {
        toast.warning("Gateway connection lost", { id: "gateway-connection", description: status.replace(/^Reconnecting in \d+s:\s*/i, "") })
      } else if (status.toLowerCase().startsWith("disconnected:")) {
        toast.error("Gateway connection failed", { id: "gateway-connection", description: status.replace(/^Disconnected:\s*/i, "") })
      } else if (status.toLowerCase().startsWith("connected")) {
        toast.dismiss("gateway-connection")
      }
    }
    if (message.event === "agent.config") applyState(message.data?.state)
    if (message.event === "agent.session_state") applyState(message.data?.state)
    if (message.event === "agent.command_error") toast.error("Remote action failed", { description: String(message.data?.error ?? "Open Activity for details.") })
    if (message.event === "activity.started") await openActivityWindow()
    if (message.event === "activity.stopped") await closeActivityWindow()
    return
  }
  if (message.type !== "request" || !message.id) return
  if (message.method === "approval.request") {
    const payload = { id: message.id, message: message.params?.message as ApprovalPayload["message"] }
    await openApprovalWindow(payload)
    return
  }
  if (message.method?.startsWith("webcam.")) {
    const action = message.method.slice("webcam.".length)
    const params = message.params ?? {}
    const response = action === "list"
      ? await webcam.list()
      : action === "snapshot"
        ? await webcam.snapshot(params)
        : action === "start"
          ? await webcam.start(params)
          : action === "stop"
            ? webcam.stop()
            : { error: `Unsupported local camera action: ${action}` }
    await bridge.reply(message.id, response)
    return
  }
  if (message.method === "capture.hide_approval_windows" || message.method === "capture.restore_approval_windows") {
    try {
      const windows = await getAllWebviewWindows()
      const approvals = windows.filter((window) => window.label.startsWith("approval-slot-"))
      if (message.method === "capture.hide_approval_windows") await Promise.all(approvals.map((window) => window.hide()))
      // A still capture runs after a decision. Do not resurrect resolved dialogs on restore.
      await bridge.reply(message.id, { ok: true })
    } catch { await bridge.reply(message.id, { ok: true }) }
    return
  }
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]"
  if (value && typeof value === "object") {
    return "{" + Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => JSON.stringify(key) + ":" + stableStringify(item)).join(",") + "}"
  }
  return JSON.stringify(value)
}

async function openApprovalWindow(payload: ApprovalPayload) {
  const key = String(payload.message.command_type ?? "action") + "-" + stableStringify(payload.message.payload ?? {})
  const existingLabel = approvalSlotsByKey.get(key)
  if (existingLabel) {
    localStorage.setItem(`approval:${existingLabel}`, JSON.stringify(payload))
    await emitTo(existingLabel, "approval-data", payload)
    return
  }

  const usedSlots = new Set(approvalSlotsByKey.values())
  const label = approvalSlots.find((slot) => !usedSlots.has(slot))
  if (!label) throw new Error("Four local approval requests are already waiting for a decision.")
  approvalSlotsByKey.set(key, label)
  localStorage.setItem(`approval:${label}`, JSON.stringify(payload))

  const existing = await WebviewWindow.getByLabel(label)
  if (existing) {
    await emitTo(label, "approval-data", payload)
    await existing.show()
    return
  }

  const approval = new WebviewWindow(label, {
    url: `/?view=approval&request=${encodeURIComponent(label)}`,
    title: "RemoteCtrl Approval",
    width: 570,
    height: 420,
    minWidth: 520,
    minHeight: 380,
    alwaysOnTop: true,
    resizable: false,
    closable: false,
    decorations: false,
    center: true,
  })
  await new Promise<void>((resolve, reject) => {
    approval.once("tauri://created", async () => {
      try {
        await approval.show()
        resolve()
      } catch (error) { reject(error) }
    })
    approval.once("tauri://error", (event) => {
      releaseApprovalSlot(label)
      reject(new Error(String(event.payload)))
    })
  })
}
async function openActivityWindow() {
  const label = "activity-indicator"
  const existing = await WebviewWindow.getByLabel(label)
  if (existing) { await existing.show(); return }
  new WebviewWindow(label, { url: "/?view=activity", title: "RemoteCtrl Activity Capture", width: 460, height: 250, minWidth: 420, minHeight: 230, alwaysOnTop: true, resizable: false })
}
async function closeActivityWindow() {
  const existing = await WebviewWindow.getByLabel("activity-indicator")
  if (existing) await existing.close()
}

async function setSessionIndicator(kind: "screen" | "webcam", active: boolean) {
  const label = kind + "-indicator"
  const existing = await WebviewWindow.getByLabel(label)
  if (!active) {
    if (existing) await existing.close()
    return
  }
  if (existing) {
    await existing.show()
    return
  }
  new WebviewWindow(label, {
    url: "/?view=session&kind=" + kind,
    title: kind === "screen" ? "RemoteCtrl Screen Sharing" : "RemoteCtrl Webcam Sharing",
    width: 460,
    height: 250,
    minWidth: 420,
    minHeight: 230,
    alwaysOnTop: true,
    resizable: false,
    closable: false,
  })
}

async function syncSessionIndicatorWindows(sessions: AgentState["sessions"]) {
  await Promise.all([
    setSessionIndicator("screen", Boolean(sessions.screen)),
    setSessionIndicator("webcam", Boolean(sessions.webcam)),
  ])
}

function ApprovalWindow({ requestId }: { requestId: string }) {
  const [payload, setPayload] = useState<ApprovalPayload | null>(() => { const raw = localStorage.getItem(`approval:${requestId}`); return raw ? JSON.parse(raw) as ApprovalPayload : null })
  const resolved = useRef(false)
  useEffect(() => {
    let unlisten: (() => void) | undefined
    listen<ApprovalPayload>("approval-data", ({ payload }) => { resolved.current = false; setPayload(payload) }).then((dispose) => { unlisten = dispose })
    return () => unlisten?.()
  }, [])
  useEffect(() => {
    let unlisten: (() => void) | undefined
    const window = getCurrentWebviewWindow()
    window.onCloseRequested((event) => {
      event.preventDefault()
    }).then((dispose) => { unlisten = dispose })
    return () => unlisten?.()
  }, [payload, requestId])
  const message = payload?.message
  const decide = async (approved: boolean, scope: "single_command" | "current_session") => {
    if (!payload || resolved.current) return
    resolved.current = true
    await emit("approval-response", { id: payload.id, approved, approval_mode: "prompt_once", policy_scope: scope })
    await emit("approval-finished", { label: requestId })
    await getCurrentWebviewWindow().hide()
  }
  return <TooltipProvider><div className="min-h-screen bg-background p-5"><Card className="border-amber-500/30 shadow-none"><CardContent className="space-y-5 p-5"><div className="flex items-start gap-3"><div className="grid size-10 place-items-center rounded-lg bg-amber-500/10 text-amber-600"><ShieldCheck className="size-5" /></div><div><p className="text-xs font-semibold tracking-wide text-amber-700 dark:text-amber-300">LOCAL CONSENT REQUIRED</p><h1 className="mt-1 text-xl font-semibold">Allow remote action?</h1></div></div><div className="rounded-lg border border-border bg-muted/35 p-3"><p className="font-mono text-sm font-semibold">{message?.command_type ?? "Remote action"}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{Object.entries(message?.payload ?? {}).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") || "No additional parameters"}</p></div><p className="text-sm text-muted-foreground">Your decision is sent to the gateway and recorded in the audit trail.</p><div className="flex flex-wrap justify-end gap-2"><Button variant="destructive" onClick={() => decide(false, "single_command")}><XCircle />Deny</Button><Button variant="outline" onClick={() => decide(true, "single_command")}>Allow once</Button><Button onClick={() => decide(true, "current_session")}>Allow for this session</Button></div></CardContent></Card></div></TooltipProvider>
}

function SessionIndicatorWindow({ kind }: { kind: "screen" | "webcam" }) {
  const [stopping, setStopping] = useState(false)
  const Icon = kind === "screen" ? Monitor : Camera
  const label = kind === "screen" ? "Screen sharing" : "Webcam sharing"
  const stop = async () => {
    if (stopping) return
    setStopping(true)
    try {
      await emitTo("main", "session-local-stop", { kind })
      await getCurrentWebviewWindow().close()
    } catch {
      setStopping(false)
    }
  }
  return <TooltipProvider><div className="min-h-screen bg-background p-4"><Card className="h-full border-emerald-500/30 shadow-none"><CardContent className="flex h-full flex-col gap-3 p-5"><div className="flex items-start gap-3"><div className="grid size-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600"><Icon className="size-5" /></div><div><p className="text-xs font-semibold tracking-wide text-emerald-700 dark:text-emerald-300">VISIBLE SESSION</p><h1 className="mt-1 text-lg font-semibold">{label} is active</h1></div></div><p className="text-sm leading-5 text-muted-foreground">This indicator stays visible while the remote session is active. You can stop access locally at any time.</p><div className="mt-auto"><Button variant="destructive" disabled={stopping} onClick={stop}><Unplug />{stopping ? "Stopping..." : "Stop local " + kind}</Button></div></CardContent></Card></div></TooltipProvider>
}

function ActivityWindow() {
  const stop = async () => { await emit("activity-local-stop"); await getCurrentWebviewWindow().close() }
  return <TooltipProvider><div className="min-h-screen bg-background p-4"><Card className="h-full border-emerald-500/30 shadow-none"><CardContent className="flex h-full flex-col gap-3 p-5"><div className="flex items-start gap-3"><div className="grid size-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600"><Activity className="size-5" /></div><div><p className="text-xs font-semibold tracking-wide text-emerald-700 dark:text-emerald-300">VISIBLE SESSION</p><h1 className="mt-1 text-lg font-semibold">Activity capture is active</h1></div></div><p className="text-sm leading-5 text-muted-foreground">The local user can stop this capture immediately. Remote requests remain subject to approval.</p><div className="mt-auto"><Button variant="destructive" onClick={stop}><Unplug />Stop local capture</Button></div></CardContent></Card></div></TooltipProvider>
}

export default App