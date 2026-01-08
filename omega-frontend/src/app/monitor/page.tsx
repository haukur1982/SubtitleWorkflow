"use client";

import { useState, useEffect } from "react";
import {
    Activity,
    Server,
    HardDrive,
    Clock,
    AlertCircle,
    CheckCircle2,
    RefreshCw,
    Terminal,
    Cpu,
    Zap
} from "lucide-react";
import { WorkspaceShell } from "@/components/layout/WorkspaceShell";
import { Sidebar, SidebarSection, SidebarItem } from "@/components/layout/Sidebar";
import { Inspector, InspectorSection, InspectorRow } from "@/components/layout/Inspector";

interface HealthData {
    storage_ready: boolean;
    cloud_enabled: boolean;
    heartbeats: {
        omega_manager_age_seconds?: number;
        dashboard_age_seconds?: number;
    };
    disk_free_gb?: number;
    active_jobs?: number;
}

interface LogEntry {
    timestamp: string;
    level: string;
    message: string;
}

export default function MonitorWorkspace() {
    const [health, setHealth] = useState<HealthData | null>(null);
    const [logs, setLogs] = useState<string>("");
    const [view, setView] = useState<"overview" | "logs">("overview");
    const [restarting, setRestarting] = useState(false);

    const fetchHealth = async () => {
        try {
            const res = await fetch("/api/health");
            const data = await res.json();
            setHealth(data);
        } catch (err) {
            console.error("Failed to fetch health", err);
        }
    };

    const fetchLogs = async () => {
        try {
            const res = await fetch("/api/logs");
            const data = await res.json();
            setLogs(data.logs || "");
        } catch (err) {
            console.error("Failed to fetch logs", err);
        }
    };

    useEffect(() => {
        fetchHealth();
        fetchLogs();
        const interval = setInterval(() => {
            fetchHealth();
            if (view === "logs") fetchLogs();
        }, 5000);
        return () => clearInterval(interval);
    }, [view]);

    const handleRestartManager = async () => {
        if (!confirm("Restart the Omega Manager?")) return;
        setRestarting(true);
        try {
            await fetch("/api/action", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "restart_manager" }),
            });
            setTimeout(fetchHealth, 3000);
        } catch (err) {
            console.error(err);
            alert("Failed to restart manager");
        } finally {
            setRestarting(false);
        }
    };

    const getStatusColor = (isOk: boolean) => isOk ? '#22c55e' : '#ef4444';
    const getLatencyStatus = (seconds?: number) => {
        if (!seconds) return { color: '#6b7280', label: 'Unknown' };
        if (seconds < 30) return { color: '#22c55e', label: 'Healthy' };
        if (seconds < 120) return { color: '#f59e0b', label: 'Slow' };
        return { color: '#ef4444', label: 'Stale' };
    };

    // Sidebar
    const sidebarContent = (
        <Sidebar>
            <SidebarSection title="Monitor">
                <SidebarItem
                    label="Overview"
                    isActive={view === "overview"}
                    onClick={() => setView("overview")}
                    icon={<Activity style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Logs"
                    isActive={view === "logs"}
                    onClick={() => setView("logs")}
                    icon={<Terminal style={{ width: 14, height: 14 }} />}
                />
            </SidebarSection>
            <SidebarSection title="Actions">
                <div style={{ padding: '0 16px' }}>
                    <button
                        onClick={handleRestartManager}
                        disabled={restarting}
                        style={{
                            width: '100%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 8,
                            padding: '10px 16px',
                            background: 'rgba(255,255,255,0.05)',
                            color: '#f5f5f5',
                            border: '1px solid rgba(255,255,255,0.1)',
                            borderRadius: 6,
                            fontSize: 12,
                            fontWeight: 500,
                            cursor: restarting ? 'not-allowed' : 'pointer',
                            opacity: restarting ? 0.6 : 1,
                        }}
                    >
                        <RefreshCw style={{ width: 14, height: 14 }} />
                        {restarting ? "Restarting..." : "Restart Manager"}
                    </button>
                </div>
            </SidebarSection>
        </Sidebar>
    );

    // Inspector
    const managerStatus = getLatencyStatus(health?.heartbeats?.omega_manager_age_seconds);
    const dashboardStatus = getLatencyStatus(health?.heartbeats?.dashboard_age_seconds);

    const inspectorContent = (
        <Inspector title="System Status">
            <InspectorSection title="Heartbeats">
                <InspectorRow label="Manager">
                    <span style={{ color: managerStatus.color }}>
                        {health?.heartbeats?.omega_manager_age_seconds?.toFixed(0) || "—"}s
                    </span>
                </InspectorRow>
                <InspectorRow label="Dashboard">
                    <span style={{ color: dashboardStatus.color }}>
                        {health?.heartbeats?.dashboard_age_seconds?.toFixed(0) || "—"}s
                    </span>
                </InspectorRow>
            </InspectorSection>
            <InspectorSection title="Storage">
                <InspectorRow label="Status">
                    <span style={{ color: getStatusColor(health?.storage_ready || false) }}>
                        {health?.storage_ready ? "Ready" : "Not Ready"}
                    </span>
                </InspectorRow>
                <InspectorRow label="Free Space">
                    {health?.disk_free_gb?.toFixed(1) || "—"} GB
                </InspectorRow>
            </InspectorSection>
            <InspectorSection title="Cloud">
                <InspectorRow label="Pipeline">
                    <span style={{ color: health?.cloud_enabled ? '#22c55e' : '#6b7280' }}>
                        {health?.cloud_enabled ? "Enabled" : "Disabled"}
                    </span>
                </InspectorRow>
            </InspectorSection>
        </Inspector>
    );

    return (
        <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
            <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24 }}>
                {view === "overview" ? (
                    <>
                        {/* Health Cards */}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
                            {/* Manager Card */}
                            <div style={{
                                padding: 20,
                                borderRadius: 12,
                                background: 'rgba(255,255,255,0.03)',
                                border: '1px solid rgba(255,255,255,0.06)',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                                    <div style={{
                                        width: 40,
                                        height: 40,
                                        borderRadius: 8,
                                        background: `${managerStatus.color}20`,
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center'
                                    }}>
                                        <Server style={{ width: 20, height: 20, color: managerStatus.color }} />
                                    </div>
                                    <div>
                                        <h3 style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>Manager</h3>
                                        <p style={{ fontSize: 12, color: managerStatus.color, margin: 0 }}>{managerStatus.label}</p>
                                    </div>
                                </div>
                                <p style={{ fontSize: 24, fontWeight: 600, color: '#f5f5f5', margin: 0 }}>
                                    {health?.heartbeats?.omega_manager_age_seconds?.toFixed(0) || "—"}s
                                </p>
                                <p style={{ fontSize: 11, color: '#6b7280', margin: 0, marginTop: 4 }}>Last heartbeat</p>
                            </div>

                            {/* Storage Card */}
                            <div style={{
                                padding: 20,
                                borderRadius: 12,
                                background: 'rgba(255,255,255,0.03)',
                                border: '1px solid rgba(255,255,255,0.06)',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                                    <div style={{
                                        width: 40,
                                        height: 40,
                                        borderRadius: 8,
                                        background: health?.storage_ready ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center'
                                    }}>
                                        <HardDrive style={{ width: 20, height: 20, color: getStatusColor(health?.storage_ready || false) }} />
                                    </div>
                                    <div>
                                        <h3 style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>Storage</h3>
                                        <p style={{ fontSize: 12, color: getStatusColor(health?.storage_ready || false), margin: 0 }}>
                                            {health?.storage_ready ? "Ready" : "Not Mounted"}
                                        </p>
                                    </div>
                                </div>
                                <p style={{ fontSize: 24, fontWeight: 600, color: '#f5f5f5', margin: 0 }}>
                                    {health?.disk_free_gb?.toFixed(0) || "—"} GB
                                </p>
                                <p style={{ fontSize: 11, color: '#6b7280', margin: 0, marginTop: 4 }}>Free space</p>
                            </div>

                            {/* Cloud Card */}
                            <div style={{
                                padding: 20,
                                borderRadius: 12,
                                background: 'rgba(255,255,255,0.03)',
                                border: '1px solid rgba(255,255,255,0.06)',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                                    <div style={{
                                        width: 40,
                                        height: 40,
                                        borderRadius: 8,
                                        background: health?.cloud_enabled ? 'rgba(82,139,255,0.2)' : 'rgba(107,114,128,0.2)',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center'
                                    }}>
                                        <Zap style={{ width: 20, height: 20, color: health?.cloud_enabled ? '#528BFF' : '#6b7280' }} />
                                    </div>
                                    <div>
                                        <h3 style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>Cloud</h3>
                                        <p style={{ fontSize: 12, color: health?.cloud_enabled ? '#528BFF' : '#6b7280', margin: 0 }}>
                                            {health?.cloud_enabled ? "Enabled" : "Disabled"}
                                        </p>
                                    </div>
                                </div>
                                <p style={{ fontSize: 24, fontWeight: 600, color: '#f5f5f5', margin: 0 }}>
                                    {health?.active_jobs || 0}
                                </p>
                                <p style={{ fontSize: 11, color: '#6b7280', margin: 0, marginTop: 4 }}>Active jobs</p>
                            </div>
                        </div>
                    </>
                ) : (
                    /* Log Viewer */
                    <div style={{
                        flex: 1,
                        background: 'rgba(0,0,0,0.3)',
                        borderRadius: 8,
                        border: '1px solid rgba(255,255,255,0.06)',
                        overflow: 'hidden',
                    }}>
                        <div style={{
                            padding: '12px 16px',
                            background: 'rgba(255,255,255,0.03)',
                            borderBottom: '1px solid rgba(255,255,255,0.06)',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8
                        }}>
                            <Terminal style={{ width: 14, height: 14, color: '#6b7280' }} />
                            <span style={{ fontSize: 12, fontWeight: 500, color: '#9ca3af' }}>System Logs</span>
                            <button
                                onClick={fetchLogs}
                                style={{
                                    marginLeft: 'auto',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 4,
                                    padding: '4px 8px',
                                    background: 'rgba(255,255,255,0.05)',
                                    border: 'none',
                                    borderRadius: 4,
                                    fontSize: 11,
                                    color: '#9ca3af',
                                    cursor: 'pointer',
                                }}
                            >
                                <RefreshCw style={{ width: 12, height: 12 }} /> Refresh
                            </button>
                        </div>
                        <pre style={{
                            margin: 0,
                            padding: 16,
                            fontSize: 11,
                            fontFamily: 'monospace',
                            color: '#d4d4d4',
                            overflowX: 'auto',
                            overflowY: 'auto',
                            maxHeight: 'calc(100vh - 280px)',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                        }}>
                            {logs || "Loading logs..."}
                        </pre>
                    </div>
                )}
            </div>
        </WorkspaceShell>
    );
}
