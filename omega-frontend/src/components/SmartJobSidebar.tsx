"use client";

import { useState, ReactNode } from "react";
import { AlertCircle, Clock, CheckCircle2, Inbox, ChevronDown, ChevronRight } from "lucide-react";
import { useOmegaStore, Job, getJobGroup, JobGroup, getStageLabel } from "@/store/omega";
import { CloudStageBadge } from "@/components/TranslationProgress";

// =============================================================================
// Group Metadata
// =============================================================================

const GROUP_META: Record<JobGroup, { label: string; icon: ReactNode; color: string; emptyText: string }> = {
    attention: {
        label: "Needs Attention",
        icon: <AlertCircle style={{ width: 14, height: 14 }} />,
        color: "#ef4444",
        emptyText: "No issues to resolve",
    },
    active: {
        label: "Active",
        icon: <Clock style={{ width: 14, height: 14 }} />,
        color: "#3b82f6",
        emptyText: "No jobs in progress",
    },
    queued: {
        label: "Queued",
        icon: <Inbox style={{ width: 14, height: 14 }} />,
        color: "#9ca3af",
        emptyText: "Queue is empty",
    },
    completed: {
        label: "Completed Today",
        icon: <CheckCircle2 style={{ width: 14, height: 14 }} />,
        color: "#22c55e",
        emptyText: "No completed jobs",
    },
    archive: {
        label: "Delivered",
        icon: <CheckCircle2 style={{ width: 14, height: 14 }} />,
        color: "#6b7280",
        emptyText: "",
    },
};

// =============================================================================
// JobGroupSection Component
// =============================================================================

interface JobGroupSectionProps {
    group: JobGroup;
    jobs: Job[];
    selectedJobId: string | null;
    onSelectJob: (fileStem: string) => void;
    defaultExpanded?: boolean;
}

function JobGroupSection({ group, jobs, selectedJobId, onSelectJob, defaultExpanded = true }: JobGroupSectionProps) {
    const [expanded, setExpanded] = useState(defaultExpanded);
    const meta = GROUP_META[group];

    // Don't show empty archive section
    if (group === "archive" && jobs.length === 0) {
        return null;
    }

    return (
        <div style={{ marginBottom: 8 }}>
            {/* Header */}
            <button
                onClick={() => setExpanded(!expanded)}
                style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "10px 16px",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    color: meta.color,
                }}
            >
                {expanded ? <ChevronDown style={{ width: 12, height: 12 }} /> : <ChevronRight style={{ width: 12, height: 12 }} />}
                {meta.icon}
                <span style={{ flex: 1, textAlign: "left", fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                    {meta.label}
                </span>
                <span
                    style={{
                        fontSize: 11,
                        fontWeight: 600,
                        fontVariantNumeric: "tabular-nums",
                        padding: "2px 8px",
                        borderRadius: 10,
                        background: jobs.length > 0 ? `${meta.color}20` : "transparent",
                        color: meta.color,
                    }}
                >
                    {jobs.length}
                </span>
            </button>

            {/* Job List */}
            {expanded && (
                <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "0 8px" }}>
                    {jobs.length === 0 ? (
                        <div style={{ padding: "8px 16px", fontSize: 11, color: "#6b7280", fontStyle: "italic" }}>
                            {meta.emptyText}
                        </div>
                    ) : (
                        jobs.map((job) => (
                            <JobListItem
                                key={job.file_stem}
                                job={job}
                                isSelected={selectedJobId === job.file_stem}
                                onClick={() => onSelectJob(job.file_stem)}
                            />
                        ))
                    )}
                </div>
            )}
        </div>
    );
}

// =============================================================================
// JobListItem Component
// =============================================================================

interface JobListItemProps {
    job: Job;
    isSelected: boolean;
    onClick: () => void;
}

