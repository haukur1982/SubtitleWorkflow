"use client";

import { create } from "zustand";

// =============================================================================
// Types
// =============================================================================

export type WorkspaceId = "media" | "pipeline" | "edit" | "settings" | "deliver" | "monitor";

export interface Job {
    file_stem: string;
    stage: string;
    status: string;
    progress: number;
    updated_at: string;
    target_language?: string;
    program_profile?: string;
    subtitle_style?: string;
    client?: string;
    due_date?: string;
    editor_report?: string;
    meta?: {
        cloud_stage?: string;
        cloud_progress?: {
            stage?: string;
            status?: string;
            progress?: number;
            updated_at?: string;
            segments_done?: number;
            segments_total?: number;
        };
        stage_timeline?: Array<{
            stage: string;
            started_at: string;
            ended_at?: string;
        }>;
        halted?: boolean;
        [key: string]: unknown;
    };
}

export interface HealthData {
    storage_ready: boolean;
    cloud_enabled: boolean;
    heartbeats: {
        omega_manager_age_seconds?: number;
        dashboard_age_seconds?: number;
    };
    disk_free_gb?: number;
    active_jobs?: number;
}

// =============================================================================
// Helper: Human-readable status labels
// =============================================================================

export const STAGE_LABELS: Record<string, string> = {
    QUEUED: "Queued",
    INGEST: "Ingesting...",
    TRANSCRIBED: "Transcript Ready",
    TRANSLATING: "Translating...",
    TRANSLATING_CLOUD_SUBMITTED: "Translating...",
    CLOUD_TRANSLATING: "Lead Translator",
    CLOUD_REVIEWING: "AI Review in Progress",
    CLOUD_POLISHING: "Senior Polish",
    CLOUD_DONE: "Translation Complete",
    TRANSLATED: "Translated",
    REVIEWING: "Reviewing...",
    REVIEWED: "Reviewed",
    FINALIZING: "Finalizing...",
    FINALIZED: "Ready to Burn",
    BURNING: "Burning...",
    COMPLETED: "Done ✓",
    DONE: "Done ✓",
};

export function getStageLabel(stage: string): string {
    return STAGE_LABELS[stage?.toUpperCase()] || stage;
}

// =============================================================================
// Helper: Job grouping
// =============================================================================

export type JobGroup = "attention" | "active" | "queued" | "completed" | "archive";

export function getJobGroup(job: Job): JobGroup {
    const stage = (job.stage || "").toUpperCase();
    const status = (job.status || "").toLowerCase();
    const isHalted = job.meta?.halted;

    // Needs attention: errors, halted, stalled
    if (isHalted || status.includes("error") || status.includes("fail") || status.includes("blocked")) {
        return "attention";
    }

    // Completed
    if (stage === "COMPLETED" || stage === "DONE") {
        // Check if delivered
        if (status === "delivered") {
            return "archive";
        }
        return "completed";
    }

    // Queued / waiting
    if (stage === "QUEUED" || status.includes("waiting")) {
        return "queued";
    }

    // Active (everything else)
    return "active";
}

// =============================================================================
// Store
// =============================================================================

interface OmegaState {
    // Data (jobs as array for SSR compatibility)
    jobs: Job[];
    health: HealthData | null;

    // UI State
    activeWorkspace: WorkspaceId;
    selectedJobId: string | null;
    sidebarFilter: string;
    isConnected: boolean;

    // Computed (derived in selectors)

    // Actions
    setJobs: (jobs: Job[]) => void;
    updateJob: (job: Job) => void;
    removeJob: (fileStem: string) => void;
    setHealth: (health: HealthData) => void;

    setActiveWorkspace: (ws: WorkspaceId) => void;
    selectJob: (fileStem: string | null) => void;
    setSidebarFilter: (filter: string) => void;
    setConnected: (connected: boolean) => void;

    // Refresh from API (initial load / fallback)
    refreshJobs: () => Promise<void>;
    refreshHealth: () => Promise<void>;
}

export const useOmegaStore = create<OmegaState>((set, get) => ({
    // Initial state
    jobs: [],
    health: null,
    activeWorkspace: "pipeline",
    selectedJobId: null,
    sidebarFilter: "all",
    isConnected: false,

    // Data actions
    setJobs: (jobs) => {
        set({ jobs });
    },

    updateJob: (job) => {
        const jobs = get().jobs;
        const idx = jobs.findIndex(j => j.file_stem === job.file_stem);
        if (idx >= 0) {
            const updated = [...jobs];
            updated[idx] = job;
            set({ jobs: updated });
        } else {
            set({ jobs: [...jobs, job] });
        }
    },

    removeJob: (fileStem) => {
        set({ jobs: get().jobs.filter(j => j.file_stem !== fileStem) });
    },

    setHealth: (health) => set({ health }),

    // UI actions
    setActiveWorkspace: (ws) => {
        set({ activeWorkspace: ws });
        // Update URL without navigation
        const href = ws === "pipeline" ? "/" : `/${ws}`;
        if (typeof window !== "undefined") {
            window.history.replaceState(null, "", href);
        }
    },

    selectJob: (fileStem) => set({ selectedJobId: fileStem }),

    setSidebarFilter: (filter) => set({ sidebarFilter: filter }),

    setConnected: (connected) => set({ isConnected: connected }),

    // API refresh (fallback / initial load)
    refreshJobs: async () => {
        try {
            const res = await fetch("/api/jobs");
            if (!res.ok) throw new Error("Failed to fetch jobs");
            const data = await res.json();
            get().setJobs(data);
        } catch (e) {
            console.error("Failed to refresh jobs:", e);
        }
    },

    refreshHealth: async () => {
        try {
            const res = await fetch("/api/health");
            if (!res.ok) throw new Error("Failed to fetch health");
            const data = await res.json();
            get().setHealth(data);
        } catch (e) {
            console.error("Failed to refresh health:", e);
        }
    },
}));

// =============================================================================
// Selectors (for efficient re-renders)
// =============================================================================

export const useJobs = () => useOmegaStore((s) => s.jobs);

export const useJobsGrouped = () => {
    const jobs = useOmegaStore((s) => s.jobs);

    const groups: Record<JobGroup, Job[]> = {
        attention: [],
        active: [],
        queued: [],
        completed: [],
        archive: [],
    };

    jobs.forEach((job) => {
        const group = getJobGroup(job);
        groups[group].push(job);
    });

    // Sort each group by updated_at (most recent first)
    Object.values(groups).forEach((arr) => {
        arr.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    });

    return groups;
};

export const useSelectedJob = () => {
    const selectedId = useOmegaStore((s) => s.selectedJobId);
    const jobs = useOmegaStore((s) => s.jobs);
    return selectedId ? jobs.find(j => j.file_stem === selectedId) || null : null;
};

export const useHealth = () => useOmegaStore((s) => s.health);

export const useActiveWorkspace = () => useOmegaStore((s) => s.activeWorkspace);

export const useIsConnected = () => useOmegaStore((s) => s.isConnected);
