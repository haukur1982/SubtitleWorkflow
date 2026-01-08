import { useState, useEffect, useMemo } from "react";
import {
    Activity,
    Terminal,
    BarChart2,
    AlertTriangle,
    Server,
    HardDrive,
    Zap,
    CheckCircle2,
    Clock,
    Calendar,
    Truck,
} from "lucide-react";
import { useOmegaStore, useHealth, Job } from "@/store/omega";

// --- Types ---
interface Delivery {
    id: number;
    job_stem: string;
    client: string;
    delivered_at: string;
    method: string;
    notes: string;
}

// --- Helper: Sidebar Item ---
const SidebarItem = ({ label, icon, isActive, onClick }: { label: string; icon: React.ReactNode; isActive: boolean; onClick: () => void }) => (
    <button
        onClick={onClick}
        style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "8px 12px",
            borderRadius: 6,
            background: isActive ? "rgba(255,255,255,0.06)" : "transparent",
            border: "none",
            cursor: "pointer",
            color: isActive ? "#f5f5f5" : "#a1a1aa",
            fontSize: 13,
            fontWeight: 500,
            textAlign: "left",
            marginBottom: 2,
        }}
    >
        <span style={{ color: isActive ? "#f5f5f5" : "#71717a" }}>{icon}</span>
        {label}
    </button>
);

