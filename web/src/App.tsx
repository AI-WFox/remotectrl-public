import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AppWindow,
  Camera,
  CheckCircle2,
  ChevronRight,
  File as FileIcon,
  FileDown,
  Folder,
  Info,
  KeyRound,
  Laptop,
  LogOut,
  Maximize2,
  Minimize2,
  Moon,
  Monitor,
  Power,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  Sun,
} from "lucide-react";
import { ApiError, createCommand, createDashboardWsTicket, createEnrollmentToken, dashboardWsUrl, deleteAgent, deleteOfflineAgents, loadDashboard, login } from "./lib/api";
import { mockAgents, mockAudit, mockCommands } from "./lib/mock";
import type { Agent, AuditEvent, Command } from "./lib/types";

const modules = [
  { id: "applications", label: "Applications", icon: AppWindow, command: "app.list", safe: false },
  { id: "processes", label: "Processes", icon: Activity, command: "process.list", safe: false },
  { id: "screen", label: "Screen", icon: Monitor, command: "screen.screenshot", safe: false },
  { id: "files", label: "Files", icon: FileDown, command: "files.roots", safe: false },
  { id: "webcam", label: "Webcam", icon: Camera, command: "webcam.list", safe: false },
  { id: "activity", label: "Activity Capture", icon: KeyRound, command: "activity.start", safe: false },
  { id: "power", label: "Power", icon: Power, command: "power.status", safe: false },
];

const appPresets = [
  { id: "notepad", label: "Notepad" },
  { id: "calculator", label: "Calculator" },
  { id: "paint", label: "Paint" },
  { id: "explorer", label: "Explorer" },
  { id: "chrome", label: "Chrome" },
  { id: "brave", label: "Brave" },
];

const protectedProcessNames = new Set([
  "system",
  "registry",
  "smss.exe",
  "csrss.exe",
  "wininit.exe",
  "services.exe",
  "lsass.exe",
  "svchost.exe",
  "explorer.exe",
]);

type Theme = "light" | "dark";
type StreamKind = "screen" | "webcam";
type StreamFrame = { mime: string; frame: string } | null;
type StreamStats = { status: string; fps: number; frames: number; latencyMs: number };
type StreamFrameMap = Record<string, StreamFrame>;
type StreamStatsMap = Record<string, StreamStats>;
type ActivityEvent = { time: string; type: string; detail: Record<string, unknown> };
type ActivityEventMap = Record<string, ActivityEvent[]>;
type AgentSessionState = { screen: boolean; webcam: boolean; activity: boolean };
type AgentSessionStateMap = Record<string, AgentSessionState>;
type AppStartMode = "focus_existing" | "new_instance";

const emptyStreamStats: StreamStats = { status: "idle", fps: 0, frames: 0, latencyMs: 0 };

function stablePayload(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(stablePayload).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => JSON.stringify(key) + ":" + stablePayload(item))
      .join(",") + "}";
  }
  return JSON.stringify(value);
}

function commandRequestKey(agentId: string, commandType: string, payload: Record<string, unknown>): string {
  return agentId + ":" + commandType + ":" + stablePayload(payload);
}

function streamStateKey(agentId: string, stream: StreamKind): string {
  return `${agentId || "unknown"}:${stream}`;
}

