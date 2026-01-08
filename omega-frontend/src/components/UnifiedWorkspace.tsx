"use client";

import { useState, ReactNode, useEffect, useMemo, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import {
    Film,
    LayoutGrid,
    Pencil,
    Settings,
    Package,
    Activity,
    Search,
    Bell,
    RefreshCw,
    UploadCloud,
    Loader2,
    Clock,
    CheckCircle2,
    AlertCircle,
    FileVideo,
    FileText,
    User,
    Globe,
    Palette,
    Cloud,
    FolderOpen,
    Server,
    HardDrive,
    Zap,
    Terminal,
    ExternalLink,
    Download,
    Info,
} from "lucide-react";
import { useOmegaStore, useActiveWorkspace, WorkspaceId, Job, HealthData, getStageLabel } from "@/store/omega";
import { TranslationProgress, CloudStageBadge } from "@/components/TranslationProgress";
import { EditorReport } from "@/components/EditorReport";
import { SmartJobSidebar } from "@/components/SmartJobSidebar";
import { MonitorDashboard } from "@/components/MonitorDashboard";
import { LanguageForkModal } from "@/components/LanguageForkModal";
import { SimpleDashboard } from "@/components/SimpleDashboard";

// =============================================================================
// WORKSPACE SHELL - Main container with instant tab switching via Zustand
// =============================================================================
export function UnifiedWorkspace() {
    const pathname = usePathname();
    const activeWorkspace = useActiveWorkspace();
    const setActiveWorkspace = useOmegaStore((s) => s.setActiveWorkspace);

    // Sync with URL on mount (one-time)
    useEffect(() => {
        let wsFromUrl: WorkspaceId = "pipeline";
        if (pathname === "/" || pathname.startsWith("/pipeline")) wsFromUrl = "pipeline";
        else if (pathname.startsWith("/media")) wsFromUrl = "media";
        else if (pathname.startsWith("/edit")) wsFromUrl = "edit";
        else if (pathname.startsWith("/settings")) wsFromUrl = "settings";
        else if (pathname.startsWith("/deliver")) wsFromUrl = "deliver";
        else if (pathname.startsWith("/monitor")) wsFromUrl = "monitor";

        if (wsFromUrl !== activeWorkspace) {
            setActiveWorkspace(wsFromUrl);
        }
    }, [pathname]); // Only on pathname change

    const WORKSPACES = [
        { id: "media" as const, label: "Media", icon: Film },
        { id: "pipeline" as const, label: "Pipeline", icon: LayoutGrid },
        { id: "edit" as const, label: "Edit", icon: Pencil },
        { id: "settings" as const, label: "Settings", icon: Settings },
        { id: "deliver" as const, label: "Deliver", icon: Package },
        { id: "monitor" as const, label: "Monitor", icon: Activity },
    ];

    return (
        <div className="flex h-screen flex-col bg-[#0f0f12] text-[#f5f5f5] overflow-hidden">
            {/* Top Bar */}
            <header className="h-14 flex items-center justify-between px-5 border-b border-[rgba(255,255,255,0.06)] bg-[#18181b] shrink-0">
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-[#528BFF] flex items-center justify-center">
                            <Film className="w-4 h-4 text-white" />
                        </div>
                        <span className="text-[15px] font-semibold tracking-tight">Omega Pro</span>
                    </div>
                </div>

                <div className="flex-1 max-w-lg mx-10">
                    <div className="relative">
                        <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#6b7280]" />
                        <input
                            type="text"
                            placeholder="Search jobs..."
                            className="w-full bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.08)] rounded-lg pl-10 pr-4 py-2 text-[13px] text-[#f5f5f5] placeholder:text-[#6b7280] focus:border-[rgba(82,139,255,0.5)] focus:outline-none transition"
                        />
                        <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[11px] text-[#6b7280] font-medium">⌘K</span>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <button
                        className="p-2.5 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[#9ca3af] hover:text-[#f5f5f5] transition"
                        onClick={() => window.dispatchEvent(new CustomEvent("omega-refresh"))}
                        title="Refresh all data"
                    >
                        <RefreshCw className="w-4 h-4" />
                    </button>
                    <button className="p-2.5 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[#9ca3af] hover:text-[#f5f5f5] transition relative">
                        <Bell className="w-4 h-4" />
                        <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-[#f59e0b]"></span>
                    </button>
                </div>
            </header>

            {/* Workspace Content - PRE-MOUNT all, visibility toggle for instant switching */}
            <div className="flex-1 min-h-0 flex overflow-hidden" style={{ paddingBottom: 56 }}>
                <WorkspacePanel isActive={activeWorkspace === "media"}>
                    <MediaWorkspaceContent />
                </WorkspacePanel>
                <WorkspacePanel isActive={activeWorkspace === "pipeline"}>
                    <SimpleDashboard />
                </WorkspacePanel>
                <WorkspacePanel isActive={activeWorkspace === "edit"}>
                    <EditWorkspaceContent />
                </WorkspacePanel>
                <WorkspacePanel isActive={activeWorkspace === "settings"}>
                    <SettingsWorkspaceContent />
                </WorkspacePanel>
                <WorkspacePanel isActive={activeWorkspace === "deliver"}>
                    <DeliverWorkspaceContent />
                </WorkspacePanel>
                <WorkspacePanel isActive={activeWorkspace === "monitor"}>
                    <MonitorDashboard />
                </WorkspacePanel>
            </div>

            {/* Bottom Tabs - Client-side switching */}
            <nav
                className="flex items-center justify-center border-t border-[rgba(255,255,255,0.06)] bg-[#0a0a0c]"
                style={{ height: 56, gap: 40, position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 100 }}
            >
                {WORKSPACES.map((ws) => {
                    const Icon = ws.icon;
                    const isActive = activeWorkspace === ws.id;
                    return (
                        <button
                            key={ws.id}
                            onClick={() => setActiveWorkspace(ws.id)}
                            className={`flex items-center rounded-lg text-[13px] font-medium transition ${isActive
                                ? "bg-[rgba(82,139,255,0.12)] text-[#528BFF]"
                                : "text-[#6b7280] hover:text-[#f5f5f5] hover:bg-[rgba(255,255,255,0.05)]"
                                }`}
                            style={{ gap: 10, padding: "10px 16px", border: "none", cursor: "pointer" }}
                        >
                            <Icon style={{ width: 18, height: 18 }} />
                            <span>{ws.label}</span>
                        </button>
                    );
                })}
            </nav>
        </div>
    );
}

// =============================================================================
// SHARED COMPONENTS
// =============================================================================

/**
 * WorkspacePanel - Wrapper that toggles visibility without unmounting.
 * This enables instant tab switching by keeping all workspaces mounted.
 */
function WorkspacePanel({ isActive, children }: { isActive: boolean; children: ReactNode }) {
    return (
        <div
            style={{
                display: isActive ? "flex" : "none",
                flex: 1,
                minHeight: 0,
                height: "100%",
                overflow: "hidden",
            }}
        >
            {children}
        </div>
    );
}

function SidebarSection({ title, children }: { title: string; children: ReactNode }) {
    return (
        <div style={{ padding: "16px 0" }}>
            <div style={{ padding: "0 16px 12px", fontSize: 11, fontWeight: 600, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                {title}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{children}</div>
        </div>
    );
}

function SidebarItem({ label, count, isActive, onClick, icon }: { label: string; count?: number; isActive?: boolean; onClick?: () => void; icon?: ReactNode }) {
    return (
        <button
            onClick={onClick}
            style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 16px",
                fontSize: 13,
                fontWeight: 500,
                borderRadius: 6,
                border: "none",
                cursor: "pointer",
                transition: "background 0.15s, color 0.15s",
                background: isActive ? "rgba(82, 139, 255, 0.12)" : "transparent",
                color: isActive ? "#528BFF" : "#9ca3af",
            }}
        >
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {icon}
                {label}
            </span>
            {count !== undefined && count > 0 && (
                <span style={{ fontSize: 11, fontWeight: 500, fontVariantNumeric: "tabular-nums", color: isActive ? "#528BFF" : "#6b7280" }}>
                    {count}
                </span>
            )}
        </button>
    );
}

function InspectorPanel({ title, children }: { title: string; children: ReactNode }) {
    return (
        <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
            <div style={{ padding: "16px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                <h3 style={{ fontSize: 12, fontWeight: 600, color: "#9ca3af", textTransform: "uppercase", letterSpacing: "0.05em", margin: 0 }}>
                    {title}
                </h3>
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>{children}</div>
        </div>
    );
}

function InspectorSection({ title, children }: { title: string; children: ReactNode }) {
    return (
        <div style={{ padding: "16px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
            <h4 style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.05em", margin: "0 0 12px 0" }}>
                {title}
            </h4>
            {children}
        </div>
    );
}

function InspectorRow({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", fontSize: 12 }}>
            <span style={{ color: "#6b7280" }}>{label}</span>
            <span style={{ color: "#f5f5f5", fontWeight: 500 }}>{children}</span>
        </div>
    );
}

// =============================================================================
// MEDIA WORKSPACE
// =============================================================================
function MediaWorkspaceContent() {
    // ✅ Use Global Store (No Polling)
    const jobs = useOmegaStore((s) => s.jobs);
    const [filter, setFilter] = useState<"all" | "recent" | "processing">("all");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
    const [uploading, setUploading] = useState(false);
    const [progress, setProgress] = useState(0);
    const [dragActive, setDragActive] = useState(false);
    const [uploadMessage, setUploadMessage] = useState<{ type: "success" | "error" | "warning"; text: string } | null>(null);

    const recentImports = useMemo(() => {
        const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
        return jobs.filter((j) => new Date(j.updated_at).getTime() > oneDayAgo).sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }, [jobs]);

    const processingJobs = useMemo(() => jobs.filter((j) => j.stage !== "DONE" && j.stage !== "DELIVER"), [jobs]);
    const displayedJobs = filter === "recent" ? recentImports.slice(0, 10) : filter === "processing" ? processingJobs : jobs;

    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        setDragActive(false);
        if (e.dataTransfer.files?.length) await uploadFiles(e.dataTransfer.files);
    };

    const uploadFiles = async (files: FileList) => {
        setUploading(true);
        setProgress(0);
        setUploadMessage(null);

        const SUPPORTED_EXTENSIONS = new Set([
            "mp3", "wav", "mp4", "m4a", "mov", "mkv", "mpg", "mpeg", "moc", "mxf"
        ]);

        const uploaded: string[] = [];
        const failed: string[] = [];
        const ignored: string[] = [];

        try {
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const ext = file.name.split('.').pop()?.toLowerCase();
                const isSupported = ext && SUPPORTED_EXTENSIONS.has(ext);

                const formData = new FormData();
                formData.append("file", file);

                await new Promise<void>((resolve, reject) => {
                    const xhr = new XMLHttpRequest();
                    xhr.open("POST", "http://127.0.0.1:8080/api/upload");

                    xhr.upload.onprogress = (event) => {
                        if (event.lengthComputable) {
                            const percentComplete = (event.loaded / event.total) * 100;
                            // Approximate total progress: (current file progress + previous files) / total files
                            // For simplicity, just show current file progress
                            setProgress(Math.round(percentComplete));
                        }
                    };

                    xhr.onload = () => {
                        if (xhr.status >= 200 && xhr.status < 300) {
                            if (isSupported) {
                                uploaded.push(file.name);
                            } else {
                                ignored.push(file.name);
                            }
                            resolve();
                        } else {
                            failed.push(file.name);
                            // Detect entity too large
                            if (xhr.status === 413) {
                                reject(new Error("File too large"));
                            } else {
                                resolve(); // Treat as failed but continue
                            }
                        }
                    };

                    xhr.onerror = () => {
                        failed.push(file.name);
                        reject(new Error("Network error"));
                    };

                    xhr.send(formData);
                });
            }
            // fetchJobs() removed - SSE handles updates


            let messageType: "success" | "error" | "warning" = "success";
            let messageText = "";

            if (uploaded.length > 0) {
                messageText += `✓ Uploaded: ${uploaded.join(", ")} `;
            }
            if (ignored.length > 0) {
                messageType = uploaded.length > 0 ? "warning" : "error";
                messageText += `⚠️ Saved but not processed (unsupported type): ${ignored.join(", ")} `;
            }
            if (failed.length > 0) {
                messageType = "error";
                messageText += `✗ Failed: ${failed.join(", ")}`;
            }

            if (messageText) {
                setUploadMessage({ type: messageType, text: messageText });
                if (messageType === "success") {
                    setTimeout(() => setUploadMessage(null), 5000);
                }
            }

        } catch (e: any) {
            setUploadMessage({
                type: "error",
                text: e.message === "File too large"
                    ? "Upload failed — File too large (Server limit)"
                    : "Upload failed — Network error or timeout"
            });
        }
        setUploading(false);
        setProgress(0);
    };

    return (
        <>
            <aside style={{ width: 224, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SidebarSection title="Library">
                    <SidebarItem label="All Media" count={jobs.length} isActive={filter === "all"} onClick={() => setFilter("all")} icon={<Film style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Recent" count={recentImports.length} isActive={filter === "recent"} onClick={() => setFilter("recent")} icon={<Clock style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Processing" count={processingJobs.length} isActive={filter === "processing"} onClick={() => setFilter("processing")} icon={<Loader2 style={{ width: 14, height: 14 }} />} />
                </SidebarSection>
            </aside>
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, minHeight: 0, height: "100%" }}>
                {uploadMessage && (
                    <div style={{
                        padding: "12px 16px",
                        marginBottom: 16,
                        borderRadius: 8,
                        background: uploadMessage.type === "success" ? "rgba(34,197,94,0.15)" : uploadMessage.type === "warning" ? "rgba(245,158,11,0.15)" : "rgba(239,68,68,0.15)",
                        border: `1px solid ${uploadMessage.type === "success" ? "rgba(34,197,94,0.3)" : uploadMessage.type === "warning" ? "rgba(245,158,11,0.3)" : "rgba(239,68,68,0.3)"}`,
                        color: uploadMessage.type === "success" ? "#22c55e" : uploadMessage.type === "warning" ? "#f59e0b" : "#ef4444",
                        fontSize: 13,
                        fontWeight: 500,
                    }}>
                        {uploadMessage.text}
                    </div>
                )}
                <input
                    type="file"
                    multiple
                    accept="video/*,audio/*"
                    style={{ display: "none" }}
                    id="media-file-input"
                    onChange={(e) => { if (e.target.files) uploadFiles(e.target.files); e.target.value = ""; }}
                />
                <div
                    onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
                    onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={handleDrop}
                    onClick={() => document.getElementById("media-file-input")?.click()}
                    style={{
                        border: `2px dashed ${dragActive ? "#528BFF" : "rgba(255,255,255,0.1)"}`,
                        borderRadius: 12,
                        padding: 48,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        gap: 16,
                        cursor: "pointer",
                        background: dragActive ? "rgba(82,139,255,0.08)" : "rgba(255,255,255,0.02)",
                        marginBottom: 24,
                    }}
                >
                    {uploading ? <Loader2 style={{ width: 48, height: 48, color: "#528BFF" }} /> : <UploadCloud style={{ width: 48, height: 48, color: dragActive ? "#528BFF" : "#6b7280" }} />}
                    <div style={{ textAlign: "center" }}>
                        <p style={{ fontSize: 16, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>{uploading ? `Uploading... ${progress}%` : "Drop files here or click to browse"}</p>
                        <p style={{ fontSize: 12, color: "#6b7280", margin: "8px 0 0 0" }}>Supported: MP4, MOV, MKV, WAV, MP3</p>
                    </div>
                </div>
                <h2 style={{ fontSize: 14, fontWeight: 600, color: "#9ca3af", marginBottom: 16, textTransform: "uppercase" }}>Media Library</h2>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {displayedJobs.map((job) => (
                        <div key={job.file_stem} onClick={() => setSelectedJob(job)} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderRadius: 8, background: selectedJob?.file_stem === job.file_stem ? "rgba(82,139,255,0.12)" : "rgba(255,255,255,0.03)", cursor: "pointer" }}>
                            <FileVideo style={{ width: 20, height: 20, color: "#528BFF" }} />
                            <div style={{ flex: 1 }}>
                                <p style={{ fontSize: 13, fontWeight: 500, color: "#f5f5f5", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.file_stem}</p>
                                <p style={{ fontSize: 11, color: "#6b7280", margin: 0 }}>{job.stage} • {job.status}</p>
                            </div>
                        </div>
                    ))}
                </div>
            </main>
            <aside style={{ width: 320, borderLeft: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <InspectorPanel title="Media Info">
                    {selectedJob ? (
                        <InspectorSection title="Details">
                            <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                            <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                            <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                        </InspectorSection>
                    ) : (
                        <div style={{ padding: 16, textAlign: "center", fontSize: 12, color: "#6b7280" }}>Select a file</div>
                    )}
                </InspectorPanel>
            </aside>
        </>
    );
}

// =============================================================================
// API HELPER
// =============================================================================
async function apiAction(action: string, file_stem: string, extra: Record<string, unknown> = {}): Promise<{ success?: boolean; error?: string; message?: string }> {
    try {
        const res = await fetch("/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, file_stem, ...extra }),
        });
        return await res.json();
    } catch (e) {
        return { success: false, error: String(e) };
    }
}

// =============================================================================
// ENCODING BANNER
// =============================================================================
// =============================================================================
// ENCODING BANNER
// =============================================================================
function EncodingBanner() {
    // Select any job that is in the BURN stage directly from store
    const burningJob = useOmegaStore(s => s.jobs.find(j => j.stage === "BURN"));

    if (!burningJob) return null;

    // Approximating elapsed time or remove it if not critical, as it requires a tick
    // For simplicity, we just show the status
    return (
        <div style={{ padding: "12px 16px", background: "rgba(82,139,255,0.15)", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 12 }}>
            <Loader2 style={{ width: 18, height: 18, color: "#528BFF", animation: "spin 1s linear infinite" }} />
            <div style={{ flex: 1 }}>
                <p style={{ margin: 0, fontSize: 13, fontWeight: 500, color: "#f5f5f5" }}>Encoding: {burningJob.file_stem}</p>
                <p style={{ margin: 0, fontSize: 11, color: "#9ca3af" }}>{burningJob.status}</p>
            </div>
        </div>
    );
}

// =============================================================================
// ACTION BUTTON
// =============================================================================
function ActionButton({ label, icon, onClick, variant = "default", disabled = false }: { label: string; icon?: ReactNode; onClick: () => void; variant?: "default" | "primary" | "danger"; disabled?: boolean }) {
    const bg = variant === "primary" ? "#528BFF" : variant === "danger" ? "#ef4444" : "rgba(255,255,255,0.08)";
    const hoverBg = variant === "primary" ? "#4178e0" : variant === "danger" ? "#dc2626" : "rgba(255,255,255,0.12)";
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "10px 16px", background: bg, color: "#fff", border: "none", borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1, transition: "background 0.15s" }}
            onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = hoverBg; }}
            onMouseLeave={(e) => { if (!disabled) e.currentTarget.style.background = bg; }}
        >
            {icon}{label}
        </button>
    );
}

// =============================================================================
// PIPELINE WORKSPACE (Dashboard) - Now using Zustand store for real-time updates
// =============================================================================
function PipelineWorkspaceContent() {
    // Use Zustand store selectors - real-time via SSE
    const jobs = useOmegaStore((s) => s.jobs);
    const health = useOmegaStore((s) => s.health);
    const selectedJobId = useOmegaStore((s) => s.selectedJobId);
    const selectJob = useOmegaStore((s) => s.selectJob);

    const [phaseFilter, setPhaseFilter] = useState("All");
    const [actionLoading, setActionLoading] = useState(false);
    const [actionResult, setActionResult] = useState<string | null>(null);
    const [forkModalOpen, setForkModalOpen] = useState(false);

    const PHASES = ["Ingest", "Transcribe", "Translate", "Review", "Finalize", "Burn", "Deliver"];

    // Get selected job from store
    const selectedJob = useMemo(() => {
        if (!selectedJobId) return null;
        return jobs.find((j: Job) => j.file_stem === selectedJobId) || null;
    }, [jobs, selectedJobId]);

    const runAction = async (action: string, extra: Record<string, unknown> = {}) => {
        if (!selectedJob) return;
        setActionLoading(true);
        setActionResult(null);
        const result = await apiAction(action, selectedJob.file_stem, extra);
        setActionResult(result.message || result.error || "Done");
        setActionLoading(false);
        setTimeout(() => setActionResult(null), 3000);
        // No need to fetchData - SSE will push updates automatically
    };

    const stageToPhase = (stage: string) => {
        const s = stage.toUpperCase();
        if (s.includes("INGEST")) return "Ingest";
        if (s.includes("TRANSCRIB")) return "Transcribe";
        if (s.includes("TRANSLAT")) return "Translate";
        if (s.includes("REVIEW") || s.includes("EDIT")) return "Review";
        if (s.includes("FINAL")) return "Finalize";
        if (s.includes("BURN")) return "Burn";
        if (s.includes("COMPLET") || s.includes("DONE")) return "Deliver";
        return "Deliver";
    };

    const phaseCounts = useMemo(() => {
        const counts: Record<string, number> = { Ingest: 0, Transcribe: 0, Translate: 0, Review: 0, Finalize: 0, Burn: 0, Deliver: 0 };
        jobs.forEach((j: Job) => { counts[stageToPhase(j.stage)] = (counts[stageToPhase(j.stage)] || 0) + 1; });
        return counts;
    }, [jobs]);

    const filteredJobs = phaseFilter === "All" ? jobs : jobs.filter((j: Job) => stageToPhase(j.stage) === phaseFilter);
    const isHalted = selectedJob?.meta && typeof selectedJob.meta === "object" && (selectedJob.meta as Record<string, unknown>).halted;
    const isError = selectedJob?.status?.toLowerCase().includes("error") || selectedJob?.status?.toLowerCase().includes("fail");
    const canBurn = selectedJob?.stage?.toUpperCase().includes("FINAL");
    const canRetry = isError || isHalted;

    return (
        <>
            <aside style={{ width: 260, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SmartJobSidebar />
                <SidebarSection title="System">
                    <div style={{ padding: "0 16px", display: "flex", flexDirection: "column", gap: 12 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                            <span style={{ color: "#9ca3af" }}>Storage</span>
                            <span style={{ color: health?.storage_ready ? "#22c55e" : "#ef4444" }}>{health?.storage_ready ? "OK" : "—"}</span>
                        </div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                            <span style={{ color: "#9ca3af" }}>Manager</span>
                            <span style={{ color: "#f5f5f5" }}>{health?.heartbeats?.omega_manager_age_seconds?.toFixed(0) || "—"}s</span>
                        </div>
                    </div>
                </SidebarSection>
            </aside>
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, minHeight: 0, height: "100%" }}>
                <EncodingBanner />
                <h2 style={{ fontSize: 14, fontWeight: 600, color: "#9ca3af", marginBottom: 16, textTransform: "uppercase" }}>{phaseFilter === "All" ? "All Jobs" : phaseFilter}</h2>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
                    {filteredJobs.map((job) => {
                        const jobHalted = job.meta && typeof job.meta === "object" && (job.meta as Record<string, unknown>).halted;
                        const jobError = job.status?.toLowerCase().includes("error") || job.status?.toLowerCase().includes("fail");
                        return (
                            <div key={job.file_stem} onClick={() => selectJob(job.file_stem)} style={{ padding: 20, borderRadius: 12, background: selectedJob?.file_stem === job.file_stem ? "rgba(82,139,255,0.12)" : "rgba(255,255,255,0.03)", border: `1px solid ${jobError ? "rgba(239,68,68,0.3)" : jobHalted ? "rgba(245,158,11,0.3)" : "rgba(255,255,255,0.06)"}`, cursor: "pointer" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                    {jobError && <AlertCircle style={{ width: 14, height: 14, color: "#ef4444" }} />}
                                    {Boolean(jobHalted) && !jobError && <Clock style={{ width: 14, height: 14, color: "#f59e0b" }} />}
                                    <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{job.file_stem}</h3>
                                </div>
                                <p style={{ fontSize: 12, color: "#6b7280", margin: "8px 0 0 0" }}>{getStageLabel(job.stage)}</p>
                                <CloudStageBadge job={job} />
                            </div>
                        );
                    })}
                </div>
            </main>
            <aside style={{ width: 320, borderLeft: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <InspectorPanel title="Job Details">
                    {selectedJob ? (
                        <>
                            <InspectorSection title="Info">
                                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                                <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                                <InspectorRow label="Updated">{new Date(selectedJob.updated_at).toLocaleString()}</InspectorRow>
                            </InspectorSection>
                            <TranslationProgress job={selectedJob} />
                            <EditorReport job={selectedJob} />
                            <InspectorSection title="Actions">
                                {actionResult && <div style={{ padding: "8px 12px", background: "rgba(82,139,255,0.1)", borderRadius: 6, marginBottom: 12, fontSize: 11, color: "#528BFF" }}>{actionResult}</div>}
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                    {Boolean(isHalted) && <ActionButton label="Resume Job" icon={<RefreshCw style={{ width: 14, height: 14 }} />} onClick={() => runAction("unhalt_job")} disabled={actionLoading} variant="primary" />}
                                    {Boolean(canRetry) && <ActionButton label="Retry Translation" icon={<RefreshCw style={{ width: 14, height: 14 }} />} onClick={() => runAction("retry_translate")} disabled={actionLoading} />}
                                    {canBurn && <ActionButton label="Force Burn" icon={<Zap style={{ width: 14, height: 14 }} />} onClick={() => runAction("force_burn")} disabled={actionLoading} variant="primary" />}
                                    <ActionButton label="Re-Burn" icon={<RefreshCw style={{ width: 14, height: 14 }} />} onClick={() => runAction("re_burn")} disabled={actionLoading} />
                                    <ActionButton label="Localize / Fork" icon={<Globe style={{ width: 14, height: 14 }} />} onClick={() => setForkModalOpen(true)} disabled={actionLoading} />
                                    <ActionButton label="Delete Job" icon={<AlertCircle style={{ width: 14, height: 14 }} />} onClick={() => { if (confirm("Delete this job?")) runAction("delete_job"); }} disabled={actionLoading} variant="danger" />
                                </div>
                            </InspectorSection>
                            <LanguageForkModal jobId={selectedJob.file_stem} isOpen={forkModalOpen} onClose={() => setForkModalOpen(false)} onSuccess={() => { }} />
                        </>
                    ) : (
                        <div style={{ padding: 16, textAlign: "center", fontSize: 12, color: "#6b7280" }}>Select a job to view details</div>
                    )}
                </InspectorPanel>
            </aside>
        </>
    );
}

// =============================================================================
// EDIT WORKSPACE
// =============================================================================
function EditWorkspaceContent() {
    const router = useRouter();
    // ✅ Use Global Store (No Polling)
    const jobs = useOmegaStore((s) => s.jobs);
    const [filter, setFilter] = useState<"editable" | "review" | "all">("editable");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);

    const editableJobs = useMemo(() => jobs.filter((j) => ["TRANSLATE", "REVIEW", "FINALIZE", "DONE"].includes(j.stage) || j.stage.includes("TRANSLAT") || j.stage.includes("EDIT")), [jobs]);
    const reviewJobs = useMemo(() => jobs.filter((j) => j.status === "needs_review" || j.stage === "REVIEW"), [jobs]);
    const displayedJobs = filter === "editable" ? editableJobs : filter === "review" ? reviewJobs : jobs;

    const openEditor = (stem: string) => router.push(`/editor/${encodeURIComponent(stem)}`);

    return (
        <>
            <aside style={{ width: 224, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SidebarSection title="Edit Queue">
                    <SidebarItem label="Editable" count={editableJobs.length} isActive={filter === "editable"} onClick={() => setFilter("editable")} icon={<Pencil style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Needs Review" count={reviewJobs.length} isActive={filter === "review"} onClick={() => setFilter("review")} icon={<AlertCircle style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="All" count={jobs.length} isActive={filter === "all"} onClick={() => setFilter("all")} icon={<FileText style={{ width: 14, height: 14 }} />} />
                </SidebarSection>
            </aside>
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, minHeight: 0, height: "100%" }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, color: "#9ca3af", marginBottom: 16, textTransform: "uppercase" }}>{filter === "editable" ? "Editable Jobs" : filter === "review" ? "Needs Review" : "All Jobs"}</h2>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {displayedJobs.map((job) => (
                        <div key={job.file_stem} onClick={() => setSelectedJob(job)} onDoubleClick={() => openEditor(job.file_stem)} style={{ display: "flex", alignItems: "center", gap: 16, padding: "16px 20px", borderRadius: 10, background: selectedJob?.file_stem === job.file_stem ? "rgba(82,139,255,0.12)" : "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", cursor: "pointer" }}>
                            <FileText style={{ width: 22, height: 22, color: "#528BFF" }} />
                            <div style={{ flex: 1 }}>
                                <p style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>{job.file_stem}</p>
                                <p style={{ fontSize: 12, color: "#6b7280", margin: 0 }}>{job.stage} • {job.status}</p>
                            </div>
                            <button onClick={(e) => { e.stopPropagation(); openEditor(job.file_stem); }} style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", background: "rgba(82,139,255,0.15)", color: "#528BFF", border: "none", borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: "pointer" }}>
                                <Pencil style={{ width: 14, height: 14 }} /> Edit
                            </button>
                        </div>
                    ))}
                </div>
            </main>
            <aside style={{ width: 320, borderLeft: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <InspectorPanel title="Edit Details">
                    {selectedJob ? (
                        <>
                            <InspectorSection title="Job Info">
                                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                                <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                            </InspectorSection>
                            <InspectorSection title="Actions">
                                <button onClick={() => openEditor(selectedJob.file_stem)} style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "12px 16px", background: "#528BFF", color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: "pointer" }}>
                                    <ExternalLink style={{ width: 16, height: 16 }} /> Open Editor
                                </button>
                            </InspectorSection>
                        </>
                    ) : (
                        <div style={{ padding: 16, textAlign: "center", fontSize: 12, color: "#6b7280" }}>Select a job to edit</div>
                    )}
                </InspectorPanel>
            </aside>
        </>
    );
}

// =============================================================================
// DELIVER WORKSPACE
// =============================================================================
function DeliverWorkspaceContent() {
    // ✅ Use Global Store (No Polling)
    const jobs = useOmegaStore((s) => s.jobs);
    const [filter, setFilter] = useState<"ready" | "delivered" | "all">("ready");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
    const [marking, setMarking] = useState(false);

    const readyJobs = useMemo(() => jobs.filter((j) => (j.stage === "DONE" || j.stage === "COMPLETED") && j.status !== "DELIVERED" && j.status !== "Delivered"), [jobs]);
    const deliveredJobs = useMemo(() => jobs.filter((j) => j.status === "DELIVERED" || j.status === "Delivered"), [jobs]);
    const displayedJobs = filter === "ready" ? readyJobs : filter === "delivered" ? deliveredJobs : jobs.filter((j) => j.stage === "DONE" || j.stage === "COMPLETED" || j.status === "DELIVERED" || j.status === "Delivered");

    const markDelivered = async (stem: string) => {
        setMarking(true);
        try {
            const res = await fetch("/api/mark_delivered", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_stem: stem }) });
            const data = await res.json();
            if (data.success) {
                // No fetchJobs(); waiting for SSE update
                setSelectedJob(null);
            } else {
                alert(data.error || "Failed to mark delivered");
            }
        } catch (e) { alert("Failed to mark delivered"); }
        setMarking(false);
    };

    return (
        <>
            <aside style={{ width: 224, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SidebarSection title="Deliverables">
                    <SidebarItem label="Ready" count={readyJobs.length} isActive={filter === "ready"} onClick={() => setFilter("ready")} icon={<Package style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Delivered" count={deliveredJobs.length} isActive={filter === "delivered"} onClick={() => setFilter("delivered")} icon={<CheckCircle2 style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="All" count={readyJobs.length + deliveredJobs.length} isActive={filter === "all"} onClick={() => setFilter("all")} icon={<FolderOpen style={{ width: 14, height: 14 }} />} />
                </SidebarSection>
            </aside>
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, minHeight: 0, height: "100%" }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, color: "#9ca3af", marginBottom: 16, textTransform: "uppercase" }}>{filter === "ready" ? "Ready for Delivery" : filter === "delivered" ? "Delivered" : "All"}</h2>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
                    {displayedJobs.map((job) => (
                        <div key={job.file_stem} onClick={() => setSelectedJob(job)} style={{ padding: 20, borderRadius: 12, background: selectedJob?.file_stem === job.file_stem ? "rgba(82,139,255,0.12)" : "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", cursor: "pointer" }}>
                            <div style={{ display: "flex", gap: 12 }}>
                                <div style={{ width: 44, height: 44, borderRadius: 8, background: job.status === "DELIVERED" ? "rgba(34,197,94,0.15)" : "rgba(82,139,255,0.15)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                                    {job.status === "DELIVERED" ? <CheckCircle2 style={{ width: 22, height: 22, color: "#22c55e" }} /> : <Package style={{ width: 22, height: 22, color: "#528BFF" }} />}
                                </div>
                                <div>
                                    <h3 style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>{job.file_stem}</h3>
                                    <p style={{ fontSize: 12, color: "#6b7280", margin: 0 }}>{job.status === "DELIVERED" ? "Delivered" : "Ready"}</p>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </main>
            <aside style={{ width: 320, borderLeft: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <InspectorPanel title="Delivery Details">
                    {selectedJob ? (
                        <>
                            <InspectorSection title="Output">
                                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                            </InspectorSection>
                            {selectedJob.status !== "DELIVERED" && (
                                <InspectorSection title="Actions">
                                    <button onClick={() => markDelivered(selectedJob.file_stem)} disabled={marking} style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "10px 16px", background: "#22c55e", color: "#fff", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: marking ? "not-allowed" : "pointer", opacity: marking ? 0.6 : 1 }}>
                                        <CheckCircle2 style={{ width: 16, height: 16 }} /> {marking ? "Marking..." : "Mark Delivered"}
                                    </button>
                                </InspectorSection>
                            )}
                        </>
                    ) : (
                        <div style={{ padding: 16, textAlign: "center", fontSize: 12, color: "#6b7280" }}>Select a job</div>
                    )}
                </InspectorPanel>
            </aside>
        </>
    );
}

// =============================================================================
// MONITOR WORKSPACE
// =============================================================================



// =============================================================================
// SETTINGS WORKSPACE
// =============================================================================
function SettingsWorkspaceContent() {
    const [section, setSection] = useState<"general" | "profiles" | "languages" | "styles" | "cloud">("general");

    return (
        <>
            <aside style={{ width: 224, borderRight: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <SidebarSection title="Settings">
                    <SidebarItem label="General" isActive={section === "general"} onClick={() => setSection("general")} icon={<Settings style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Profiles" isActive={section === "profiles"} onClick={() => setSection("profiles")} icon={<User style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Languages" isActive={section === "languages"} onClick={() => setSection("languages")} icon={<Globe style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Styles" isActive={section === "styles"} onClick={() => setSection("styles")} icon={<Palette style={{ width: 14, height: 14 }} />} />
                    <SidebarItem label="Cloud" isActive={section === "cloud"} onClick={() => setSection("cloud")} icon={<Cloud style={{ width: 14, height: 14 }} />} />
                </SidebarSection>
            </aside>
            <main style={{ flex: 1, overflowY: "auto", background: "#0f0f12", padding: 24, maxWidth: 800 }}>
                <h2 style={{ fontSize: 18, fontWeight: 600, color: "#f5f5f5", marginBottom: 24 }}>
                    {section.charAt(0).toUpperCase() + section.slice(1)}
                </h2>
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 0", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                        <div>
                            <p style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>Default Target Language</p>
                            <p style={{ fontSize: 12, color: "#6b7280", margin: "4px 0 0 0" }}>Language used for new jobs</p>
                        </div>
                        <select style={{ padding: "8px 12px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#f5f5f5", fontSize: 13 }}>
                            <option>Icelandic</option>
                            <option>English</option>
                            <option>Spanish</option>
                        </select>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 0", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                        <div>
                            <p style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", margin: 0 }}>Subtitle Style</p>
                            <p style={{ fontSize: 12, color: "#6b7280", margin: "4px 0 0 0" }}>ASS template for burns</p>
                        </div>
                        <select style={{ padding: "8px 12px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, color: "#f5f5f5", fontSize: 13 }}>
                            <option>Cinema</option>
                            <option>Minimal</option>
                            <option>Classic</option>
                        </select>
                    </div>
                </div>
            </main>
            <aside style={{ width: 320, borderLeft: "1px solid rgba(255,255,255,0.06)", background: "#18181b", flexShrink: 0, overflowY: "auto" }}>
                <InspectorPanel title="Help">
                    <div style={{ padding: 16, fontSize: 12, color: "#9ca3af", lineHeight: 1.6 }}>
                        Configure default settings for new jobs.
                    </div>
                </InspectorPanel>
            </aside>
        </>
    );
}