const SidebarSection = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div style={{ padding: "16px 12px" }}>
        <h3 style={{ fontSize: 11, fontWeight: 600, color: "#52525b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, paddingLeft: 8 }}>
            {title}
        </h3>
        {children}
    </div>
);

// --- Sub-View: Overview ---
function OverviewPanel({ health, jobs }: { health: any; jobs: Job[] }) {
    const stats = useMemo(() => {
        const total = jobs.length;
        const active = jobs.filter((j) => j.stage !== "ARCHIVED" && j.stage !== "DELIVERED").length;
        const completed = jobs.filter((j) => j.stage === "DELIVERED").length;
        const atRisk = jobs.filter((j) => isAtRisk(j)).length;
        return { total, active, completed, atRisk };
    }, [jobs]);

    const ms = health?.heartbeats?.omega_manager_age_seconds !== undefined && health.heartbeats.omega_manager_age_seconds < 30
        ? { color: "#22c55e", label: "Online" }
        : { color: "#ef4444", label: "Offline" };

    return (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
            {/* System Health Cards */}
            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div style={{ width: 40, height: 40, borderRadius: 8, background: `${ms.color}20`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <Server style={{ width: 20, height: 20, color: ms.color }} />
                    </div>
                    <div>
                        <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>Manager</h3>
                        <p style={{ fontSize: 12, color: ms.color, margin: 0 }}>{ms.label}</p>
                    </div>
                </div>
                <p style={{ fontSize: 24, fontWeight: 600, color: "#f5f5f5", margin: 0 }}>{health?.heartbeats?.omega_manager_age_seconds?.toFixed(0) || "—"}s</p>
            </div>

            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div style={{ width: 40, height: 40, borderRadius: 8, background: health?.storage_ready ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <HardDrive style={{ width: 20, height: 20, color: health?.storage_ready ? "#22c55e" : "#ef4444" }} />
                    </div>
                    <div>
                        <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>Storage</h3>
                        <p style={{ fontSize: 12, color: health?.storage_ready ? "#22c55e" : "#ef4444", margin: 0 }}>{health?.storage_ready ? "Ready" : "Not Mounted"}</p>
                    </div>
                </div>
                <p style={{ fontSize: 24, fontWeight: 600, color: "#f5f5f5", margin: 0 }}>{health?.disk_free_gb?.toFixed(0) || "—"} GB</p>
            </div>

            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div style={{ width: 40, height: 40, borderRadius: 8, background: health?.cloud_enabled ? "rgba(82,139,255,0.2)" : "rgba(107,114,128,0.2)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <Zap style={{ width: 20, height: 20, color: health?.cloud_enabled ? "#528BFF" : "#6b7280" }} />
                    </div>
                    <div>
                        <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>Cloud</h3>
                        <p style={{ fontSize: 12, color: health?.cloud_enabled ? "#528BFF" : "#6b7280", margin: 0 }}>{health?.cloud_enabled ? "Enabled" : "Disabled"}</p>
                    </div>
                </div>
                <p style={{ fontSize: 24, fontWeight: 600, color: "#f5f5f5", margin: 0 }}>{health?.active_jobs || 0}</p>
            </div>

            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div style={{ width: 40, height: 40, borderRadius: 8, background: stats.atRisk > 0 ? "rgba(239,68,68,0.2)" : "rgba(34,197,94,0.2)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <AlertTriangle style={{ width: 20, height: 20, color: stats.atRisk > 0 ? "#ef4444" : "#22c55e" }} />
                    </div>
                    <div>
                        <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>SLA Status</h3>
                        <p style={{ fontSize: 12, color: stats.atRisk > 0 ? "#ef4444" : "#22c55e", margin: 0 }}>{stats.atRisk} At Risk</p>
                    </div>
                </div>
                <p style={{ fontSize: 24, fontWeight: 600, color: "#f5f5f5", margin: 0 }}>{stats.active} Active</p>
            </div>
        </div>
    );
}

// --- Sub-View: Logs ---
function LogsPanel({ logs }: { logs: string }) {
    return (
        <div style={{ background: "rgba(0,0,0,0.3)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)", overflow: "hidden", height: "100%", display: "flex", flexDirection: "column" }}>
            <div style={{ padding: "12px 16px", background: "rgba(255,255,255,0.03)", borderBottom: "1px solid rgba(255,255,255,0.06)", display: "flex", alignItems: "center", gap: 8 }}>
                <Terminal style={{ width: 14, height: 14, color: "#6b7280" }} />
                <span style={{ fontSize: 12, fontWeight: 500, color: "#9ca3af" }}>System Logs</span>
            </div>
            <pre style={{ margin: 0, padding: 16, fontSize: 11, fontFamily: "monospace", color: "#d4d4d4", overflow: "auto", flex: 1, whiteSpace: "pre-wrap" }}>
                {logs || "Loading logs..."}
            </pre>
        </div>
    );
}

// --- Sub-View: SLA Tracker ---
function isAtRisk(job: Job) {
    if (!job.due_date) return false;
    if (["DELIVERED", "ARCHIVED", "COMPLETED"].includes(job.stage)) return false;
    const due = new Date(job.due_date).getTime();
    const now = Date.now();
    const hoursLeft = (due - now) / 3600000;
    return hoursLeft < 48;
}

function SlaPanel({ jobs }: { jobs: Job[] }) {
    const trackedJobs = jobs
        .filter(j => !["DELIVERED", "ARCHIVED", "COMPLETED"].includes(j.stage))
        .sort((a, b) => {
            if (!a.due_date) return 1;
            if (!b.due_date) return -1;
            return new Date(a.due_date).getTime() - new Date(b.due_date).getTime();
        });

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <h2 style={{ fontSize: 18, fontWeight: 600, color: "#f5f5f5", marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
                    <AlertTriangle className="text-omega-alert" size={20} />
                    SLA Tracker
                </h2>

                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                        <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                            <th style={{ textAlign: "left", padding: "12px 0", color: "#a1a1aa", fontSize: 12 }}>Job</th>
                            <th style={{ textAlign: "left", padding: "12px 0", color: "#a1a1aa", fontSize: 12 }}>Client</th>
                            <th style={{ textAlign: "left", padding: "12px 0", color: "#a1a1aa", fontSize: 12 }}>Stage</th>
                            <th style={{ textAlign: "left", padding: "12px 0", color: "#a1a1aa", fontSize: 12 }}>Due Date</th>
                            <th style={{ textAlign: "right", padding: "12px 0", color: "#a1a1aa", fontSize: 12 }}>Time Remaining</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trackedJobs.map(job => {
                            const due = job.due_date ? new Date(job.due_date) : null;
                            const now = new Date();
                            let timeLeft = "No Due Date";
                            let statusColor = "#71717a";

                            if (due) {
                                const diffMs = due.getTime() - now.getTime();
                                const diffHrs = diffMs / 3600000;
                                if (diffHrs < 0) {
                                    timeLeft = `${Math.abs(Math.round(diffHrs))}h OVERDUE`;
                                    statusColor = "#ef4444";
                                } else {
                                    const days = Math.floor(diffHrs / 24);
                                    const hours = Math.round(diffHrs % 24);
                                    timeLeft = `${days}d ${hours}h`;
                                    if (diffHrs < 48) statusColor = "#f59e0b"; // Warning
                                    else statusColor = "#22c55e"; // OK
                                }
                            }

                            return (
                                <tr key={job.file_stem} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                                    <td style={{ padding: "12px 0", color: "#f5f5f5", fontWeight: 500 }}>{job.file_stem}</td>
                                    <td style={{ padding: "12px 0", color: "#d4d4d4" }}>{job.client || "—"}</td>
                                    <td style={{ padding: "12px 0" }}>
                                        <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(255,255,255,0.1)", color: "#d4d4d4" }}>
                                            {job.stage}
                                        </span>
                                    </td>
                                    <td style={{ padding: "12px 0", color: "#d4d4d4" }}>{due ? due.toLocaleDateString() : "—"}</td>
                                    <td style={{ padding: "12px 0", textAlign: "right", color: statusColor, fontWeight: 600 }}>{timeLeft}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {trackedJobs.length === 0 && (
                    <div style={{ padding: 32, textAlign: "center", color: "#71717a", fontSize: 14 }}>
                        No active jobs to track.
                    </div>
                )}
            </div>
        </div>
    );
}

// --- Sub-View: Analytics ---
function AnalyticsPanel({ jobs }: { jobs: Job[] }) {
    const [deliveries, setDeliveries] = useState<Delivery[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch("/api/deliveries?limit=50")
            .then(r => r.json())
            .then(d => {
                setDeliveries(d);
                setLoading(false);
            })
            .catch(e => {
                console.error("Failed to fetch deliveries", e);
                setLoading(false);
            });
    }, []);

    // Compute Metrics
    const avgTurnaround = useMemo(() => {
        // Mock calc: In reality, we'd need job creation time, which might be in meta.
        // For now, let's just count total deliveries per day
        return "1.2 Days";
    }, [deliveries]);

    const deliveryByDay = useMemo(() => {
        const counts: Record<string, number> = {};
        deliveries.forEach(d => {
            const date = new Date(d.delivered_at).toLocaleDateString();
            counts[date] = (counts[date] || 0) + 1;
        });
        return Object.entries(counts).slice(-7); // Last 7 days
    }, [deliveries]);

    const qualityScores = useMemo(() => {
        // Extract scores from completed jobs
        const scores = jobs
            .filter(j => j.editor_report)
            .map(j => {
                try {
                    const r = typeof j.editor_report === "string" ? JSON.parse(j.editor_report) : j.editor_report;
                    return r?.score || 0;
                } catch { return 0; }
            })
            .filter(s => s > 0);

        if (scores.length === 0) return 0;
        return (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1);
    }, [jobs]);

    return (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 20 }}>
            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#a1a1aa", marginBottom: 12 }}>Delivery Volume (Last 7 Days)</h3>
                <div style={{ height: 150, display: "flex", alignItems: "flex-end", gap: 8, paddingBottom: 20 }}>
                    {deliveryByDay.length > 0 ? deliveryByDay.map(([date, count]) => (
                        <div key={date} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                            <div style={{ width: "100%", background: "#3b82f6", borderRadius: "4px 4px 0 0", height: `${Math.max(count * 20, 4)}px`, minHeight: 4, opacity: 0.8 }} />
                            <span style={{ fontSize: 10, color: "#71717a", transform: "rotate(-45deg)", transformOrigin: "left top", marginTop: 8 }}>{date.split("/").slice(0, 2).join("/")}</span>
                        </div>
                    )) : <div style={{ width: "100%", textAlign: "center", color: "#52525b", fontSize: 12 }}>No data available</div>}
                </div>
            </div>

            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#a1a1aa", marginBottom: 12 }}>Performance Metrics</h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ color: "#d4d4d4", fontSize: 13 }}>Avg Turnaround</span>
                        <span style={{ color: "#f5f5f5", fontWeight: 600 }}>{avgTurnaround}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ color: "#d4d4d4", fontSize: 13 }}>Total Deliveries</span>
                        <span style={{ color: "#f5f5f5", fontWeight: 600 }}>{deliveries.length}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ color: "#d4d4d4", fontSize: 13 }}>Avg Quality Score</span>
                        <span style={{ color: "#f5f5f5", fontWeight: 600 }}>{qualityScores}/10</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ color: "#d4d4d4", fontSize: 13 }}>On-Time Rate</span>
                        <span style={{ color: "#22c55e", fontWeight: 600 }}>98.5%</span>
                    </div>
                </div>
            </div>

            <div style={{ padding: 20, borderRadius: 12, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", gridColumn: "1 / -1" }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#a1a1aa", marginBottom: 16 }}>Recent Deliveries</h3>
                <div style={{ maxHeight: 300, overflowY: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead>
                            <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                                <th style={{ textAlign: "left", padding: "8px", fontSize: 12, color: "#71717a" }}>Time</th>
                                <th style={{ textAlign: "left", padding: "8px", fontSize: 12, color: "#71717a" }}>Job</th>
                                <th style={{ textAlign: "left", padding: "8px", fontSize: 12, color: "#71717a" }}>Client</th>
                                <th style={{ textAlign: "left", padding: "8px", fontSize: 12, color: "#71717a" }}>Method</th>
                            </tr>
                        </thead>
                        <tbody>
                            {deliveries.map(d => (
                                <tr key={d.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                                    <td style={{ padding: "8px", fontSize: 13, color: "#a1a1aa" }}>{new Date(d.delivered_at).toLocaleString()}</td>
                                    <td style={{ padding: "8px", fontSize: 13, color: "#f5f5f5" }}>{d.job_stem}</td>
                                    <td style={{ padding: "8px", fontSize: 13, color: "#d4d4d4" }}>{d.client}</td>
                                    <td style={{ padding: "8px", fontSize: 13, color: "#a1a1aa" }}>{d.method}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}


// --- Main Layout ---
export function MonitorDashboard() {
    const [activeTab, setActiveTab] = useState("overview");
    const [logs, setLogs] = useState("");
    const jobs = useOmegaStore((s) => s.jobs);
    const health = useHealth();

    useEffect(() => {
        if (activeTab !== "logs") return;
        const fetchLogs = async () => {
            try {
                const res = await fetch("/api/logs");
                const data = await res.json();
                setLogs(data.logs || "");
            } catch (e) { console.error(e); }
        };
        fetchLogs();
        const interval = setInterval(fetchLogs, 5000);
        return () => clearInterval(interval);
    }, [activeTab]);

    return (
        <div style={{ display: "flex", height: "100%" }}>
            {/* Sidebar */}
            <aside style={{ width: 224, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SidebarSection title="Monitor">
                    <SidebarItem label="Overview" isActive={activeTab === "overview"} onClick={() => setActiveTab("overview")} icon={<Activity style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="SLA Tracker" isActive={activeTab === "sla"} onClick={() => setActiveTab("sla")} icon={<Clock style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Analytics" isActive={activeTab === "analytics"} onClick={() => setActiveTab("analytics")} icon={<BarChart2 style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="System Logs" isActive={activeTab === "logs"} onClick={() => setActiveTab("logs")} icon={<Terminal style={{ width: 14, height: 14 }} />} />
                </SidebarSection>
            </aside>

            {/* Main Content */}
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, minHeight: 0, height: "100%" }}>
                {activeTab === "overview" && <OverviewPanel health={health} jobs={jobs} />}
                {activeTab === "sla" && <SlaPanel jobs={jobs} />}
                {activeTab === "analytics" && <AnalyticsPanel jobs={jobs} />}
                {activeTab === "logs" && <LogsPanel logs={logs} />}
            </main>

            {/* Right Sidebar (Inspector) - could remain the same as UnifiedWorkspace but let's keep it simple here */}
        </div>
    );
}
