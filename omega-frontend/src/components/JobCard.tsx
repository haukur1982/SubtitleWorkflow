import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, CalendarClock, CheckCircle2, Clock, Flame, Play, ExternalLink } from "lucide-react";
import { motion } from "framer-motion";
import { Job, parseEditorReport } from "@/types/job";
import { JobActionMenu } from "@/components/JobActionMenu";

const PHASES = ["Ingest", "Transcribe", "Translate", "Review", "Finalize", "Burn", "Deliver"];

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

const getStatusStyle = (status: string, stage: string) => {
  const statusLower = status.toLowerCase();
  const stageUpper = stage.toUpperCase();
  if (stageUpper === "COMPLETED" || statusLower === "done") {
    return { bg: "bg-[rgba(34,197,94,0.1)]", text: "text-green", dot: "bg-green" };
  }
  if (statusLower.includes("waiting") || statusLower.includes("blocked")) {
    return { bg: "bg-[rgba(245,158,11,0.1)]", text: "text-amber", dot: "bg-amber" };
  }
  if (statusLower.includes("error") || statusLower.includes("failed") || stageUpper === "DEAD") {
    return { bg: "bg-[rgba(239,68,68,0.1)]", text: "text-red", dot: "bg-red" };
  }
  return { bg: "bg-[rgba(82,139,255,0.1)]", text: "text-blue", dot: "bg-blue" };
};

const needsAttention = (status: string) => {
  const value = status.toLowerCase();
  return value.includes("waiting") || value.includes("blocked") || value.includes("error") || value.includes("failed");
};

interface JobCardProps {
  job: Job;
  view?: "grid" | "list";
  onRefresh?: () => void;
  isSelected?: boolean;
}

export function JobCard({ job, view = "grid", onRefresh, isSelected = false }: JobCardProps) {
  const report = parseEditorReport(job.editor_report);
  const phaseIndex = stageToPhaseIndex(job.stage || "");
  const style = getStatusStyle(job.status || "", job.stage || "");
  const isWaiting = needsAttention(job.status || "");
  const statusLower = (job.status || "").toLowerCase();
  const dueDate = job.due_date ? new Date(job.due_date) : null;
  const dueSoon = dueDate ? dueDate.getTime() - Date.now() < 1000 * 60 * 60 * 48 : false;
  const overdue = dueDate ? dueDate.getTime() < Date.now() : false;

  const [approvingBurn, setApprovingBurn] = useState(false);
  const awaitingBurn = statusLower.includes("waiting for burn approval");
  const reviewUrl = job.meta?.remote_review_url || job.meta?.review_url;

  const handleApproveBurn = async () => {
    if (approvingBurn) return;
    setApprovingBurn(true);
    try {
      await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "approve_burn", file_stem: job.file_stem }),
      });
      onRefresh?.();
    } finally {
      setApprovingBurn(false);
    }
  };

  const phaseProgress = Math.round((phaseIndex / (PHASES.length - 1)) * 100);
  const statusText = job.status || job.stage || "Processing";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`card p-3 cursor-pointer ${isSelected ? "ring-1 ring-[#528BFF] border-[#528BFF]/50" : ""}`}
    >
      {/* Row 1: Phase + Status */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-[11px] text-muted font-medium">{PHASES[phaseIndex]}</span>
        <div className="flex items-center gap-1.5">
          <span className={`pill ${style.bg} ${style.text}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`}></span>
            {statusText.length > 18 ? statusText.slice(0, 18) + "…" : statusText}
          </span>
          <JobActionMenu fileStem={job.file_stem} onActionComplete={() => onRefresh?.()} />
        </div>
      </div>

      {/* Row 2: Title */}
      <h3 className="text-[13px] font-medium text-primary truncate mb-2" title={job.file_stem}>
        {job.file_stem}
      </h3>

      {/* Row 3: Progress */}
      <div className="mb-2">
        <div className="progress-track">
          <motion.div
            className={`progress-fill ${style.dot}`}
            initial={{ width: 0 }}
            animate={{ width: `${phaseProgress}%` }}
            transition={{ duration: 0.4, ease: "easeOut" }}
          />
        </div>
        <div className="flex items-center justify-between mt-1 text-[10px] text-muted">
          <span>{phaseProgress}%</span>
          <span className="flex items-center gap-1 tabular-nums">
            <Clock className="w-3 h-3" />
            {new Date(job.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      </div>

      {/* Row 4: Meta */}
      <div className="flex items-center gap-1.5 text-[10px] mb-2">
        <span className="pill py-0.5">
          {(job.target_language || job.meta?.target_language || "UNK").toUpperCase()}
        </span>
        {job.client && <span className="pill py-0.5">{job.client}</span>}
        {report && <span className="pill py-0.5">AI: {report.rating}/10</span>}
        {dueDate && (
          <span className={`pill py-0.5 ${overdue ? "bg-[rgba(239,68,68,0.1)] text-red" : dueSoon ? "bg-[rgba(245,158,11,0.1)] text-amber" : ""}`}>
            <CalendarClock className="w-2.5 h-2.5" />
            {dueDate.toLocaleDateString()}
          </span>
        )}
      </div>

      {/* Row 5: Actions */}
      <div className="flex items-center gap-1.5">
        <Link href={`/editor/${job.file_stem}`} className="btn btn-primary text-[11px] py-1 px-2.5">
          <Play className="w-3 h-3" /> Edit
        </Link>
        {reviewUrl && (
          <a href={reviewUrl} className="btn btn-secondary text-[11px] py-1 px-2.5" target="_blank" rel="noreferrer">
            <ExternalLink className="w-3 h-3" /> Review
          </a>
        )}
        {awaitingBurn && (
          <button
            onClick={handleApproveBurn}
            className="btn text-[11px] py-1 px-2.5 bg-[rgba(34,197,94,0.1)] text-green border-[rgba(34,197,94,0.2)]"
            disabled={approvingBurn}
          >
            <CheckCircle2 className="w-3 h-3" /> Approve
          </button>
        )}
        {isWaiting && !awaitingBurn && (
          <span className="pill bg-[rgba(245,158,11,0.1)] text-amber">
            <AlertTriangle className="w-3 h-3" /> Attention
          </span>
        )}
        {statusLower.includes("burning") && (
          <span className="pill">
            <Flame className="w-3 h-3 text-amber" /> Encoding
          </span>
        )}
      </div>
    </motion.div>
  );
}
