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
import { ApiError, createCommand, createEnrollmentToken, dashboardWsUrl, deleteAgent, deleteOfflineAgents, loadDashboard, login } from "./lib/api";
import { mockAgents, mockAudit, mockCommands } from "./lib/mock";
import type { Agent, AuditEvent, Command } from "./lib/types";

const modules = [
  { id: "applications", label: "Applications", icon: AppWindow, command: "app.list", safe: false },
  { id: "processes", label: "Processes", icon: Activity, command: "process.list", safe: false },
  { id: "screen", label: "Screen", icon: Monitor, command: "screen.screenshot", safe: false },
  { id: "files", label: "Files", icon: FileDown, command: "files.roots", safe: false },
  { id: "webcam", label: "Webcam", icon: Camera, command: "webcam.list", safe: false },
  { id: "keycapture", label: "Activity Capture", icon: KeyRound, command: "activity.start", safe: false },
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
type AgentSessionState = { screen: boolean; webcam: boolean; activity: boolean; keycapture: boolean };
type AgentSessionStateMap = Record<string, AgentSessionState>;
type AppStartMode = "focus_existing" | "new_instance";

const emptyStreamStats: StreamStats = { status: "idle", fps: 0, frames: 0, latencyMs: 0 };

function streamStateKey(agentId: string, stream: StreamKind): string {
  return `${agentId || "unknown"}:${stream}`;
}

export function App() {
  const [theme, setTheme] = useState<Theme>("light");
  const [token, setToken] = useState<string>(() => localStorage.getItem("rt_token") ?? "");
  const [demoMode, setDemoMode] = useState<boolean>(() => localStorage.getItem("rt_demo") === "true");
  const [agents, setAgents] = useState<Agent[]>(() => (localStorage.getItem("rt_demo") === "true" ? mockAgents : []));
  const [commands, setCommands] = useState<Command[]>(() => (localStorage.getItem("rt_demo") === "true" ? mockCommands : []));
  const [audit, setAudit] = useState<AuditEvent[]>(() => (localStorage.getItem("rt_demo") === "true" ? mockAudit : []));
  const [selectedAgentId, setSelectedAgentId] = useState(() => (localStorage.getItem("rt_demo") === "true" ? mockAgents[0].id : ""));
  const [selectedModule, setSelectedModule] = useState(modules[0].id);
  const [loginError, setLoginError] = useState("");
  const [notice, setNotice] = useState("Demo data is loaded until the gateway responds.");
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
  const downloadEffectsReady = useRef(false);
  const keycaptureActive = Boolean(selectedAgent && agentSessionStates[selectedAgent.id]?.activity);

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
    refresh(token);
    const ws = new WebSocket(dashboardWsUrl());
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "agent.session_snapshot" && message.sessions && typeof message.sessions === "object") {
          const snapshot = message.sessions as Record<string, Record<string, unknown>>;
          const next: AgentSessionStateMap = {};
          for (const [agentId, sessions] of Object.entries(snapshot)) next[agentId] = { screen: Boolean(sessions.screen), webcam: Boolean(sessions.webcam), activity: Boolean(sessions.activity), keycapture: Boolean(sessions.keycapture) };
          setAgentSessionStates(next);
          return;
        }
        if (message.type === "agent.session_state" && message.sessions && typeof message.sessions === "object") {
          const agentId = String(message.agent_id ?? "");
          if (!agentId) return;
          const source = message.sessions as Record<string, unknown>;
          setAgentSessionStates((current) => ({
            ...current,
            [agentId]: { screen: Boolean(source.screen), webcam: Boolean(source.webcam), activity: Boolean(source.activity), keycapture: Boolean(source.keycapture) },
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
    return () => ws.close();
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
      setAgents(mockAgents);
      setCommands(mockCommands);
      setAudit(mockAudit);
      setNotice("Gateway unavailable. Showing premium demo data.");
    }
  }

  function signOut() {
    localStorage.removeItem("rt_token");
    localStorage.removeItem("rt_demo");
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
      localStorage.setItem("rt_token", nextToken);
      localStorage.removeItem("rt_demo");
      setDemoMode(false);
      setToken(nextToken);
      setLoginError("");
      await refresh(nextToken);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Login failed");
    }
  }

  function enterDemoMode() {
    localStorage.setItem("rt_demo", "true");
    localStorage.setItem("rt_token", "demo");
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
    if (!selectedAgent || !token) return;
    const startedStream = commandType === "screen.live.start" ? "screen" : commandType === "webcam.live.start" ? "webcam" : null;
    const startedStreamKey = startedStream && selectedAgent ? streamStateKey(selectedAgent.id, startedStream) : null;
    const startedStreamStats = startedStreamKey ? streamStats[startedStreamKey] ?? emptyStreamStats : emptyStreamStats;
    if (startedStream && ["starting", "running"].includes(startedStreamStats.status)) {
      setNotice(`${startedStream} stream is already ${startedStreamStats.status}. Stop it before starting again.`);
      return;
    }
    if ((commandType === "activity.start" || commandType === "keycapture.start") && keycaptureActive) {
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
    try {
      const command = await createCommand(token, selectedAgent.id, commandType, payload);
      setCommands((items) => [command, ...items]);
      setNotice(`${commandType} sent to ${selectedAgent.name}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Command failed.");
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
  const latestModuleCommand = selectedAgent
    ? moduleResultCache[moduleCacheKey(selectedAgent.id, selectedModule)]
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
              <button className="agent-pick" onClick={() => setSelectedAgentId(agent.id)}>
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
              keycaptureActive={keycaptureActive}
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
        <label>Email<input name="email" defaultValue="admin@remotectrl.local" /></label>
        <label>Password<input name="password" type="password" defaultValue="admin12345" /></label>
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
  keycaptureActive,
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
  keycaptureActive: boolean;
  appStartMode: AppStartMode;
  setAppStartMode: (mode: AppStartMode) => void;
  commandDisabled: boolean;
}) {
  const Icon = module.icon;
  const previewRef = useRef<HTMLDivElement | null>(null);
  const canFullscreenPreview = module.id === "screen" || module.id === "webcam";
  const isLiveModule = module.id === "screen" || module.id === "webcam";
  const isDataModule = ["applications", "processes", "files", "keycapture"].includes(module.id);
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
            {renderControls(module.id, runCommand, liveRunning, liveActive, keycaptureActive, appStartMode, setAppStartMode, commandDisabled, latestCommand)}
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
          {module.id === "keycapture" && <LiveActivityFeed events={activityEvents} />}
          <ResultView moduleId={module.id} command={latestCommand} runCommand={runCommand} />
        </div>
      </div>
    </>
  );
}

function renderControls(moduleId: string, runCommand: (type: string, payload?: Record<string, unknown>) => void, liveRunning: boolean, liveActive: boolean, keycaptureActive: boolean, appStartMode: AppStartMode, setAppStartMode: (mode: AppStartMode) => void, commandDisabled: boolean, latestCommand?: Command) {
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
  if (moduleId === "keycapture") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand("activity.start")} disabled={commandDisabled || keycaptureActive}>Start Activity Session</button>
        <button className="secondary" onClick={() => runCommand("activity.stop")} disabled={commandDisabled || !keycaptureActive}>Stop Session</button>
        <button className="secondary" onClick={() => runCommand("activity.export")} disabled={commandDisabled}>Export Activity</button>
      </div>
    );
  }
  return <button className="primary" onClick={() => runCommand("process.list")} disabled={commandDisabled}>Run</button>;
}

function ResultView({ moduleId, command, runCommand }: { moduleId: string; command?: Command; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
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
  if (moduleId === "files") return <FilesResult result={command.result} runCommand={runCommand} />;
  if (moduleId === "screen" || moduleId === "webcam") return <MediaResult command={command} />;
  if (moduleId === "power") return <PowerResult result={command.result} />;
  if (moduleId === "keycapture" && command.type === "activity.export") return <div className="state-card"><strong>Activity export downloaded</strong><p>The current session log was saved to the browser download folder.</p></div>;
  if (moduleId === "keycapture") return <KeyCaptureResult result={command.result} />;
  return <DeveloperDetails result={command.result} />;
}

function ApplicationsResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const items = asRecords(result.items).slice(0, 20);
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>Visible windows</strong><span>{Number(result.count ?? items.length)} found</span></div>
      {items.map((item) => (
        <div className="result-row" key={`${item.pid}-${item.title}`}>
          <div className="row-main">
            <strong>{String(item.title || item.name || "Untitled window")}</strong>
            <div className="row-meta">
              <span>{String(item.name ?? "unknown")}</span>
              <span>PID {String(item.pid ?? "-")}</span>
            </div>
          </div>
          <button className="row-action" onClick={() => confirmAndRun("Stop this app window?", () => runCommand("app.stop", { pid: item.pid }))}>Stop</button>
        </div>
      ))}
      <DeveloperDetails result={result} />
    </div>
  );
}

function ProcessesResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const apps = asRecords(result.apps).slice(0, 20);
  const appPids = new Set(apps.map((item) => String(item.pid)));
  const items = asRecords(result.items)
    .filter((item) => String(item.name ?? "").trim() && !appPids.has(String(item.pid)))
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
      {apps.map((item) => (
        <div className="result-row app-row" key={`app-${item.pid}-${item.title}`}>
          <div className="row-main">
            <strong>{String(item.title || item.name || "Untitled app")}</strong>
            <div className="row-meta"><span>{String(item.name ?? "unknown")}</span><span>PID {String(item.pid ?? "-")}</span></div>
          </div>
          <button className="row-action" onClick={() => confirmAndRun("Stop this app window?", () => runCommand("app.stop", { pid: item.pid }))}>Stop</button>
        </div>
      ))}
      {!apps.length && <div className="empty-result">No visible app windows match.</div>}
      <div className="result-list-heading secondary-heading"><strong>Background processes</strong><span>{items.length}/{Number(result.count ?? items.length)} shown</span></div>
      {items.map((item) => {
        const name = String(item.name ?? "").toLowerCase();
        const protectedProcess = protectedProcessNames.has(name);
        return (
          <div className={`result-row ${protectedProcess ? "guarded-row" : ""}`} key={`${item.pid}-${item.name}`}>
            <div className="row-main">
              <strong>{String(item.name ?? "Unknown process")}</strong>
              <div className="row-meta">
                <span>PID {String(item.pid ?? "-")}</span>
                <span>{Number(item.memory_mb ?? 0).toFixed(1)} MB RAM</span>
                <span>{String(item.status ?? "unknown")}</span>
              </div>
            </div>
            <button
              className="row-action danger"
              disabled={protectedProcess}
              onClick={() => confirmAndRun(`Kill process ${item.name} (${item.pid})?`, () => runCommand("process.kill", { pid: item.pid }))}
            >
              {protectedProcess ? "Guarded" : "Kill"}
            </button>
          </div>
        );
      })}
      <DeveloperDetails result={result} />
    </div>
  );
}

function FilesResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
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
        {roots.map((root) => (
          <div className="result-row" key={String(root.path)}>
            <div className="row-main">
              <strong className="entry-name"><Folder size={17} /> {String(root.name || root.path)}</strong>
              <div className="row-meta"><span>{root.exists ? "Available" : "Missing"}</span><span>{String(root.path)}</span></div>
            </div>
            <button className="row-action" disabled={!root.exists || !root.is_dir} onClick={() => runCommand("files.list", { path: root.path })}>Open</button>
          </div>
        ))}
        <DeveloperDetails result={result} />
      </div>
    );
  }
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>{String(result.path ?? "Allowed folder")}</strong><span>{entries.length} entries</span></div>
      {typeof result.path === "string" && <FileBreadcrumb path={result.path} rootPath={typeof result.allowed_root === "string" ? result.allowed_root : undefined} runCommand={runCommand} />}
      {entries.map((entry) => (
        <div className="result-row" key={String(entry.path ?? entry.name)}>
          <div className="row-main">
            <strong className="entry-name">{entry.is_dir ? <Folder size={17} /> : <FileIcon size={17} />} {String(entry.name ?? "")}</strong>
            <div className="row-meta">
              <span>{entry.is_dir ? "Folder" : "File"}</span>
              <span>{formatBytes(Number(entry.size ?? 0))}</span>
              <span>{String(entry.path ?? "")}</span>
            </div>
          </div>
          {entry.is_dir ? (
            <button className="row-action" onClick={() => runCommand("files.list", { path: entry.path })}>Open</button>
          ) : (
            <button className="row-action" onClick={() => runCommand("files.download", { path: entry.path })}>Download</button>
          )}
        </div>
      ))}
      <DeveloperDetails result={result} />
    </div>
  );
}


function FileBreadcrumb({ path, rootPath, runCommand }: { path: string; rootPath?: string; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const parts = buildPathBreadcrumb(path, rootPath);
  if (!parts.length) return null;
  return (
    <div className="file-breadcrumb" aria-label="Current directory">
      {parts.map((part, index) => (
        <button key={`${part.path}-${index}`} onClick={() => index === 0 ? runCommand("files.roots") : runCommand("files.list", { path: part.path })} title={index === 0 ? "Return to allowed folders" : "Open this folder"}>
          {part.label}
        </button>
      ))}
    </div>
  );
}

function buildPathBreadcrumb(rawPath: string, rootPath?: string): { label: string; path: string }[] {
  const normalized = normalizeWindowsPath(rawPath);
  const normalizedRoot = rootPath ? normalizeWindowsPath(rootPath) : normalized;
  if (!normalizedRoot || !isPathInsideRoot(normalized, normalizedRoot)) return [{ label: "Allowed folders", path: normalized }];
  const rootParts = splitWindowsPath(normalizedRoot);
  const currentParts = splitWindowsPath(normalized);
  const rootLabel = rootParts[rootParts.length - 1] || normalizedRoot;
  const parts: { label: string; path: string }[] = [{ label: "Allowed folders", path: normalizedRoot }, { label: rootLabel, path: normalizedRoot }];
  for (let index = rootParts.length; index < currentParts.length; index += 1) {
    parts.push({ label: currentParts[index], path: joinWindowsParts(currentParts.slice(0, index + 1)) });
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

function PowerResult({ result }: { result: Record<string, unknown> }) {
  const isStatus = result.action === "status";
  if (isStatus) {
    return (
      <div className="state-card">
        {Boolean(result.dry_run_power) && <div className="danger-note">Dry-run mode active. Enable real power actions on the Agent before shutdown/restart/sleep can execute.</div>}
        <div className="power-grid">
          <PowerMetric tone="temp" label="CPU usage" value={formatNullable(result.cpu_percent, "%")} />
          <PowerMetric tone="uptime" label="System uptime" value={formatDuration(Number(result.system_uptime_seconds ?? 0))} />
          <PowerMetric tone="battery" label="Battery" value={formatBattery(result.battery_percent, result.battery_plugged)} />
        </div>
        <DeveloperDetails result={result} />
      </div>
    );
  }
  return (
    <div className="state-card">
      <strong>{String(result.action ?? "power")}</strong>
      <span className={`status-pill ${result.status === "dry_run" ? "" : "succeeded"}`}>{String(result.status ?? "unknown")}</span>
      <p>{String(result.message ?? "Request sent to the agent.")}</p>
      <DeveloperDetails result={result} />
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
function KeyCaptureResult({ result }: { result: Record<string, unknown> }) {
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
    keycapture: "Visible activity capture session, never a hidden keylogger.",
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
    keycapture: ["activity.start", "activity.stop", "activity.export", "keycapture.start", "keycapture.stop", "keycapture.export"],
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
  if (["activity.start", "activity.stop", "activity.export", "keycapture.start", "keycapture.stop", "keycapture.export"].includes(commandType)) return "keycapture";
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
    if ((command.type === "app.stop" || command.type === "process.kill") && command.result) {
      const pid = String(command.result.pid ?? command.payload?.pid ?? "");
      if (pid) {
        for (const targetModule of ["applications", "processes"]) {
          const key = moduleCacheKey(command.agent_id, targetModule);
          if (next[key]) next[key] = removePidFromCachedCommand(next[key], pid);
        }
      }
    }
  }
  return next;
}

function removePidFromCachedCommand(command: Command, pid: string): Command {
  if (!command.result) return command;
  const removePid = (value: unknown) => asRecords(value).filter((item) => String(item.pid) !== pid);
  return {
    ...command,
    result: {
      ...command.result,
      items: Array.isArray(command.result.items) ? removePid(command.result.items) : command.result.items,
      apps: Array.isArray(command.result.apps) ? removePid(command.result.apps) : command.result.apps,
      count: Array.isArray(command.result.items) ? removePid(command.result.items).length : command.result.count,
      app_count: Array.isArray(command.result.apps) ? removePid(command.result.apps).length : command.result.app_count,
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
    "keycapture.start",
    "keycapture.stop",
    "keycapture.export",
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
        { pid: 1034, name: "Chrome", title: "RemoteCtrl Dashboard" },
        { pid: 2208, name: "Code", title: "D:\\Project\\MMT" },
      ],
      items: [
        { pid: 1034, name: "chrome.exe", cpu: 2.4, memory_mb: 412, status: "running" },
        { pid: 2208, name: "Code.exe", cpu: 6.8, memory_mb: 865, status: "running" },
        { pid: 4320, name: "python.exe", cpu: 1.1, memory_mb: 155, status: "sleeping" },
      ],
    };
  }
  if (commandType === "app.list") {
    return { count: 2, items: [{ pid: 1034, name: "Chrome", title: "RemoteCtrl Dashboard" }, { pid: 2208, name: "VSCode", title: "D:\\Project\\MMT" }] };
  }
  if (commandType === "app.start") return { status: payload.mode === "new_instance" ? "started_new" : "focused_existing", preset: payload.preset, mode: payload.mode };
  if (commandType === "files.roots") return { count: 2, requires_selection: true, roots: [{ name: "Documents", path: "C:\\Users\\demo\\Documents", exists: true, is_dir: true }, { name: "Data", path: "D:\\Data", exists: true, is_dir: true }] };
  if (commandType === "files.list") {
    return { path: String(payload.path ?? "D:\\Data"), entries: [{ name: "Reports", path: "D:\\Data\\Reports", is_dir: true, size: 0 }, { name: "report.docx", path: "D:\\Data\\report.docx", is_dir: false, size: 81234 }] };
  }
  if (commandType === "screen.screenshot") return { mime: "image/jpeg", image: "", width: 1920, height: 1080, status: "demo_screenshot_placeholder" };
  if (commandType === "webcam.list") return { capture_backend: "webview2", available: true, opencv_available: false, cv2_available: false, agent_packaged: true, count: 1, items: [{ index: 0, label: "Camera 0" }] };
  if (commandType === "power.status") return { action: "status", status: "ok", dry_run_power: true, cpu_percent: 23, system_uptime_seconds: 9300, battery_percent: 84, battery_plugged: true, supported_actions: ["shutdown", "restart", "sleep"] };
  if (commandType === "activity.export") return { mode: "visible_activity_session", events: [{ time: new Date().toISOString(), type: "active_window.changed", detail: { process: "Code.exe", title: "RemoteCtrl" } }] };
  return { status: "queued", approval_required: true };
}
