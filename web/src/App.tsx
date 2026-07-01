import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AppWindow,
  Camera,
  CheckCircle2,
  ChevronRight,
  FileDown,
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
  Search,
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
  { id: "screen", label: "Screen", icon: Monitor, command: "screen.live.start", safe: false },
  { id: "files", label: "Files", icon: FileDown, command: "files.list", safe: false },
  { id: "webcam", label: "Webcam", icon: Camera, command: "webcam.live.start", safe: false },
  { id: "keycapture", label: "Key Capture", icon: KeyRound, command: "keycapture.start", safe: false },
  { id: "power", label: "Power", icon: Power, command: "power.shutdown", safe: false },
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

const emptyStreamStats: StreamStats = { status: "idle", fps: 0, frames: 0, latencyMs: 0 };

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
  const [streamFrames, setStreamFrames] = useState<Record<StreamKind, StreamFrame>>({ screen: null, webcam: null });
  const [streamStats, setStreamStats] = useState<Record<StreamKind, StreamStats>>({
    screen: emptyStreamStats,
    webcam: emptyStreamStats,
  });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const selectedAgent = useMemo(() => agents.find((agent) => agent.id === selectedAgentId) ?? agents[0], [agents, selectedAgentId]);
  const keycaptureActive = useMemo(() => {
    const latestStateCommand = commands.find(
      (command) => command.agent_id === selectedAgent?.id && ["keycapture.start", "keycapture.stop"].includes(command.type) && command.status === "succeeded",
    );
    return latestStateCommand?.type === "keycapture.start";
  }, [commands, selectedAgent?.id]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const syncFullscreen = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);

  useEffect(() => {
    if (!token || demoMode) return;
    refresh(token);
    const ws = new WebSocket(dashboardWsUrl());
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "stream.frame" && message.frame) {
          const stream = streamKind(message.stream);
          if (!stream) return;
          setStreamFrames((frames) => ({ ...frames, [stream]: { mime: message.mime ?? "image/jpeg", frame: message.frame } }));
          setStreamStats((stats) => ({
            ...stats,
            [stream]: {
              ...stats[stream],
              status: "running",
              frames: stats[stream].frames + 1,
              latencyMs: typeof message.sent_at === "number" ? Math.max(0, Math.round(Date.now() - message.sent_at * 1000)) : stats[stream].latencyMs,
            },
          }));
          return;
        }
        if (message.type === "stream.status") {
          const stream = streamKind(message.stream);
          if (!stream) return;
          setStreamStats((stats) => ({
            ...stats,
            [stream]: {
              ...stats[stream],
              status: message.status ?? stats[stream].status,
              fps: Number(message.fps ?? stats[stream].fps),
              frames: message.status === "running" ? 0 : stats[stream].frames,
            },
          }));
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
      const selected = nextAgents.find((agent) => agent.id === selectedAgentId);
      const onlineAgent = nextAgents.find((agent) => agent.status === "online");
      setAgents(nextAgents);
      setCommands(data.commands);
      setAudit(data.audit);
      if (onlineAgent && (!selected || selected.status !== "online")) {
        setSelectedAgentId(onlineAgent.id);
      } else if (nextAgents[0] && !selected) {
        setSelectedAgentId(nextAgents[0].id);
      } else if (!nextAgents.length) {
        setSelectedAgentId("");
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
    setStreamFrames({ screen: null, webcam: null });
    setStreamStats({ screen: emptyStreamStats, webcam: emptyStreamStats });
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

  async function runCommand(commandType: string, payload: Record<string, unknown> = defaultPayload(commandType)) {
    if (!selectedAgent || !token) return;
    const startedStream = commandType === "screen.live.start" ? "screen" : commandType === "webcam.live.start" ? "webcam" : null;
    if (startedStream && ["starting", "running"].includes(streamStats[startedStream].status)) {
      setNotice(`${startedStream} stream is already ${streamStats[startedStream].status}. Stop it before starting again.`);
      return;
    }
    if (commandType === "keycapture.start" && keycaptureActive) {
      setNotice("Key Capture session is already running. Stop it before starting again.");
      return;
    }
    if (startedStream) {
      setStreamFrames((frames) => ({ ...frames, [startedStream]: null }));
      setStreamStats((stats) => ({ ...stats, [startedStream]: { ...emptyStreamStats, status: "starting", fps: Number(payload.fps ?? 10) } }));
    }
    if (demoMode) {
      const command: Command = {
        id: `demo-${Date.now()}`,
        agent_id: selectedAgent.id,
        type: commandType,
        payload,
        requires_approval: commandRequiresApproval(commandType),
        status: commandRequiresApproval(commandType) ? "pending_approval" : "succeeded",
        result: demoResult(commandType),
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
          detail: { type: commandType, demo: true },
          created_at: new Date().toISOString(),
        },
        ...items,
      ]);
      setNotice(`${commandType} queued in demo mode.`);
      return;
    }
    if (selectedAgent.status !== "online") {
      const onlineAgent = agents.find((agent) => agent.status === "online");
      if (onlineAgent) {
        setSelectedAgentId(onlineAgent.id);
        setNotice(`Selected agent was offline. Switched to online agent: ${onlineAgent.name}. Run the command again.`);
      } else {
        setNotice("No online agent is available. Connect the Windows agent before running commands.");
      }
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
  const latestModuleCommand = commands.find((command) => moduleCommandTypes(selectedModule).includes(command.type));

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
              <strong>No agents yet</strong>
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
          <div className="search">
            <Search size={18} />
            <input placeholder="Search commands, agents, audit events" />
          </div>
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
            <button className="primary" onClick={() => runCommand(activeModule.command)}>
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
              latestCommand={latestModuleCommand}
              keycaptureActive={keycaptureActive}
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
                <small>{command.created_by}</small>
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
  latestCommand,
  keycaptureActive,
}: {
  module: (typeof modules)[number];
  selectedAgent?: Agent;
  runCommand: (type: string, payload?: Record<string, unknown>) => void;
  refresh: () => void;
  streamFrames: Record<StreamKind, StreamFrame>;
  streamStats: Record<StreamKind, StreamStats>;
  latestCommand?: Command;
  keycaptureActive: boolean;
}) {
  const Icon = module.icon;
  const previewRef = useRef<HTMLDivElement | null>(null);
  const canFullscreenPreview = module.id === "screen" || module.id === "webcam";
  const isLiveModule = module.id === "screen" || module.id === "webcam";
  const isDataModule = ["applications", "processes", "files"].includes(module.id);
  const activeStream = isLiveModule ? (module.id as StreamKind) : null;
  const activeStats = activeStream ? streamStats[activeStream] : emptyStreamStats;
  const activeFrame = activeStream ? streamFrames[activeStream] : null;
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
      <div className={`module-preview ${isDataModule ? "data-layout" : ""}`}>
        <div className="module-actions">
          <div className="action-row">
            {renderControls(module.id, runCommand, liveRunning, liveActive, keycaptureActive)}
            <button className="secondary" onClick={() => refresh()}>Refresh audit trail</button>
          </div>
          {isLiveModule && (
            <div className="stream-stats">
              <span>{activeStats.status}</span>
              <span>{activeStats.fps || 10} FPS</span>
              <span>{activeStats.frames} frames</span>
              <span>{activeStats.latencyMs} ms</span>
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
          <ResultView moduleId={module.id} command={latestCommand} runCommand={runCommand} />
        </div>
      </div>
    </>
  );
}

function renderControls(moduleId: string, runCommand: (type: string, payload?: Record<string, unknown>) => void, liveRunning: boolean, liveActive: boolean, keycaptureActive: boolean) {
  if (moduleId === "screen" || moduleId === "webcam") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand(`${moduleId}.live.start`, { fps: 10, quality: 65 })} disabled={liveActive}>
          <Play size={16} /> Start Live
        </button>
        <button className="secondary" onClick={() => runCommand(`${moduleId}.live.stop`)} disabled={!liveRunning}>
          <Square size={16} /> Stop Live
        </button>
      </div>
    );
  }
  if (moduleId === "applications") {
    return (
      <div className="control-stack">
        <button className="primary" onClick={() => runCommand("app.list")}>Refresh Applications</button>
        <div className="preset-grid">
          {appPresets.map((preset) => (
            <button className="secondary compact" key={preset.id} onClick={() => runCommand("app.start", { preset: preset.id })}>
              {preset.label}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (moduleId === "processes") {
    return <button className="primary" onClick={() => runCommand("process.list")}>Refresh Processes</button>;
  }
  if (moduleId === "files") {
    return <button className="primary" onClick={() => runCommand("files.list", { path: "" })}>Browse Allowed Folder</button>;
  }
  if (moduleId === "power") {
    return (
      <div className="control-stack">
        <div className="danger-note">Dry-run by default. Real power actions require agent-side real mode and local approval.</div>
        {["shutdown", "restart", "logout"].map((action) => (
          <button
            className="secondary"
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
        <button className="primary" onClick={() => runCommand("keycapture.start")} disabled={keycaptureActive}>Start Visible Session</button>
        <button className="secondary" onClick={() => runCommand("keycapture.stop")} disabled={!keycaptureActive}>Stop Session</button>
        <button className="secondary" onClick={() => runCommand("keycapture.export")}>Export Text</button>
      </div>
    );
  }
  return <button className="primary" onClick={() => runCommand("process.list")}>Run</button>;
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
  if (moduleId === "files") return <FilesResult result={command.result} runCommand={runCommand} />;
  if (moduleId === "power") return <PowerResult result={command.result} />;
  if (moduleId === "keycapture") return <KeyCaptureResult result={command.result} />;
  return <DeveloperDetails result={command.result} />;
}

function ApplicationsResult({ result, runCommand }: { result: Record<string, unknown>; runCommand: (type: string, payload?: Record<string, unknown>) => void }) {
  const items = asRecords(result.items).slice(0, 12);
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
  const items = asRecords(result.items)
    .filter((item) => String(item.name ?? "").trim())
    .sort((left, right) => {
      const leftName = String(left.name ?? "").toLowerCase();
      const rightName = String(right.name ?? "").toLowerCase();
      const leftProtected = protectedProcessNames.has(leftName) ? 1 : 0;
      const rightProtected = protectedProcessNames.has(rightName) ? 1 : 0;
      if (leftProtected !== rightProtected) return leftProtected - rightProtected;
      return String(left.name ?? "").localeCompare(String(right.name ?? ""));
    })
    .slice(0, 40);
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>Processes</strong><span>{Number(result.count ?? items.length)} found</span></div>
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
  const entries = asRecords(result.entries).slice(0, 14);
  return (
    <div className="result-list">
      <div className="result-list-heading"><strong>{String(result.path ?? "Allowed folder")}</strong><span>{entries.length} entries</span></div>
      {entries.map((entry) => (
        <div className="result-row" key={String(entry.path ?? entry.name)}>
          <div className="row-main">
            <strong>{String(entry.name ?? "")}</strong>
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

function PowerResult({ result }: { result: Record<string, unknown> }) {
  return (
    <div className="state-card">
      <strong>{String(result.action ?? "power")}</strong>
      <span className={`status-pill ${result.status === "dry_run" ? "" : "succeeded"}`}>{String(result.status ?? "unknown")}</span>
      <p>{String(result.message ?? "Request sent to the agent.")}</p>
      <DeveloperDetails result={result} />
    </div>
  );
}

function KeyCaptureResult({ result }: { result: Record<string, unknown> }) {
  return (
    <div className="state-card">
      <strong>{String(result.status ?? result.mode ?? "Key capture")}</strong>
      {result.text ? <pre>{String(result.text)}</pre> : <p>{String(result.mode ?? "Visible session updated.")}</p>}
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
    applications: "Enumerate visible windows and app titles.",
    processes: "Inspect CPU, memory, PID, and process state.",
    screen: "Capture a screenshot after local consent.",
    files: "Browse configured allowed folders only.",
    webcam: "Capture camera media after explicit approval.",
    keycapture: "Open a visible typing session, never a hidden keylogger.",
    power: "Request shutdown/restart/logout with local confirmation.",
  };
  return copy[id] ?? "Remote operation surface.";
}

function moduleCommandTypes(moduleId: string): string[] {
  const map: Record<string, string[]> = {
    applications: ["app.list", "app.start", "app.stop"],
    processes: ["process.list", "process.kill"],
    screen: ["screen.screenshot", "screen.live.start", "screen.live.stop"],
    files: ["files.list", "files.download"],
    webcam: ["webcam.list", "webcam.snapshot", "webcam.live.start", "webcam.live.stop"],
    keycapture: ["keycapture.start", "keycapture.stop", "keycapture.export"],
    power: ["power.shutdown", "power.restart", "power.logout"],
  };
  return map[moduleId] ?? [];
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
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
    "files.list",
    "files.download",
    "screen.live.start",
    "screen.screenshot",
    "webcam.live.start",
    "webcam.snapshot",
    "keycapture.start",
    "keycapture.export",
    "power.shutdown",
    "power.restart",
    "power.logout",
  ]).has(commandType);
}
function defaultPayload(commandType: string): Record<string, unknown> {
  if (commandType === "files.list") return { path: "" };
  if (commandType.endsWith("live.start")) return { fps: 10, quality: 65 };
  return {};
}

function demoResult(commandType: string): Record<string, unknown> {
  if (commandType === "process.list") {
    return {
      count: 47,
      items: [
        { pid: 1034, name: "chrome.exe", cpu: 2.4, memory_mb: 412 },
        { pid: 2208, name: "Code.exe", cpu: 6.8, memory_mb: 865 },
      ],
    };
  }
  if (commandType === "app.list") {
    return {
      count: 3,
      items: [
        { pid: 1034, name: "Chrome", title: "RemoteCtrl Dashboard" },
        { pid: 2208, name: "VSCode", title: "D:\\Project\\MMT" },
      ],
    };
  }
  if (commandType === "files.list") {
    return {
      path: "C:\\Users\\demo",
      entries: [
        { name: "Documents", is_dir: true, size: 0 },
        { name: "report.docx", is_dir: false, size: 81234 },
      ],
    };
  }
  return { status: "queued", approval_required: true };
}