export function App() {
  const [theme, setTheme] = useState<Theme>("light");
  const [token, setToken] = useState<string>(() => sessionStorage.getItem("rt_token") ?? "");
  const [demoMode, setDemoMode] = useState<boolean>(() => sessionStorage.getItem("rt_demo") === "true");
  const [agents, setAgents] = useState<Agent[]>(() => (sessionStorage.getItem("rt_demo") === "true" ? mockAgents : []));
  const [commands, setCommands] = useState<Command[]>(() => (sessionStorage.getItem("rt_demo") === "true" ? mockCommands : []));
  const [audit, setAudit] = useState<AuditEvent[]>(() => (sessionStorage.getItem("rt_demo") === "true" ? mockAudit : []));
  const [selectedAgentId, setSelectedAgentId] = useState(() => (sessionStorage.getItem("rt_demo") === "true" ? mockAgents[0].id : ""));
  const [selectedModule, setSelectedModule] = useState(modules[0].id);
  const [loginError, setLoginError] = useState("");
  const [notice, setNotice] = useState(() => sessionStorage.getItem("rt_demo") === "true" ? "Demo mode is active." : "Connecting to the Gateway...");
  const [enrollmentToken, setEnrollmentToken] = useState("");
  const [moduleResultCache, setModuleResultCache] = useState<Record<string, Command>>({});
  const [appStartMode, setAppStartMode] = useState<AppStartMode>("focus_existing");
  const [streamFrames, setStreamFrames] = useState<StreamFrameMap>({});
  const [streamStats, setStreamStats] = useState<StreamStatsMap>({});
  const [activityEvents, setActivityEvents] = useState<ActivityEventMap>({});
  const [agentSessionStates, setAgentSessionStates] = useState<AgentSessionStateMap>({});
  const [isFullscreen, setIsFullscreen] = useState(false);
  const selectedAgentIdRef = useRef(selectedAgentId);
  const manualAgentSelectionRef = useRef(false);
  const selectedAgent = useMemo(() => agents.find((agent) => agent.id === selectedAgentId) ?? agents[0], [agents, selectedAgentId]);
  const downloadedCommandIds = useRef<Set<string>>(new Set());
  const exportedActivityCommandIds = useRef<Set<string>>(new Set());
  const commandRequestsInFlight = useRef<Set<string>>(new Set());
  const recentCommandRequests = useRef<Map<string, number>>(new Map());
  const downloadEffectsReady = useRef(false);
  const activityActive = Boolean(selectedAgent && agentSessionStates[selectedAgent.id]?.activity);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    selectedAgentIdRef.current = selectedAgentId;
  }, [selectedAgentId]);

  useEffect(() => {
    const syncFullscreen = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);

  useEffect(() => {
    setModuleResultCache((current) => updateModuleResultCache(current, commands));
    if (!downloadEffectsReady.current) {
      for (const command of commands) {
        if (command.type === "files.download") downloadedCommandIds.current.add(command.id);
        if (command.type === "activity.export") exportedActivityCommandIds.current.add(command.id);
      }
      downloadEffectsReady.current = true;
      return;
    }
    for (const command of commands) {
      if (command.status === "succeeded" && ["screen.live.stop", "webcam.live.stop"].includes(command.type)) {
        const stream = command.type.startsWith("screen.") ? "screen" : "webcam";
        const key = streamStateKey(command.agent_id, stream);
        setStreamFrames((frames) => ({ ...frames, [key]: null }));
        setStreamStats((stats) => ({ ...stats, [key]: { ...emptyStreamStats, status: "idle" } }));
      }
      if (command.type === "activity.export" && command.status === "succeeded" && command.result && !exportedActivityCommandIds.current.has(command.id)) {
        exportedActivityCommandIds.current.add(command.id);
        const exported = downloadActivityExport(command.result, command.created_at);
        setNotice(exported.ok ? `Downloaded ${exported.name}.` : exported.error);
      }
      if (command.type !== "files.download" || command.status !== "succeeded" || !command.result || downloadedCommandIds.current.has(command.id)) continue;
      downloadedCommandIds.current.add(command.id);
      const downloaded = downloadCommandResult(command.result);
      if (downloaded.ok) {
        setNotice(`Downloaded ${downloaded.name}.`);
      } else {
        setNotice(downloaded.error);
      }
    }
  }, [commands]);

  useEffect(() => {
    if (!token || demoMode) return;
    let disposed = false;
    let websocket: WebSocket | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let retryAttempt = 0;

    const connectRealtime = async () => {
      try {
        const ticket = await createDashboardWsTicket(token);
        if (disposed) return;
        const ws = new WebSocket(dashboardWsUrl(ticket));
        websocket = ws;
        ws.onopen = () => {
          retryAttempt = 0;
          setNotice("Gateway connected. Realtime updates are active.");
          refresh(token);
        };
        ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "agent.session_snapshot" && message.sessions && typeof message.sessions === "object") {
          const snapshot = message.sessions as Record<string, Record<string, unknown>>;
          const next: AgentSessionStateMap = {};
          for (const [agentId, sessions] of Object.entries(snapshot)) next[agentId] = { screen: Boolean(sessions.screen), webcam: Boolean(sessions.webcam), activity: Boolean(sessions.activity) };
          setAgentSessionStates(next);
          return;
        }
        if (message.type === "agent.session_state" && message.sessions && typeof message.sessions === "object") {
          const agentId = String(message.agent_id ?? "");
          if (!agentId) return;
          const source = message.sessions as Record<string, unknown>;
          setAgentSessionStates((current) => ({
            ...current,
            [agentId]: { screen: Boolean(source.screen), webcam: Boolean(source.webcam), activity: Boolean(source.activity) },
          }));
          return;
        }
        if (message.type === "agent.metadata" && message.agent && typeof message.agent === "object") {
          const updated = message.agent as Agent;
          setAgents((current) => current.map((agent) => agent.id === updated.id ? { ...agent, ...updated } : agent));
          return;
        }
        if (message.type === "agent.config_invalidated") {
          const agentId = String(message.agent_id ?? "");
          setModuleResultCache((current) => {
            const next = { ...current };
            delete next[moduleCacheKey(agentId, "files")];
            return next;
          });
          if (agentId === selectedAgentIdRef.current) setNotice("Allowed folders changed on the Agent. Choose a folder again.");
          return;
        }
        if (message.type === "agent.command_error") {
          if (String(message.agent_id ?? "") === selectedAgentIdRef.current) setNotice(`${String(message.command_type ?? "Remote action")} failed: ${String(message.error ?? "Unknown error")}`);
          return;
        }
        if (message.type === "activity.event" && message.event && typeof message.event === "object") {
          const agentId = String(message.agent_id ?? "")
          if (!agentId) return
          const activityEvent = message.event as ActivityEvent
          const segmentId = typeof activityEvent.detail?.segment_id === "string" ? activityEvent.detail.segment_id : ""
          const isTextUpdate = activityEvent.type === "keyboard.text.draft" || activityEvent.type === "keyboard.text"
          setActivityEvents((current) => {
            const events = current[agentId] ?? []
            if (!segmentId || !isTextUpdate) {
              return { ...current, [agentId]: [activityEvent, ...events].slice(0, 1000) }
            }
            const index = events.findIndex((event) => event.detail?.segment_id === segmentId)
            const isEmptyDraft = activityEvent.type === "keyboard.text.draft" && !String(activityEvent.detail?.text ?? "")
            if (isEmptyDraft) {
              return { ...current, [agentId]: events.filter((event) => event.detail?.segment_id !== segmentId) }
            }
            if (index < 0) {
              return { ...current, [agentId]: [activityEvent, ...events].slice(0, 1000) }
            }
            const next = [...events]
            next[index] = activityEvent
            return { ...current, [agentId]: next }
          })
          return
        }
        if (message.type === "stream.frame" && message.frame) {
          const stream = streamKind(message.stream);
          if (!stream) return;
          const key = streamStateKey(String(message.agent_id ?? ""), stream);
          setStreamFrames((frames) => ({ ...frames, [key]: { mime: message.mime ?? "image/jpeg", frame: message.frame } }));
          setStreamStats((stats) => {
            const current = stats[key] ?? emptyStreamStats;
            return {
              ...stats,
              [key]: {
                ...current,
                status: "running",
                frames: current.frames + 1,
                latencyMs: typeof message.sent_at === "number" ? Math.max(0, Math.round(Date.now() - message.sent_at * 1000)) : current.latencyMs,
              },
            };
          });
          return;
        }
        if (message.type === "stream.status") {
          const stream = streamKind(message.stream);
          if (!stream) return;
          const key = streamStateKey(String(message.agent_id ?? ""), stream);
          const nextStatus = message.status ?? "idle";
          if (["stopped", "failed"].includes(nextStatus)) {
            setStreamFrames((frames) => ({ ...frames, [key]: null }));
          }
          setStreamStats((stats) => {
            const current = stats[key] ?? emptyStreamStats;
            return {
              ...stats,
              [key]: nextStatus === "stopped" || nextStatus === "failed"
                ? { ...emptyStreamStats, status: "idle" }
                : {
                    ...current,
                    status: nextStatus,
                    fps: Number(message.fps ?? current.fps),
                    frames: nextStatus === "running" ? 0 : current.frames,
                  },
            };
          });
          return;
        }
      } catch {
        // Ignore malformed realtime events and fall back to polling refresh.
      }
      refresh(token);
        };
        ws.onclose = (event) => {
          if (disposed) return;
          if (event.code === 4401 || event.code === 4403) {
            setNotice("Realtime session expired. Sign in again.");
            signOut();
            return;
          }
          const delays = [1000, 2000, 5000, 10000];
          const delay = delays[Math.min(retryAttempt, delays.length - 1)];
          retryAttempt += 1;
          setNotice("Realtime disconnected. Reconnecting...");
          retryTimer = setTimeout(() => void connectRealtime(), delay);
        };
        ws.onerror = () => ws.close();
      } catch (error) {
        if (disposed) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setNotice("Session expired or invalid. Sign in again.");
          signOut();
          return;
        }
        const delays = [1000, 2000, 5000, 10000];
        const delay = delays[Math.min(retryAttempt, delays.length - 1)];
        retryAttempt += 1;
        setNotice("Realtime authentication failed. Retrying...");
        retryTimer = setTimeout(() => void connectRealtime(), delay);
      }
    };

    refresh(token);
    void connectRealtime();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      websocket?.close();
    };
  }, [token, demoMode]);

  async function refresh(activeToken = token) {
    if (demoMode) {
      setNotice("Demo mode: backend is not required. Start the gateway to control a real agent.");
      return;
    }
    try {
      const data = await loadDashboard(activeToken);
      const nextAgents = data.agents;
      const currentSelectedId = selectedAgentIdRef.current;
      const selected = nextAgents.find((agent) => agent.id === currentSelectedId);
      const hasManualSelection = manualAgentSelectionRef.current;
      const onlineAgent = nextAgents.find((agent) => agent.status === "online");
      setAgents(nextAgents);
      setCommands(data.commands);
      setAudit(data.audit);
      if (!nextAgents.length) {
        chooseAgent("", false);
      } else if (!selected || (!hasManualSelection && !currentSelectedId)) {
        chooseAgent((onlineAgent ?? nextAgents[0]).id, false);
      }
      setNotice(nextAgents.length ? "Gateway connected. Live agent data is active." : "Gateway connected. Create an enrollment token, then connect a Windows agent.");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setNotice("Session expired or invalid. Sign out, then sign in again to create real enrollment tokens.");
        return;
      }
      setAgents([]);
      setCommands([]);
      setAudit([]);
      setNotice("Gateway unavailable. Live controls are disabled. Use Demo mode explicitly from sign-in if needed.");
    }
  }

  function signOut() {
    sessionStorage.removeItem("rt_token");
    sessionStorage.removeItem("rt_demo");
    setToken("");
    setDemoMode(false);
    setEnrollmentToken("");
    setStreamFrames({});
    setStreamStats({});
    setActivityEvents({});
    setAgentSessionStates({});
    downloadedCommandIds.current.clear();
    exportedActivityCommandIds.current.clear();
    downloadEffectsReady.current = false;
    setNotice("Signed out. Sign in to the live gateway for real agent enrollment.");
  }

  async function doLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const nextToken = await login(String(form.get("email")), String(form.get("password")));
      sessionStorage.setItem("rt_token", nextToken);
      sessionStorage.removeItem("rt_demo");
      setDemoMode(false);
      setToken(nextToken);
      setLoginError("");
      await refresh(nextToken);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Login failed");
    }
  }

  function enterDemoMode() {
    sessionStorage.setItem("rt_demo", "true");
    sessionStorage.setItem("rt_token", "demo");
    setDemoMode(true);
    setToken("demo");
    setAgents(mockAgents);
    setCommands(mockCommands);
    setAudit(mockAudit);
    setNotice("Demo mode active. UI is fully inspectable without a running backend.");
  }

  function chooseAgent(agentId: string, manual = true) {
    if (manual) manualAgentSelectionRef.current = true;
    selectedAgentIdRef.current = agentId;
    setSelectedAgentId(agentId);
  }

  async function runCommand(commandType: string, payload: Record<string, unknown> = defaultPayload(commandType)) {
    if (!selectedAgent) {
      setNotice("Select an Agent before running a command.");
      return;
    }
    if (!token) {
      setNotice("Your dashboard session is unavailable. Sign in again.");
      return;
    }
    const startedStream = commandType === "screen.live.start" ? "screen" : commandType === "webcam.live.start" ? "webcam" : null;
    const startedStreamKey = startedStream && selectedAgent ? streamStateKey(selectedAgent.id, startedStream) : null;
    const startedStreamStats = startedStreamKey ? streamStats[startedStreamKey] ?? emptyStreamStats : emptyStreamStats;
    if (startedStream && ["starting", "running"].includes(startedStreamStats.status)) {
      setNotice(`${startedStream} stream is already ${startedStreamStats.status}. Stop it before starting again.`);
      return;
    }
    if (commandType === "activity.start" && activityActive) {
      setNotice("Activity Capture session is already running. Stop it before starting again.");
      return;
    }
    if (startedStream && startedStreamKey) {
      setStreamFrames((frames) => ({ ...frames, [startedStreamKey]: null }));
      setStreamStats((stats) => ({ ...stats, [startedStreamKey]: { ...emptyStreamStats, status: "starting", fps: Number(payload.fps ?? 10) } }));
    }
    if (demoMode) {
      const command: Command = {
        id: `demo-${Date.now()}`,
        agent_id: selectedAgent.id,
        type: commandType,
        payload,
        requires_approval: commandRequiresApproval(commandType),
        status: commandRequiresApproval(commandType) ? "pending_approval" : "succeeded",
        result: demoResult(commandType, payload),
        created_by: "demo-operator",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setCommands((items) => [command, ...items]);
      setAudit((items) => [
        {
          id: `audit-${Date.now()}`,
          actor: "demo-operator",
          action: "command.created",
          agent_id: selectedAgent.id,
          command_id: command.id,
          detail: { type: commandType, target: selectedAgent.name, demo: true },
          created_at: new Date().toISOString(),
        },
        ...items,
      ]);
      setNotice(`${commandType} queued in demo mode.`);
      return;
    }
    if (selectedAgent.status !== "online") {
      setNotice(`${selectedAgent.name} is offline. Select an online agent before running commands.`);
      return;
    }
    const requestKey = commandRequestKey(selectedAgent.id, commandType, payload);
    const lastRequestAt = recentCommandRequests.current.get(requestKey) ?? 0;
    if (commandRequestsInFlight.current.has(requestKey) || Date.now() - lastRequestAt < 350) {
      setNotice(commandType + " is already being submitted. Wait for the current request.");
      return;
    }
    commandRequestsInFlight.current.add(requestKey);
    recentCommandRequests.current.set(requestKey, Date.now());
    try {
      setNotice(commandType + " is being sent to " + selectedAgent.name + "...");
      const command = await createCommand(token, selectedAgent.id, commandType, payload);
      setCommands((items) => [command, ...items.filter((item) => item.id !== command.id)]);
      setNotice(commandType + " sent to " + selectedAgent.name + ".");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Command failed.");
    } finally {
      commandRequestsInFlight.current.delete(requestKey);
    }
  }

  async function makeEnrollmentToken() {
    if (!token) return;
    if (demoMode) {
      setEnrollmentToken("");
      setNotice("Demo mode cannot create a real enrollment token. Sign out, then sign in to the live gateway.");
      return;
    }
    try {
      const response = await createEnrollmentToken(token);
      setEnrollmentToken(response.token);
      setNotice("Enrollment token created. Paste it into the Windows agent app.");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setNotice("Session expired or invalid. Sign out, then sign in again before creating an enrollment token.");
        return;
      }
      setNotice(error instanceof Error ? error.message : "Token creation failed.");
    }
  }

  async function clearOfflineAgents() {
    if (!token || demoMode) {
      setAgents((items) => {
        const nextAgents = items.filter((agent) => agent.status === "online");
        if (!nextAgents.some((agent) => agent.id === selectedAgentId)) {
          setSelectedAgentId(nextAgents[0]?.id ?? "");
        }
        return nextAgents;
      });
      setNotice("Demo offline agents cleared.");
      return;
    }
    try {
      const response = await deleteOfflineAgents(token);
      setNotice(`${response.deleted} offline agent record(s) removed.`);
      await refresh(token);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Offline agent cleanup failed.");
    }
  }

  async function removeAgent(agent: Agent) {
    const onlineCopy = agent.status === "online" ? " This will disconnect the online agent from the gateway." : "";
    if (!window.confirm(`Remove ${agent.name} from dashboard?${onlineCopy}`)) return;
    if (!token || demoMode) {
      setAgents((items) => {
        const nextAgents = items.filter((item) => item.id !== agent.id);
        if (selectedAgentId === agent.id) {
          const nextSelected = nextAgents.find((item) => item.status === "online") ?? nextAgents[0];
          setSelectedAgentId(nextSelected?.id ?? "");
        }
        return nextAgents;
      });
      setNotice(`${agent.name} removed from demo dashboard.`);
      return;
    }
    try {
      await deleteAgent(token, agent.id);
      setNotice(`${agent.name} removed${agent.status === "online" ? " and disconnected" : ""}.`);
      setAgents((items) => {
        const nextAgents = items.filter((item) => item.id !== agent.id);
        if (selectedAgentId === agent.id) {
          const nextSelected = nextAgents.find((item) => item.status === "online") ?? nextAgents[0];
          setSelectedAgentId(nextSelected?.id ?? "");
        }
        return nextAgents;
      });
      await refresh(token);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Agent removal failed.");
    }
  }

  async function toggleFullscreen() {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      return;
    }
    await document.documentElement.requestFullscreen();
  }

  if (!token) {
    return <LoginScreen onLogin={doLogin} onDemo={enterDemoMode} error={loginError} theme={theme} setTheme={setTheme} />;
  }

  const onlineCount = agents.filter((agent) => agent.status === "online").length;
  const pendingApprovals = commands.filter((command) => command.status === "pending_approval").length;
  const activeModule = modules.find((item) => item.id === selectedModule) ?? modules[0];
  const powerStatusCommand = selectedAgent
    ? commands.find((command) => command.agent_id === selectedAgent.id && command.type === "power.status" && command.status === "succeeded" && command.result)
    : undefined;
  const latestModuleCommand = selectedAgent
    ? selectedModule === "power"
      ? commands.find((command) => command.agent_id === selectedAgent.id && command.type.startsWith("power."))
        ?? powerStatusCommand
      : moduleResultCache[moduleCacheKey(selectedAgent.id, selectedModule)]
        ?? commands.find((command) => (
          command.agent_id === selectedAgent.id
          && moduleCommandTypes(selectedModule).includes(command.type)
          && !(selectedAgent.status === "online" && command.status === "failed" && command.error === "Agent offline")
        ))
        ?? commands.find((command) => command.agent_id === selectedAgent.id && moduleCommandTypes(selectedModule).includes(command.type))
    : undefined;
  const commandDisabled = !selectedAgent || (!demoMode && selectedAgent.status !== "online");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={20} /></div>
          <div>
            <strong>RemoteCtrl</strong>
            <span>Consent-first ops</span>
          </div>
        </div>

        <div className="nav-section">
          <div className="nav-section-title">
            <p>Agents</p>
            {agents.some((agent) => agent.status !== "online") && (
              <button onClick={clearOfflineAgents}>Clear offline</button>
            )}
          </div>
          {agents.map((agent) => (
            <div key={agent.id} className={`agent-row ${agent.id === selectedAgentId ? "active" : ""}`}>
              <button className="agent-pick" onClick={() => chooseAgent(agent.id)}>
                <span className={`status-dot ${agent.status}`} />
                <span>
                  <strong>{agent.name}</strong>
                  <small>{agent.ip_address ?? agent.hostname}</small>
                </span>
                <ChevronRight size={16} />
              </button>
              <button className="agent-remove" onClick={() => removeAgent(agent)} title={`Remove ${agent.name}`}>
                Remove
              </button>
            </div>
          ))}
          {!agents.length && (
            <div className="empty-sidebar-state">
              <strong>No agents connected</strong>
              <span>Create an enrollment token and connect the Windows agent.</span>
            </div>
          )}
        </div>

        <div className="nav-section">
          <p>Modules</p>
          {modules.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`module-row ${item.id === selectedModule ? "active" : ""}`}
                onClick={() => setSelectedModule(item.id)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-spacer" />
          <button className="icon-button" onClick={toggleFullscreen} title="Toggle fullscreen">
            {isFullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </button>
          <button className="icon-button" onClick={() => refresh()} title="Refresh"><RefreshCw size={18} /></button>
          <button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} title="Toggle theme">
            {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
          </button>
          <button className="text-button" onClick={signOut} title="Sign out">
            <LogOut size={17} />
            <span>Sign out</span>
          </button>
        </header>

        <section className="agent-hero">
          <div>
            <div className="eyebrow">Selected endpoint</div>
            <h1>{selectedAgent?.name ?? "No agent"}</h1>
            <p>{selectedAgent?.os}{" / "}{selectedAgent?.hostname}{" / "}{selectedAgent?.ip_address ?? "no IP"}</p>
          </div>
          <div className="hero-actions">
            <button className="secondary" onClick={makeEnrollmentToken}>Create enrollment token</button>
            <button className="primary" disabled={commandDisabled} onClick={() => runCommand(activeModule.command)}>
              Run selected module
            </button>
          </div>
        </section>

        <div className={`notice ${demoMode ? "demo" : ""}`}>
          <span>{demoMode ? "Demo mode" : "Runtime"}</span>
          <strong>{notice}</strong>
        </div>

        {enrollmentToken && (
          <div className="token-strip">
            <strong>Enrollment token</strong>
            <code>{enrollmentToken}</code>
          </div>
        )}

        <section className="metrics-grid">
          <Metric label="Agents online" value={`${onlineCount}/${agents.length}`} tone="green" />
          <Metric label="Pending approvals" value={String(pendingApprovals)} tone="amber" />
          <Metric label="Commands logged" value={String(commands.length)} tone="blue" />
          <Metric label="Consent coverage" value="100%" tone="violet" />
        </section>

        <section className="panel module-panel">
            <ModuleSurface
              module={activeModule}
              selectedAgent={selectedAgent}
              runCommand={runCommand}
              refresh={refresh}
              streamFrames={streamFrames}
              streamStats={streamStats}
              activityEvents={selectedAgent ? activityEvents[selectedAgent.id] ?? [] : []}
              latestCommand={latestModuleCommand}
              powerStatusCommand={powerStatusCommand}
              activityActive={activityActive}
              appStartMode={appStartMode}
              setAppStartMode={setAppStartMode}
              commandDisabled={commandDisabled}
            />
        </section>

        <section className="panel activity-section">
          <div className="section-heading">
            <h2>Command Timeline</h2>
            <span>{commands.length} logged</span>
          </div>
          <div className="activity-list">
            {commands.slice(0, 8).map((command) => (
              <div className="activity-row" key={command.id}>
                <span className={`status-pill ${command.status}`}>{command.status}</span>
                <strong>{command.type}</strong>
                <small>{agentName(agents, command.agent_id)} / {command.created_by}</small>
                <time>{formatTime(command.created_at)}</time>
              </div>
            ))}
          </div>
        </section>

        <section className="panel audit-section">
          <div className="section-heading">
            <div>
              <h2>Audit</h2>
              <p>Every sensitive action must leave a trail.</p>
            </div>
            <span>{audit.length} events</span>
          </div>
          <div className="activity-list">
            {audit.slice(0, 10).map((event) => (
              <div className="activity-row audit-row" key={event.id}>
                <CheckCircle2 size={16} />
                <strong>{event.action}</strong>
                <small>{event.actor}</small>
                <time>{formatTime(event.created_at)}</time>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function LoginScreen({ onLogin, onDemo, error, theme, setTheme }: { onLogin: (event: FormEvent<HTMLFormElement>) => void; onDemo: () => void; error: string; theme: Theme; setTheme: (theme: Theme) => void }) {
  return (
    <div className="login-shell">
      <button className="icon-button theme-float" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
        {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
      </button>
      <form className="login-card" onSubmit={onLogin}>
        <div className="brand large">
          <div className="brand-mark"><ShieldCheck size={22} /></div>
          <div>
            <strong>RemoteCtrl</strong>
            <span>Premium endpoint operations</span>
          </div>
        </div>
        <h1>Control center sign in</h1>
        <p>Use the demo admin account or your configured operator account.</p>
        <label>Email<input name="email" type="email" autoComplete="username" placeholder="operator@example.com" /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" placeholder="Enter your password" /></label>
        {error && <div className="error">{error}</div>}
        <button className="primary">Sign in</button>
        <button className="secondary full-width" type="button" onClick={onDemo}>View premium demo dashboard</button>
      </form>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ModuleSurface({
  module,
  selectedAgent,
  runCommand,
  refresh,
  streamFrames,
  streamStats,
  activityEvents,
  latestCommand,
  powerStatusCommand,
  activityActive,
  appStartMode,
  setAppStartMode,
  commandDisabled,
}: {
  module: (typeof modules)[number];
  selectedAgent?: Agent;
  runCommand: (type: string, payload?: Record<string, unknown>) => void;
  refresh: () => void;
  streamFrames: StreamFrameMap;
  streamStats: StreamStatsMap;
  activityEvents: ActivityEvent[];
  latestCommand?: Command;
  powerStatusCommand?: Command;
  activityActive: boolean;
  appStartMode: AppStartMode;
  setAppStartMode: (mode: AppStartMode) => void;
  commandDisabled: boolean;
}) {
  const Icon = module.icon;
  const previewRef = useRef<HTMLDivElement | null>(null);
  const canFullscreenPreview = module.id === "screen" || module.id === "webcam";
  const isLiveModule = module.id === "screen" || module.id === "webcam";
  const isDataModule = ["applications", "processes", "files", "activity"].includes(module.id);
  const activeStream = isLiveModule ? (module.id as StreamKind) : null;
  const activeStreamKey = selectedAgent && activeStream ? streamStateKey(selectedAgent.id, activeStream) : null;
  const activeStats = activeStreamKey ? streamStats[activeStreamKey] ?? emptyStreamStats : emptyStreamStats;
  const activeFrame = activeStreamKey ? streamFrames[activeStreamKey] ?? null : null;
  const liveRunning = activeStats.status === "running";
  const liveStarting = activeStats.status === "starting";
  const liveActive = liveRunning || liveStarting;

  async function togglePreviewFullscreen() {
    const preview = previewRef.current;
    if (!preview) return;
    if (document.fullscreenElement === preview) {
      await document.exitFullscreen();
      return;
    }
    await preview.requestFullscreen();
  }

  return (
    <>
      <div className="module-heading">
        <div className="module-icon"><Icon size={22} /></div>
        <div>
          <h2>{module.label}</h2>
          <p>{module.safe ? "Runs immediately and records command output." : "Requires visible local approval on the agent."}</p>
        </div>
      </div>
      <div className="target-strip"><strong>Target</strong><span>{selectedAgent ? `${selectedAgent.name} / ${selectedAgent.ip_address ?? selectedAgent.hostname} / ${selectedAgent.status}` : "No agent selected"}</span></div>
      <div className={`module-preview ${isDataModule ? "data-layout" : ""}`}>
        <div className="module-actions">
          <div className="action-row">
            {renderControls(module.id, runCommand, liveRunning, liveActive, activityActive, appStartMode, setAppStartMode, commandDisabled, latestCommand)}
            <button className="secondary" onClick={() => refresh()}>Refresh audit trail</button>
          </div>
          {isLiveModule && (
            <div className="stream-stats">
              <span title="Current live stream state.">{activeStats.status}</span>
              <span title="FPS: frames per second the Agent tries to send.">{activeStats.fps || 10} FPS <Info size={12} /></span>
              <span title="Frames: total frames this browser received in the current live session.">{activeStats.frames} frames <Info size={12} /></span>
              <span title="ms: estimated delay from Agent frame send time to browser receive time.">{activeStats.latencyMs} ms <Info size={12} /></span>
            </div>
          )}
          {!isDataModule && (
            <div className="preview-window" ref={previewRef}>
              <div className="preview-titlebar">
                <div className="preview-dots"><span /><span /><span /></div>
                {canFullscreenPreview && (
                  <button className="preview-fullscreen" onClick={togglePreviewFullscreen} title="Fullscreen preview">
                    <Maximize2 size={15} />
                  </button>
                )}
              </div>
              <div className="preview-content">
                {activeFrame ? (
                  <img className="stream-frame" src={`data:${activeFrame.mime};base64,${activeFrame.frame}`} alt={`${module.label} stream frame`} />
                ) : (
                  <>
                    <Laptop size={42} />
                    <strong>{selectedAgent?.hostname ?? "No endpoint selected"}</strong>
                    <p>{copyForModule(module.id)}</p>
                  </>
                )}
              </div>
            </div>
          )}
          {module.id === "activity" && <LiveActivityFeed events={activityEvents} />}
          <ResultView moduleId={module.id} command={latestCommand} powerStatusCommand={powerStatusCommand} runCommand={runCommand} commandDisabled={commandDisabled} />
        </div>
      </div>
    </>
  );
}

function renderControls(moduleId: string, runCommand: (type: string, payload?: Record<string, unknown>) => void, liveRunning: boolean, liveActive: boolean, activityActive: boolean, appStartMode: AppStartMode, setAppStartMode: (mode: AppStartMode) => void, commandDisabled: boolean, latestCommand?: Command) {
  if (moduleId === "screen" || moduleId === "webcam") {
    const webcamDiagnostics = moduleId === "webcam" ? latestCommand?.result : undefined;
    const isWebViewCamera = webcamDiagnostics?.capture_backend === "webview2";
    const webcamReady = moduleId !== "webcam" || ((isWebViewCamera || Boolean(webcamDiagnostics?.opencv_available)) && Boolean(webcamDiagnostics?.available ?? true) && Number(webcamDiagnostics?.count ?? 0) > 0);
    const webcamMessage = moduleId === "webcam" && !webcamReady
      ? webcamDiagnostics?.error
        ? isWebViewCamera
          ? "The local Windows camera service is not available. Reopen the Agent and check cameras again."
          : "This Agent EXE cannot load bundled OpenCV. Download and run the latest RemoteCtrlAgent.exe, then check cameras again."
        : "Check cameras before starting webcam live."
      : "";
    return (
      <div className="control-stack">
        {moduleId === "webcam" && <button className="secondary" onClick={() => runCommand("webcam.list")} disabled={commandDisabled}>Check Cameras</button>}
        <button
          className="primary"
          title={webcamMessage || undefined}
          onClick={() => runCommand(`${moduleId}.live.start`, moduleId === "webcam" ? { fps: 15, quality: 40, camera_index: 0, width: 640, height: 360 } : { fps: 10, quality: 65 })}
          disabled={commandDisabled || liveActive || !webcamReady}
        >
          <Play size={16} /> Start Live
        </button>
        <button className="secondary" onClick={() => runCommand(`${moduleId}.live.stop`)} disabled={commandDisabled || !liveRunning}>
          <Square size={16} /> Stop Live
        </button>
        {moduleId === "screen" && <button className="secondary" onClick={() => runCommand("screen.screenshot", { quality: 85 })} disabled={commandDisabled} title="Save a full-resolution still without stopping the live stream">Capture Still</button>}
        {moduleId === "webcam" && <button className="secondary" onClick={() => runCommand("webcam.snapshot", { quality: 85, width: 1280, height: 720 })} disabled={commandDisabled || !webcamReady} title="Save a still camera image without stopping the live stream">Capture Snapshot</button>}
        {webcamMessage && <div className="inline-hint danger-text">{webcamMessage}</div>}
      </div>
    );
  }
  if (moduleId === "applications") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand("app.list")} disabled={commandDisabled}>Refresh Applications</button>
        <div className="segmented-control" aria-label="Application start mode">
          <button className={appStartMode === "focus_existing" ? "active" : ""} onClick={() => setAppStartMode("focus_existing")}>Focus existing</button>
          <button className={appStartMode === "new_instance" ? "active" : ""} onClick={() => setAppStartMode("new_instance")}>New instance</button>
        </div>
        <div className="preset-grid">
          {appPresets.map((preset) => (
            <button className="secondary compact" key={preset.id} disabled={commandDisabled} onClick={() => runCommand("app.start", { preset: preset.id, mode: appStartMode })}>
              {preset.label}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (moduleId === "processes") {
    return <button className="primary" onClick={() => runCommand("process.list")} disabled={commandDisabled}>Refresh Processes</button>;
  }
  if (moduleId === "files") {
    return <button className="primary" onClick={() => runCommand("files.roots")} disabled={commandDisabled}>Choose Folder</button>;
  }
  if (moduleId === "power") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand("power.status")} disabled={commandDisabled}>Refresh Power Status</button>
        <div className="danger-note">Dry-run by default. Real power actions require Agent-side real mode and local approval.</div>
        {["shutdown", "restart", "sleep"].map((action) => (
          <button
            className="secondary"
            disabled={commandDisabled}
            key={action}
            onClick={() => {
              if (window.confirm(`Request ${action} on the agent? The agent still requires local approval.`)) {
                runCommand(`power.${action}`);
              }
            }}
          >
            {action[0].toUpperCase() + action.slice(1)}
          </button>
        ))}
      </div>
    );
  }
  if (moduleId === "activity") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand("activity.start")} disabled={commandDisabled || activityActive}>Start Activity Session</button>
        <button className="secondary" onClick={() => runCommand("activity.stop")} disabled={commandDisabled || !activityActive}>Stop Session</button>
        <button className="secondary" onClick={() => runCommand("activity.export")} disabled={commandDisabled}>Export Activity</button>
      </div>
    );
  }
  return <button className="primary" onClick={() => runCommand("process.list")} disabled={commandDisabled}>Run</button>;
}

function ResultView({ moduleId, command, powerStatusCommand, runCommand, commandDisabled }: { moduleId: string; command?: Command; powerStatusCommand?: Command; runCommand: (type: string, payload?: Record<string, unknown>) => void; commandDisabled: boolean }) {
  if (moduleId === "power") {
    return <PowerResult command={command} telemetry={powerStatusCommand?.result} />;
  }
  if (!command) {
    return <div className="empty-result">Run a command to show live results here.</div>;
  }
  if (command.error) {
    return (
      <div className="result-card danger">
        <span>Latest error</span>
        <pre>{command.error}</pre>
      </div>
    );
  }
  if (!command.result) {
    return (
      <div className="empty-result">
        <strong>{command.type}</strong>
        <span>{command.status}</span>
      </div>
    );
  }
  if (moduleId === "applications") return <ApplicationsResult result={command.result} runCommand={runCommand} />;
  if (moduleId === "processes") return <ProcessesResult result={command.result} runCommand={runCommand} />;
  if (command.type === "files.download") return <DownloadResult result={command.result} />;
  if (moduleId === "files") return <FilesResult result={command.result} runCommand={runCommand} commandDisabled={commandDisabled} />;
  if (moduleId === "screen" || moduleId === "webcam") return <MediaResult command={command} />;
  if (moduleId === "activity" && command.type === "activity.export") return <div className="state-card"><strong>Activity export downloaded</strong><p>The current session log was saved to the browser download folder.</p></div>;
  if (moduleId === "activity") return <ActivityResult result={command.result} />;
  return <DeveloperDetails result={command.result} />;
}

function ApplicationsResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const items = asRecords(result.items).slice(0, 40);
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>Running applications</strong><span>{Number(result.count ?? items.length)} found</span></div>
      {items.map((item) => {
        const appKey = String(item.app_key ?? "");
        const appName = String(item.name || appKey || "Unknown application");
        const windows = Number(item.window_count ?? 1);
        return (
          <div className="result-row" key={appKey}>
            <div className="row-main">
              <strong>{appName}</strong>
              <div className="row-meta"><span>{windows} visible window{windows === 1 ? "" : "s"}</span></div>
            </div>
            <button className="row-action" onClick={() => confirmAndRun("Close all windows for " + appName + "?", () => runCommand("app.stop", { app_key: appKey }))}>Close all</button>
          </div>
        );
      })}
      {!items.length && <div className="empty-result">No visible applications found.</div>}
      <DeveloperDetails result={result} />
    </div>
  );
}

function ProcessesResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const apps = asRecords(result.apps).slice(0, 40);
  const appProcessNames = new Set(apps.flatMap((item) => Array.isArray(item.process_names) ? item.process_names.map((name) => String(name).toLowerCase()) : []));
  const items = asRecords(result.items)
    .filter((item) => String(item.name ?? "").trim() && !appProcessNames.has(String(item.name ?? "").toLowerCase()))
    .sort((left, right) => {
      const leftName = String(left.name ?? "").toLowerCase();
      const rightName = String(right.name ?? "").toLowerCase();
      const leftProtected = protectedProcessNames.has(leftName) ? 1 : 0;
      const rightProtected = protectedProcessNames.has(rightName) ? 1 : 0;
      if (leftProtected !== rightProtected) return leftProtected - rightProtected;
      return String(left.name ?? "").localeCompare(String(right.name ?? ""));
    })
    .slice(0, 60);
  return (
    <div className="result-list split-list">
      <div className="result-list-heading"><strong>Running apps</strong><span>{apps.length}/{Number(result.app_count ?? apps.length)} shown</span></div>
      {apps.map((item) => {
        const appKey = String(item.app_key ?? "");
        const appName = String(item.name || appKey || "Unknown application");
        const windows = Number(item.window_count ?? 1);
        return (
          <div className="result-row app-row" key={"app-" + appKey}>
            <div className="row-main">
              <strong>{appName}</strong>
              <div className="row-meta"><span>{windows} visible window{windows === 1 ? "" : "s"}</span></div>
            </div>
            <button className="row-action" onClick={() => confirmAndRun("Close all windows for " + appName + "?", () => runCommand("app.stop", { app_key: appKey }))}>Close all</button>
          </div>
        );
      })}
      {!apps.length && <div className="empty-result">No visible applications found.</div>}
      <div className="result-list-heading secondary-heading"><strong>Background processes</strong><span>{items.length}/{Number(result.count ?? items.length)} shown</span></div>
      {items.map((item) => {
        const name = String(item.name ?? "").toLowerCase();
        const protectedProcess = protectedProcessNames.has(name);
        return (
          <div className={"result-row " + (protectedProcess ? "guarded-row" : "")} key={String(item.pid) + "-" + String(item.name)}>
            <div className="row-main">
              <strong>{String(item.name ?? "Unknown process")}</strong>
              <div className="row-meta">
                <span>PID {String(item.pid ?? "-")}</span>
                <span>{formatBytes(Number(item.memory_mb ?? 0) * 1024 * 1024)}</span>
                <span>{String(item.cpu ?? 0)}% CPU</span>
                <span>{String(item.status ?? "unknown")}</span>
              </div>
            </div>
            <button className={"row-action " + (protectedProcess ? "guarded" : "")} disabled={protectedProcess} onClick={() => confirmAndRun("Kill process " + String(item.name) + " (PID " + String(item.pid) + ")?", () => runCommand("process.kill", { pid: item.pid }))}>{protectedProcess ? "Guarded" : "Kill"}</button>
          </div>
        );
      })}
      <DeveloperDetails result={result} />
    </div>
  );
}

function FilesResult({ result, runCommand, commandDisabled }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void; commandDisabled: boolean }) {
  const roots = asRecords(result.roots);
  const entries = asRecords(result.entries).slice(0, 80);
  if (!roots.length && !entries.length && !result.path && result.requires_selection) {
    return (
      <div className="empty-result">
        <strong>No allowed folders yet</strong>
        <span>Ask the Agent user to click ?Allow folder for Web Files? in the Agent app.</span>
        <DeveloperDetails result={result} />
      </div>
    );
  }
  if (roots.length && !entries.length && !result.path) {
    return (
      <div className="result-list">
        <div className="result-list-heading"><strong>Choose an allowed folder</strong><span>{roots.length} roots</span></div>
        <div className="inline-hint">Only folders added on the Agent app appear here.</div>
        {commandDisabled && <div className="inline-hint">The selected Agent is offline. Reconnect it before opening a folder.</div>}
        {roots.map((root) => (
          <div className="result-row" key={String(root.path)}>
            <div className="row-main">
              <strong className="entry-name"><Folder size={17} /> {String(root.name || root.path)}</strong>
              <div className="row-meta"><span>{root.exists ? "Available" : "Missing"}</span><span>Locally approved root</span></div>
            </div>
            <button type="button" className="row-action" disabled={commandDisabled || !root.exists || !root.is_dir} onClick={() => runCommand("files.list", { path: root.path })}>Open</button>
          </div>
        ))}
        <DeveloperDetails result={result} />
      </div>
    );
  }
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>{displayAllowedPath(result.path, result.allowed_root)}</strong><span>{entries.length} entries</span></div>
      {typeof result.path === "string" && <FileBreadcrumb path={result.path} rootPath={typeof result.allowed_root === "string" ? result.allowed_root : undefined} runCommand={runCommand} />}
      {entries.map((entry) => (
        <div className="result-row" key={String(entry.path ?? entry.name)}>
          <div className="row-main">
            <strong className="entry-name">{entry.is_dir ? <Folder size={17} /> : <FileIcon size={17} />} {String(entry.name ?? "")}</strong>
            <div className="row-meta">
              <span>{entry.is_dir ? "Folder" : "File"}</span>
              <span>{formatBytes(Number(entry.size ?? 0))}</span>

            </div>
          </div>
          {entry.is_dir ? (
            <button type="button" className="row-action" disabled={commandDisabled} onClick={() => runCommand("files.list", { path: entry.path })}>Open</button>
          ) : (
            <button type="button" className="row-action" disabled={commandDisabled} onClick={() => runCommand("files.download", { path: entry.path })}>Download</button>
          )}
        </div>
      ))}
      <DeveloperDetails result={result} />
    </div>
  );
}