function JobListItem({ job, isSelected, onClick }: JobListItemProps) {
    const group = getJobGroup(job);
    const isError = group === "attention";

    return (
        <button
            onClick={onClick}
            style={{
                width: "100%",
                display: "flex",
                flexDirection: "column",
                gap: 4,
                padding: "10px 12px",
                borderRadius: 8,
                border: "none",
                cursor: "pointer",
                textAlign: "left",
                background: isSelected ? "rgba(82, 139, 255, 0.12)" : "transparent",
                transition: "background 0.15s",
            }}
            onMouseOver={(e) => { if (!isSelected) e.currentTarget.style.background = "rgba(255,255,255,0.04)"; }}
            onMouseOut={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
        >
            {/* Name */}
            <span
                style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: isError ? "#ef4444" : isSelected ? "#528BFF" : "#f5f5f5",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                }}
            >
                {job.file_stem}
            </span>

            {/* Status Row */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 11, color: "#6b7280" }}>
                    {getStageLabel(job.stage)}
                </span>
                <CloudStageBadge job={job} />
            </div>

            {/* Progress Bar (if applicable) */}
            {job.progress > 0 && job.progress < 100 && (
                <div
                    style={{
                        height: 3,
                        background: "rgba(255,255,255,0.1)",
                        borderRadius: 2,
                        overflow: "hidden",
                        marginTop: 4,
                    }}
                >
                    <div
                        style={{
                            height: "100%",
                            width: `${job.progress}%`,
                            background: isError ? "#ef4444" : "#528BFF",
                            borderRadius: 2,
                            transition: "width 0.3s ease",
                        }}
                    />
                </div>
            )}
        </button>
    );
}

// =============================================================================
// SmartJobSidebar - Main Export
// =============================================================================

interface SmartJobSidebarProps {
    showArchive?: boolean;
}

/**
 * Smart job sidebar with grouped, collapsible sections.
 * Groups: Needs Attention → Active → Queued → Completed → Archive
 */
export function SmartJobSidebar({ showArchive = false }: SmartJobSidebarProps) {
    const jobs = useOmegaStore((s) => s.jobs);
    const selectedJobId = useOmegaStore((s) => s.selectedJobId);
    const selectJob = useOmegaStore((s) => s.selectJob);

    // Group jobs
    const grouped: Record<JobGroup, Job[]> = {
        attention: [],
        active: [],
        queued: [],
        completed: [],
        archive: [],
    };

    jobs.forEach((job) => {
        const group = getJobGroup(job);
        grouped[group].push(job);
    });

    // Sort each group by updated_at (most recent first)
    Object.values(grouped).forEach((arr) => {
        arr.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    });

    // Filter completed to "today" only
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    grouped.completed = grouped.completed.filter((job) => {
        const updated = new Date(job.updated_at);
        return updated >= today;
    });

    return (
        <div style={{ display: "flex", flexDirection: "column", paddingTop: 8 }}>
            <JobGroupSection
                group="attention"
                jobs={grouped.attention}
                selectedJobId={selectedJobId}
                onSelectJob={selectJob}
                defaultExpanded={true}
            />
            <JobGroupSection
                group="active"
                jobs={grouped.active}
                selectedJobId={selectedJobId}
                onSelectJob={selectJob}
                defaultExpanded={true}
            />
            <JobGroupSection
                group="queued"
                jobs={grouped.queued}
                selectedJobId={selectedJobId}
                onSelectJob={selectJob}
                defaultExpanded={grouped.queued.length > 0}
            />
            <JobGroupSection
                group="completed"
                jobs={grouped.completed}
                selectedJobId={selectedJobId}
                onSelectJob={selectJob}
                defaultExpanded={grouped.completed.length > 0 && grouped.completed.length <= 5}
            />
            {showArchive && (
                <JobGroupSection
                    group="archive"
                    jobs={grouped.archive}
                    selectedJobId={selectedJobId}
                    onSelectJob={selectJob}
                    defaultExpanded={false}
                />
            )}
        </div>
    );
}
