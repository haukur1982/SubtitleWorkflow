"use client";

import { useEffect, useMemo, useState } from "react";
import { Job } from "@/types/job";
import { UploadZone } from "@/components/UploadZone";
import { JobCard } from "@/components/JobCard";
import { WorkspaceShell, Sidebar, SidebarSection, SidebarItem, Inspector, InspectorSection, InspectorRow } from "@/components/layout";
import { LayoutGrid, List, Zap, Clock, AlertCircle, HardDrive, Activity, Play, CalendarClock } from "lucide-react";

const PHASES = ["Ingest", "Transcribe", "Translate", "Review", "Finalize", "Burn", "Deliver"] as const;
type PhaseLabel = typeof PHASES[number];

type HealthSnapshot = {
  storage_ready?: boolean;
  disk_free_gb?: number | null;
  heartbeats?: {
    omega_manager_age_seconds?: number | null;
    dashboard_age_seconds?: number | null;
  };
  jobs?: {
    total?: number;
    halted?: number;
    dead?: number;
    stages?: Record<string, number>;
  };
};

const stageToPhaseIndex = (stage: string) => {
  const value = stage.toUpperCase();
  if (["INGEST"].includes(value)) return 0;
  if (["TRANSCRIBED"].includes(value)) return 1;
  if (["TRANSLATING", "TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"].includes(value)) return 2;
  if (["TRANSLATED", "REVIEWING", "REVIEWED"].includes(value)) return 3;
  if (["FINALIZING", "FINALIZED"].includes(value)) return 4;
  if (["BURNING"].includes(value)) return 5;
  if (["COMPLETED"].includes(value)) return 6;
  return 0;
};

const jobNeedsAttention = (status: string) => {
  const value = status.toLowerCase();
  return value.includes("waiting") || value.includes("blocked") || value.includes("error") || value.includes("failed");
};

/**
 * PipelineWorkspace — Main dashboard view for job pipeline overview.
 * 
 * Data Flow:
 * - Fetches jobs from: GET /api/jobs
 * - Fetches health from: GET /api/health
 * - Job actions call: POST /api/action
 * - Selected job is displayed in Inspector panel
 */