function displayAllowedPath(rawPath: unknown, rawRoot: unknown): string {
  if (typeof rawPath !== "string" || typeof rawRoot !== "string") return "Allowed folder";
  const parts = buildPathBreadcrumb(rawPath, rawRoot).slice(1).map((part) => part.label);
  return parts.length ? parts.join(" / ") : "Allowed folder";
}

function FileBreadcrumb({ path, rootPath, runCommand }: { path: string; rootPath?: string; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const parts = buildPathBreadcrumb(path, rootPath);
  if (!parts.length) return null;
  return (
    <div className="file-breadcrumb" aria-label="Current directory">
      {parts.map((part, index) => (
        <button key={`${part.path}-${index}`} onClick={() => !part.path ? runCommand("files.roots") : runCommand("files.list", { path: part.path })} title={index === 0 ? "Return to allowed folders" : "Open this folder"}>
          {part.label}
        </button>
      ))}
    </div>
  );
}

function buildPathBreadcrumb(rawPath: string, rootPath?: string): { label: string; path: string }[] {
  const normalized = normalizeWindowsPath(rawPath);
  const normalizedRoot = rootPath ? normalizeWindowsPath(rootPath) : "";
  const virtualRoot = { label: "Allowed folders", path: "" };
  if (!normalizedRoot || !isPathInsideRoot(normalized, normalizedRoot)) return [virtualRoot];

  const rootParts = splitWindowsPath(normalizedRoot);
  const rootLabel = /^[A-Za-z]:$/.test(normalizedRoot)
    ? normalizedRoot
    : rootParts[rootParts.length - 1] || normalizedRoot;
  const parts: { label: string; path: string }[] = [virtualRoot, { label: rootLabel, path: normalizedRoot }];
  const relative = normalized.slice(normalizedRoot.length).replace(/^\\+/, "");
  const descendants = relative.split("\\").filter(Boolean);
  let cursor = normalizedRoot;
  for (const descendant of descendants) {
    cursor = cursor + (cursor.endsWith("\\") ? "" : "\\") + descendant;
    parts.push({ label: descendant, path: cursor });
  }
  return parts;
}

function normalizeWindowsPath(value: string): string {
  return value.replace(/\//g, "\\").replace(/\\+$/g, "");
}

function splitWindowsPath(value: string): string[] {
  const normalized = normalizeWindowsPath(value);
  const driveMatch = normalized.match(/^([A-Za-z]:)(?:\\|$)/);
  if (!driveMatch) return normalized.split("\\").filter(Boolean);
  const tail = normalized.slice(driveMatch[1].length).replace(/^\\/, "");
  return [driveMatch[1], ...tail.split("\\").filter(Boolean)];
}

function joinWindowsParts(parts: string[]): string {
  if (!parts.length) return "";
  const [first, ...rest] = parts;
  if (/^[A-Za-z]:$/.test(first)) return rest.length ? `${first}\\${rest.join("\\")}` : `${first}\\`;
  return parts.join("\\");
}

function isPathInsideRoot(path: string, root: string): boolean {
  const left = normalizeWindowsPath(path).toLowerCase();
  const right = normalizeWindowsPath(root).toLowerCase();
  return left === right || left.startsWith(`${right}\\`);
}

function DownloadResult({ result }: { result: Record<string, unknown> }) {
  return (
    <div className="state-card">
      <strong>Downloaded {String(result.name ?? "file")}</strong>
      <p>The browser download was started after Agent approval.</p>
      <DeveloperDetails result={result} />
    </div>
  );
}

function MediaResult({ command }: { command: Command }) {
  const result = command.result ?? {};
  if (command.type === "webcam.list") {
    const cameras = asRecords(result.items);
    return (
      <div className="result-list">
        <div className="result-list-heading"><strong>Camera diagnostics</strong><span>{cameras.length} camera(s)</span></div>
        {Boolean(result.error) && <div className="result-card danger"><span>Webcam issue</span><pre>{String(result.error)}</pre></div>}
        {cameras.map((camera) => (
          <div className="result-row" key={String(camera.index)}>
            <div className="row-main"><strong>{String(camera.label ?? `Camera ${camera.index}`)}</strong><div className="row-meta"><span>Index {String(camera.index)}</span><span>{result.capture_backend === "webview2" ? "Windows camera service" : "OpenCV bundled"}</span></div></div>
          </div>
        ))}
        <DeveloperDetails result={result} />
      </div>
    );
  }
  if (typeof result.image === "string" && result.image) {
    return (
      <div className="result-list">
        <div className="result-list-heading"><strong>{command.type === "screen.screenshot" ? "Screenshot" : "Snapshot"}</strong><span>{String(result.width ?? "")} {result.height ? `x ${result.height}` : ""}</span></div>
        <div className="media-result"><img src={`data:${result.mime ?? "image/jpeg"};base64,${result.image}`} alt={command.type} /></div>
        <DeveloperDetails result={result} />
      </div>
    );
  }
  return <div className="state-card"><strong>{String(result.status ?? command.type)}</strong><p>{String(result.stream ?? result.mode ?? result.error ?? "Request updated.")}</p><DeveloperDetails result={result} /></div>;
}

function PowerResult({ command, telemetry }: { command?: Command; telemetry?: Record<string, unknown> | null }) {
  const actionResult = command?.type !== "power.status" ? command?.result : undefined;
  const statusResult = command?.type === "power.status" && command.result ? command.result : telemetry;
  return (
    <div className="state-card">
      {statusResult ? (
        <>
          {Boolean(statusResult.dry_run_power) && <div className="danger-note">Dry-run mode active. Enable real power actions on the Agent before shutdown/restart/sleep can execute.</div>}
          <div className="power-grid">
            <PowerMetric tone="temp" label="CPU usage" value={formatNullable(statusResult.cpu_percent, "%")} />
            <PowerMetric tone="uptime" label="System uptime" value={formatDuration(Number(statusResult.system_uptime_seconds ?? 0))} />
            <PowerMetric tone="battery" label="Battery" value={formatBattery(statusResult.battery_percent, statusResult.battery_plugged)} />
          </div>
        </>
      ) : (
        <div className="inline-hint">Refresh Power Status to load CPU, uptime, and battery telemetry.</div>
      )}
      {command && command.type !== "power.status" && (
        <div className={"power-command-state " + (command.error ? "danger-note" : "")}>
          <strong>{command.type.replace("power.", "")}</strong>
          <span className={"status-pill " + command.status}>{command.status}</span>
          <p>{command.error ?? String(actionResult?.message ?? "Waiting for local approval or Agent result.")}</p>
        </div>
      )}
      {statusResult && <DeveloperDetails result={statusResult} />}
    </div>
  );
}

function PowerMetric({ tone, label, value }: { tone: string; label: string; value: string }) {
  return (
    <div className={`power-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatNullable(value: unknown, suffix = ""): string {
  if (value === null || value === undefined || value === "") return "Unavailable";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric}${suffix}` : String(value);
}

function formatBattery(percent: unknown, plugged: unknown): string {
  if (percent === null || percent === undefined) return "Unavailable";
  return `${Number(percent).toFixed(0)}%${plugged === true ? " plugged" : ""}`;
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "Unavailable";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function LiveActivityFeed({ events }: { events: ActivityEvent[] }) {
  return (
    <div className="result-list" aria-live="polite">
      <div className="result-list-heading"><strong>Live device activity</strong><span>{events.length} event(s)</span></div>
      {events.length ? events.slice(0, 80).map((event, index) => (
        <div className="result-row" key={`${event.time}-${event.type}-${index}`}>
          <div className="row-main"><strong>{activityEventLabel(event)}</strong><div className="row-meta"><span>{activityEventSummary(event)}</span></div></div>
          <time>{formatTime(event.time)}</time>
        </div>
      )) : <div className="empty-result"><strong>Waiting for approved activity session</strong><p>Events from this selected device will appear here in real time.</p></div>}
    </div>
  );
}

function activityEventLabel(event: ActivityEvent): string {
  return event.type === "keyboard.text.draft" ? "keyboard text" : event.type.replace(/\./g, " ");
}
function activityEventSummary(event: ActivityEvent): string {
  const detail = event.detail ?? {};
  const window = detail.window as Record<string, unknown> | undefined;
  if (typeof detail.text === "string") return detail.text;
  if (typeof detail.keys === "string") return detail.keys;
  if (typeof detail.key === "string") return detail.key;
  if (typeof detail.title === "string") return `${String(detail.process ?? "App")}: ${detail.title}`;
  if (window && typeof window.title === "string") return `${String(window.process ?? "App")}: ${window.title}`;
  if (typeof detail.x === "number" && typeof detail.y === "number") return `Click at ${detail.x}, ${detail.y}`;
  return "Device activity";
}
function ActivityResult({ result }: { result: Record<string, unknown> }) {
  const events = asRecords(result.events).slice(-30).reverse();
  return (
    <div className="state-card">
      <strong>{String(result.status ?? result.mode ?? "Activity capture")}</strong>
      {events.length ? (
        <div className="mini-log">
          {events.map((event, index) => (
            <div key={`${event.time}-${index}`}><span>{String(event.time ?? "")}</span><strong>{String(event.type ?? "event")}</strong><small>{JSON.stringify(event.detail ?? {})}</small></div>
          ))}
        </div>
      ) : result.text ? <pre>{String(result.text)}</pre> : <p>{String(result.mode ?? "Visible session updated.")}</p>}
      <DeveloperDetails result={result} />
    </div>
  );
}

function DeveloperDetails({ result }: { result: Record<string, unknown> }) {
  return (
    <details className="developer-details">
      <summary>Developer details</summary>
      <pre>{JSON.stringify(result, null, 2).slice(0, 1800)}</pre>
    </details>
  );
}

function copyForModule(id: string): string {
  const copy: Record<string, string> = {
    applications: "Focus existing apps or launch a new approved instance.",
    processes: "Separate visible apps from background tasks.",
    screen: "Capture screenshots or stream live after local consent.",
    files: "Choose an allowed root before browsing files.",
    webcam: "Check camera diagnostics before live capture.",
    activity: "Visible activity capture session, never a hidden keylogger.",
    power: "Request shutdown/restart/sleep with local confirmation.",
  };
  return copy[id] ?? "Remote operation surface.";
}

function moduleCommandTypes(moduleId: string): string[] {
  const map: Record<string, string[]> = {
    applications: ["app.list", "app.start", "app.stop"],
    processes: ["process.list", "process.kill", "app.stop"],
    screen: ["screen.screenshot", "screen.live.start", "screen.live.stop"],
    files: ["files.roots", "files.list", "files.download"],
    webcam: ["webcam.list", "webcam.snapshot", "webcam.live.start", "webcam.live.stop"],
    activity: ["activity.start", "activity.stop", "activity.export"],
    power: ["power.status", "power.shutdown", "power.restart", "power.sleep"],
  };
  return map[moduleId] ?? [];
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
}


function moduleCacheKey(agentId: string, moduleId: string): string {
  return `${agentId}:${moduleId}`;
}

function cacheModuleForCommand(commandType: string): string | null {
  if (commandType === "app.list") return "applications";
  if (commandType === "process.list") return "processes";
  if (commandType === "files.roots" || commandType === "files.list") return "files";
  if (commandType === "webcam.list" || commandType === "webcam.snapshot") return "webcam";
  if (commandType === "screen.screenshot") return "screen";
  if (["activity.start", "activity.stop", "activity.export"].includes(commandType)) return "activity";
  if (commandType.startsWith("power.")) return "power";
  return null;
}

function updateModuleResultCache(current: Record<string, Command>, commands: Command[]): Record<string, Command> {
  const next: Record<string, Command> = { ...current };
  for (const command of [...commands].reverse()) {
    if (!command.agent_id || command.status !== "succeeded") continue;
    const moduleId = cacheModuleForCommand(command.type);
    if (moduleId && command.result) {
      next[moduleCacheKey(command.agent_id, moduleId)] = command;
    }
    if (command.type === "app.stop" && command.result) {
      const appKey = String(command.result.app_key ?? command.payload?.app_key ?? "");
      const processNames = Array.isArray(command.result.process_names) ? command.result.process_names.map((name) => String(name).toLowerCase()) : [];
      if (appKey) {
        for (const targetModule of ["applications", "processes"]) {
          const key = moduleCacheKey(command.agent_id, targetModule);
          if (next[key]) next[key] = removeAppFromCachedCommand(next[key], appKey, processNames);
        }
      }
    }
    if (command.type === "process.kill" && command.result) {
      const pid = String(command.result.pid ?? command.payload?.pid ?? "");
      if (pid) {
        const key = moduleCacheKey(command.agent_id, "processes");
        if (next[key]) next[key] = removePidFromCachedCommand(next[key], pid);
      }
    }
  }
  return next;
}

function removeAppFromCachedCommand(command: Command, appKey: string, processNames: string[]): Command {
  if (!command.result) return command;
  const removeApp = (value: unknown) => asRecords(value).filter((item) => String(item.app_key) !== appKey);
  const removeProcesses = (value: unknown) => asRecords(value).filter((item) => !processNames.includes(String(item.name ?? "").toLowerCase()));
  return {
    ...command,
    result: {
      ...command.result,
      items: Array.isArray(command.result.items)
        ? (Array.isArray(command.result.apps) ? removeProcesses(command.result.items) : removeApp(command.result.items))
        : command.result.items,
      apps: Array.isArray(command.result.apps) ? removeApp(command.result.apps) : command.result.apps,
      count: Array.isArray(command.result.items)
        ? (Array.isArray(command.result.apps) ? removeProcesses(command.result.items).length : removeApp(command.result.items).length)
        : command.result.count,
      app_count: Array.isArray(command.result.apps) ? removeApp(command.result.apps).length : command.result.app_count,
    },
  };
}

function removePidFromCachedCommand(command: Command, pid: string): Command {
  if (!command.result) return command;
  const removePid = (value: unknown) => asRecords(value).filter((item) => String(item.pid) !== pid);
  return {
    ...command,
    result: {
      ...command.result,
      items: Array.isArray(command.result.items) ? removePid(command.result.items) : command.result.items,
      count: Array.isArray(command.result.items) ? removePid(command.result.items).length : command.result.count,
    },
  };
}

function downloadCommandResult(result: Record<string, unknown>): { ok: true; name: string } | { ok: false; error: string } {
  try {
    const data = String(result.data ?? "");
    const name = String(result.name ?? "remote-file.bin");
    if (!data) return { ok: false, error: "Download result did not include file data." };
    const binary = window.atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const blob = new Blob([bytes], { type: String(result.mime ?? "application/octet-stream") });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return { ok: true, name };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Browser download failed." };
  }
}

function downloadActivityExport(result: Record<string, unknown>, createdAt: string): { ok: true; name: string } | { ok: false; error: string } {
  try {
    const timestamp = new Date(createdAt).toISOString().replace(/[:.]/g, "-");
    const name = `remotectrl-activity-${timestamp}.json`;
    const payload = JSON.stringify({ exported_at: new Date().toISOString(), events: asRecords(result.events) }, null, 2);
    const url = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return { ok: true, name };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Browser download failed." };
  }
}
function agentName(agents: Agent[], agentId: string): string {
  return agents.find((agent) => agent.id === agentId)?.name ?? "Unknown agent";
}

function streamKind(value: unknown): StreamKind | null {
  return value === "screen" || value === "webcam" ? value : null;
}

function confirmAndRun(message: string, action: () => void): void {
  if (window.confirm(message)) action();
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}


function commandRequiresApproval(commandType: string): boolean {
  return new Set([
    "app.list",
    "app.start",
    "app.stop",
    "process.list",
    "process.kill",
    "files.roots",
    "files.list",
    "files.download",
    "screen.live.start",
    "screen.live.stop",
    "screen.screenshot",
    "webcam.list",
    "webcam.live.start",
    "webcam.live.stop",
    "webcam.snapshot",
    "activity.start",
    "activity.stop",
    "activity.export",
    "power.shutdown",
    "power.restart",
    "power.sleep",
    "power.status",
  ]).has(commandType);
}
function defaultPayload(commandType: string): Record<string, unknown> {
  if (commandType.endsWith("live.start")) return { fps: 10, quality: 65 };
  if (commandType === "screen.screenshot") return { quality: 75 };
  return {};
}

function demoResult(commandType: string, payload: Record<string, unknown> = {}): Record<string, unknown> {
  if (commandType === "process.list") {
    return {
      count: 47,
      app_count: 2,
      apps: [
        { app_key: "chrome", name: "Chrome", window_count: 2, process_names: ["chrome.exe"] },
        { app_key: "code", name: "Code", window_count: 1, process_names: ["Code.exe"] },
      ],
      items: [
        { pid: 1034, name: "chrome.exe", cpu: 2.4, memory_mb: 412, status: "running" },
        { pid: 2208, name: "Code.exe", cpu: 6.8, memory_mb: 865, status: "running" },
        { pid: 4320, name: "python.exe", cpu: 1.1, memory_mb: 155, status: "sleeping" },
      ],
    };
  }
  if (commandType === "app.list") {
    return { count: 2, items: [{ app_key: "chrome", name: "Chrome", window_count: 2, process_names: ["chrome.exe"] }, { app_key: "code", name: "Code", window_count: 1, process_names: ["Code.exe"] }] };
  }
  if (commandType === "app.start") return { status: payload.mode === "new_instance" ? "started_new" : "focused_existing", preset: payload.preset, mode: payload.mode };
  if (commandType === "files.roots") return { count: 2, requires_selection: true, roots: [{ name: "Documents", path: "C:\\Users\\demo\\Documents", exists: true, is_dir: true }, { name: "Data", path: "D:\\Data", exists: true, is_dir: true }] };
  if (commandType === "files.list") {
    return { path: String(payload.path ?? "D:\\Data"), allowed_root: String(payload.path ?? "D:\\Data"), entries: [{ name: "Reports", path: "D:\\Data\\Reports", is_dir: true, size: 0 }, { name: "report.docx", path: "D:\\Data\\report.docx", is_dir: false, size: 81234 }] };
  }
  if (commandType === "screen.screenshot") return { mime: "image/jpeg", image: "", width: 1920, height: 1080, status: "demo_screenshot_placeholder" };
  if (commandType === "webcam.list") return { capture_backend: "webview2", available: true, opencv_available: false, cv2_available: false, agent_packaged: true, count: 1, items: [{ index: 0, label: "Camera 0" }] };
  if (commandType === "power.status") return { action: "status", status: "ok", dry_run_power: true, cpu_percent: 23, system_uptime_seconds: 9300, battery_percent: 84, battery_plugged: true, supported_actions: ["shutdown", "restart", "sleep"] };
  if (commandType === "activity.export") return { mode: "visible_activity_session", events: [{ time: new Date().toISOString(), type: "active_window.changed", detail: { process: "Code.exe", title: "RemoteCtrl" } }] };
  return { status: "queued", approval_required: true };
}
