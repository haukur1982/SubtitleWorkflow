"use client";

import { useState } from "react";
import { useOmegaStore } from "@/store/omega";
import { Film, Download, Pencil, Trash2, CheckCircle, Loader2 } from "lucide-react";

export function SimpleDashboard() {
    const jobs = useOmegaStore((s) => s.jobs);
    const [selectedJob, setSelectedJob] = useState<string | null>(null);

    // Sort: in-progress first, then recent
    const sortedJobs = [...jobs].sort((a, b) => {
        const aActive = !["DONE", "COMPLETED", "DELIVERED"].includes(a.stage);
        const bActive = !["DONE", "COMPLETED", "DELIVERED"].includes(b.stage);
        if (aActive !== bActive) return aActive ? -1 : 1;
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });

    const handleAction = async (action: string, stem: string) => {
        try {
            const res = await fetch("/api/action", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action, file_stem: stem }),
            });
            if (!res.ok) throw new Error("Action failed");
        } catch (e) {
            alert("Error: " + e);
        }
    };

    return (
        <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
            <div style={{ marginBottom: 24 }}>
                <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600, color: "#f5f5f5" }}>
                    Job Queue
                </h1>
                <p style={{ margin: "8px 0 0", fontSize: 14, color: "#9ca3af" }}>
                    {jobs.length} total • {sortedJobs.filter(j => !["DONE", "COMPLETED", "DELIVERED"].includes(j.stage)).length} active
                </p>
            </div>

            <div style={{ display: "grid", gap: 16 }}>
                {sortedJobs.map((job) => (
                    <JobCard
                        key={job.file_stem}
                        job={job}
                        onAction={handleAction}
                        isSelected={selectedJob === job.file_stem}
                        onClick={() => setSelectedJob(job.file_stem)}
                    />
                ))}
            </div>

            {jobs.length === 0 && (
                <div style={{ textAlign: "center", padding: 64, color: "#71717a" }}>
                    <Film size={48} style={{ margin: "0 auto 16px", opacity: 0.3 }} />
                    <p style={{ fontSize: 14 }}>No jobs yet. Drop a video to get started.</p>
                </div>
            )}
        </div>
    );
}

function JobCard({ job, onAction, isSelected, onClick }: any) {
    const isActive = !["DONE", "COMPLETED", "DELIVERED"].includes(job.stage);
    const progress = job.progress || 0;

    return (
        <div
            onClick={onClick}
            style={{
                padding: 16,
                borderRadius: 8,
                border: isSelected ? "1px solid #528BFF" : "1px solid rgba(255,255,255,0.1)",
                background: isSelected ? "rgba(82,139,255,0.05)" : "rgba(255,255,255,0.02)",
                cursor: "pointer",
                transition: "all 0.2s",
            }}
        >
            <div style={{ display: "flex", gap: 16 }}>
                {/* Thumbnail */}
                <div
                    style={{
                        width: 120,
                        height: 68,
                        borderRadius: 4,
                        background: "rgba(0,0,0,0.3)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                    }}
                >
                    <Film size={24} style={{ color: "#71717a" }} />
                </div>

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, color: "#f5f5f5", marginBottom: 8 }}>
                        {job.file_stem.replace(/-\d{8}T\d{9}Z/, "")}
                    </div>

                    {/* Progress Bar */}
                    {isActive && (
                        <>
                            <div
                                style={{
                                    height: 4,
                                    borderRadius: 2,
                                    background: "rgba(255,255,255,0.1)",
                                    overflow: "hidden",
                                    marginBottom: 8,
                                }}
                            >
                                <div
                                    style={{
                                        width: `${progress}%`,
                                        height: "100%",
                                        background: "#528BFF",
                                        transition: "width 0.3s",
                                    }}
                                />
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <Loader2 size={12} className="animate-spin" style={{ color: "#528BFF" }} />
                                <span style={{ fontSize: 12, color: "#9ca3af" }}>
                                    {job.status || job.stage}
                                </span>
                            </div>
                        </>
                    )}

                    {!isActive && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <CheckCircle size={12} style={{ color: "#22c55e" }} />
                            <span style={{ fontSize: 12, color: "#22c55e" }}>Complete</span>
                        </div>
                    )}
                </div>

                {/* Actions */}
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    {job.stage === "DONE" && (
                        <>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    window.open(`/editor/${job.file_stem}`, "_blank");
                                }}
                                style={{
                                    padding: "8px 12px",
                                    borderRadius: 6,
                                    border: "1px solid rgba(255,255,255,0.1)",
                                    background: "transparent",
                                    color: "#d4d4d4",
                                    fontSize: 12,
                                    cursor: "pointer",
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 6,
                                }}
                            >
                                <Pencil size={14} />
                                Edit
                            </button>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    onAction("download_final", job.file_stem);
                                }}
                                style={{
                                    padding: "8px 12px",
                                    borderRadius: 6,
                                    border: "none",
                                    background: "#528BFF",
                                    color: "#fff",
                                    fontSize: 12,
                                    cursor: "pointer",
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 6,
                                }}
                            >
                                <Download size={14} />
                                Download
                            </button>
                        </>
                    )}
                    {job.stage !== "DONE" && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                if (confirm("Cancel this job?")) {
                                    onAction("cancel", job.file_stem);
                                }
                            }}
                            style={{
                                padding: "8px",
                                borderRadius: 6,
                                border: "1px solid rgba(255,255,255,0.1)",
                                background: "transparent",
                                color: "#ef4444",
                                fontSize: 12,
                                cursor: "pointer",
                            }}
                        >
                            <Trash2 size={14} />
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