export default function PipelineWorkspace() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [phaseFilter, setPhaseFilter] = useState<PhaseLabel | "All">("All");
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/jobs");
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      setJobs(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchHealth = async () => {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      setHealth(data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchJobs();
    fetchHealth();
    // Poll every 10 seconds
    const interval = setInterval(() => {
      fetchJobs();
      fetchHealth();
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  const phaseCounts = useMemo(() => {
    const counts: Record<PhaseLabel, number> = {
      Ingest: 0, Transcribe: 0, Translate: 0, Review: 0, Finalize: 0, Burn: 0, Deliver: 0,
    };
    jobs.forEach((job) => {
      const index = stageToPhaseIndex(job.stage || "");
      counts[PHASES[index]] += 1;
    });
    return counts;
  }, [jobs]);

  const attentionCount = useMemo(() => {
    return jobs.filter((job) => jobNeedsAttention(job.status || "")).length;
  }, [jobs]);

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (phaseFilter === "All") return true;
      const phaseIndex = stageToPhaseIndex(job.stage || "");
      return PHASES[phaseIndex] === phaseFilter;
    });
  }, [jobs, phaseFilter]);

  // Sidebar content
  const sidebarContent = (
    <Sidebar>
      <SidebarSection title="Pipeline Stages">
        <SidebarItem
          label="All Jobs"
          count={jobs.length}
          isActive={phaseFilter === "All"}
          onClick={() => setPhaseFilter("All")}
        />
        {PHASES.map((phase) => (
          <SidebarItem
            key={phase}
            label={phase}
            count={phaseCounts[phase]}
            isActive={phaseFilter === phase}
            onClick={() => setPhaseFilter(phase)}
          />
        ))}
      </SidebarSection>
      <SidebarSection title="System">
        <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 12 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9ca3af' }}>
              <HardDrive style={{ width: 14, height: 14 }} /> Storage
            </span>
            <span style={{ fontWeight: 500, color: health?.storage_ready ? '#22c55e' : '#ef4444' }}>
              {health?.storage_ready ? "OK" : "—"}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 12 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9ca3af' }}>
              <Activity style={{ width: 14, height: 14 }} /> Latency
            </span>
            <span style={{ fontWeight: 500, color: '#f5f5f5', fontVariantNumeric: 'tabular-nums' }}>
              {health?.heartbeats?.omega_manager_age_seconds?.toFixed(0) || "—"}s
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 12 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9ca3af' }}>
              <AlertCircle style={{ width: 14, height: 14 }} /> Attention
            </span>
            <span style={{ fontWeight: 500, color: attentionCount ? '#f59e0b' : '#22c55e' }}>
              {attentionCount}
            </span>
          </div>
        </div>
      </SidebarSection>
    </Sidebar>
  );

  // Inspector content (shows selected job details)
  const inspectorContent = selectedJob ? (
    <Inspector title="Job Details">
      <InspectorSection title="Info">
        <InspectorRow label="File">{selectedJob.file_stem}</InspectorRow>
        <InspectorRow label="Client">{selectedJob.client || "—"}</InspectorRow>
        <InspectorRow label="Language">{selectedJob.target_language || selectedJob.meta?.target_language || "—"}</InspectorRow>
        <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
      </InspectorSection>
      <InspectorSection title="Status">
        <div className="text-[11px] text-[#9ca3af] leading-relaxed">
          {selectedJob.status || "Processing..."}
        </div>
      </InspectorSection>
      <InspectorSection title="Timeline">
        <div className="space-y-1">
          {PHASES.map((phase, i) => {
            const jobPhaseIndex = stageToPhaseIndex(selectedJob.stage || "");
            const isDone = i < jobPhaseIndex;
            const isCurrent = i === jobPhaseIndex;
            return (
              <div key={phase} className="flex items-center gap-2 text-[11px]">
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] ${isDone ? "bg-[#22c55e] text-white" : isCurrent ? "bg-[#528BFF] text-white" : "bg-[rgba(255,255,255,0.06)] text-[#6b7280]"
                  }`}>
                  {isDone ? "✓" : i + 1}
                </span>
                <span className={isDone || isCurrent ? "text-[#f5f5f5]" : "text-[#6b7280]"}>{phase}</span>
              </div>
            );
          })}
        </div>
      </InspectorSection>
      <InspectorSection title="Actions">
        <div className="flex flex-wrap gap-1.5">
          <a
            href={`/edit/${selectedJob.file_stem}`}
            className="btn btn-primary text-[10px] py-1 px-2"
          >
            <Play className="w-3 h-3" /> Edit
          </a>
        </div>
      </InspectorSection>
    </Inspector>
  ) : (
    <Inspector title="Inspector">
      <div className="p-4 text-center text-[11px] text-[#6b7280]">
        Select a job to view details
      </div>
    </Inspector>
  );

  return (
    <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
      {/* Toolbar */}
      <div className="h-10 flex items-center justify-between px-4 border-b border-[rgba(255,255,255,0.06)] bg-[#1a1a1c]">
        <div className="flex items-center gap-2">
          <span className="text-[12px] text-[#9ca3af]">
            {filteredJobs.length} jobs
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* View Toggle */}
          <div className="flex items-center rounded-md border border-[rgba(255,255,255,0.06)] overflow-hidden">
            <button
              onClick={() => setView("grid")}
              className={`p-1.5 transition ${view === "grid" ? "bg-[rgba(255,255,255,0.06)] text-[#f5f5f5]" : "text-[#6b7280] hover:text-[#f5f5f5]"}`}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setView("list")}
              className={`p-1.5 transition ${view === "list" ? "bg-[rgba(255,255,255,0.06)] text-[#f5f5f5]" : "text-[#6b7280] hover:text-[#f5f5f5]"}`}
            >
              <List className="w-3.5 h-3.5" />
            </button>
          </div>
          <UploadZone onUploadComplete={fetchJobs} />
        </div>
      </div>

      {/* Content */}
      <div className="p-4">
        {loading && jobs.length === 0 ? (
          <div className={view === "grid" ? "grid gap-3 md:grid-cols-2 xl:grid-cols-3" : "space-y-2"}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="card h-28 pulse-soft" />
            ))}
          </div>
        ) : filteredJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-center">
            <Zap className="w-8 h-8 text-[#6b7280] mb-2" />
            <p className="text-[#9ca3af] text-[13px]">No jobs found</p>
            <p className="text-[#6b7280] text-[12px]">Adjust filters or import media</p>
          </div>
        ) : (
          <div className={view === "grid" ? "grid gap-3 md:grid-cols-2 xl:grid-cols-3" : "space-y-2"}>
            {filteredJobs.map((job) => (
              <div key={job.file_stem} onClick={() => setSelectedJob(job)}>
                <JobCard
                  job={job}
                  view={view}
                  onRefresh={fetchJobs}
                  isSelected={selectedJob?.file_stem === job.file_stem}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </WorkspaceShell>
  );
}
