"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Badge from "@/components/common/Badge";
import Button from "@/components/common/Button";
import AddTrackModal from "@/components/common/AddTrackModal";
import Modal from "@/components/common/Modal";
import ProgressBar from "@/components/common/ProgressBar";
import TrackDetailPanel from "@/components/common/TrackDetailPanel";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore, Track } from "@/store/programs";

interface Props {
  programId: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

const WAVEFORM_BARS = [
  18, 32, 26, 40, 22, 36, 28, 48, 22, 30, 44, 20, 52, 28, 40, 24, 36, 30, 46, 22, 34, 26,
  42, 20, 36, 28, 48, 24, 38, 22, 44, 28, 40, 20,
];

const FLAG_MAP: Record<string, string> = {
  is: "🇮🇸",
  en: "🇬🇧",
  es: "🇪🇸",
  de: "🇩🇪",
  fr: "🇫🇷",
  pt: "🇵🇹",
  it: "🇮🇹",
  nl: "🇳🇱",
  sv: "🇸🇪",
  no: "🇳🇴",
  da: "🇩🇰",
  fi: "🇫🇮",
  pl: "🇵🇱",
  ru: "🇷🇺",
  ja: "🇯🇵",
  ko: "🇰🇷",
  zh: "🇨🇳",
  ar: "🇸🇦",
};

const stageVariant = (stage: string): "success" | "warning" | "info" | "error" => {
  switch (stage) {
    case "COMPLETE":
    case "DELIVERED":
      return "success";
    case "AWAITING_REVIEW":
    case "AWAITING_APPROVAL":
      return "warning";
    case "FAILED":
      return "error";
    default:
      return "info";
  }
};

const formatStage = (stage: string): string => {
  return stage
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
};

const formatDuration = (seconds?: number): string => {
  if (!seconds) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};

const stripExtension = (value?: string): string => {
  if (!value) return "";
  return value.replace(/\.[^/.]+$/, "");
};

// Extract client name from source path (e.g., /01_AUTO_PILOT/CBN/... → "CBN")
const extractClientFromPath = (path?: string): string | null => {
  if (!path) return null;
  // Common patterns: /..../CLIENT/..., or filename starts with client prefix like CBN, I2251
  const parts = path.split("/").filter(Boolean);

  // Look for known client patterns
  const knownClients = ["CBN", "TBN", "HOPE", "DAYSTAR", "GOD_TV", "BYU"];
  for (const part of parts) {
    const upper = part.toUpperCase();
    if (knownClients.some(c => upper.includes(c))) {
      return part.replace(/_/g, " ");
    }
  }

  // Fallback: parse from filename prefix like "CBNJD010126..."
  const filename = parts[parts.length - 1] || "";
  if (filename.startsWith("CBN")) return "CBN";
  if (filename.startsWith("TBN")) return "TBN";
  if (filename.startsWith("I225")) return "CBN Europe"; // I2251, I2252 pattern

  return null;
};

const trackLabel = (track: Track): string => {
  const typeLabel = track.type === "dub" ? "Dub" : "Sub";
  const flag = FLAG_MAP[track.language_code?.toLowerCase()] || "🌐";
  return `${flag} ${track.language_name} ${typeLabel}`;
};

const TranslationStepper = ({ track }: { track: Track }) => {
  const status = (track.status || "").toLowerCase();
  const progress = track.progress || 0;

  // Determine active step (1=Translate, 2=Chief, 3=Polish)
  let activeStep = 1;
  if (status.includes("polish") || progress >= 70) activeStep = 3;
  else if (status.includes("chief") || status.includes("review") || progress >= 60) activeStep = 2;

  // If complete/delivered/burning/reviewed, all translation steps are done
  if (["COMPLETE", "DELIVERED", "FINALIZED", "BURNING", "REVIEWED", "APPROVED"].includes(track.stage)) activeStep = 4;

  const steps = [
    { id: 1, label: "Translate", icon: "🌐" },
    { id: 2, label: "Chief Review", icon: "🕵️" },
    { id: 3, label: "Polish", icon: "✨" },
  ];

  return (
    <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(0,0,0,0.2)", borderRadius: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", position: "relative" }}>
        {/* Connecting Line */}
        <div style={{ position: "absolute", top: 12, left: 20, right: 20, height: 2, background: "rgba(255,255,255,0.1)", zIndex: 0 }} />

        {steps.map((step) => {
          const isActive = activeStep === step.id;
          const isDone = activeStep > step.id;
          return (
            <div key={step.id} style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{
                width: 24, height: 24, borderRadius: "50%",
                background: isDone ? "#22c55e" : isActive ? "#3b82f6" : "#27272a",
                border: `2px solid ${isActive ? "#60a5fa" : "transparent"}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, color: "#fff", transition: "all 0.3s ease"
              }}>
                {isDone ? "✓" : isActive ? <div className="animate-pulse w-2 h-2 bg-white rounded-full" /> : step.id}
              </div>
              <span style={{ fontSize: 10, fontWeight: isActive ? 600 : 400, color: isActive ? "#f5f5f5" : "#71717a" }}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>
      {activeStep < 4 && (
        <div style={{ marginTop: 8, textAlign: "center", fontSize: 11, color: "#a1a1aa" }}>
          <span className="animate-pulse">●</span> {track.status || "Processing..."}
        </div>
      )}
    </div>
  );
};

export default function ProgramDetailView({ programId }: Props) {
  const { clearSelection } = useNavigation();
  const router = useRouter();
  const { programs, fetchPrograms, sendTrackToReview, approveTrack } = useProgramsStore();
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [isAddTrackOpen, setIsAddTrackOpen] = useState(false);

  const program = programs.find((p) => p.id === programId);

  // Check if any track is in active/processing stage
  const hasActiveTrack = program?.tracks?.some(track =>
    ["BURNING", "FINALIZING", "INGESTING", "TRANSCRIBING", "TRANSLATING", "CLOUD_TRANSLATING", "CLOUD_EDITING", "CLOUD_POLISHING"].includes(track.stage)
  );

  useEffect(() => {
    if (!program) {
      fetchPrograms();
    }
  }, [program, fetchPrograms]);

  // Real-time polling during active stages
  useEffect(() => {
    if (!hasActiveTrack) return;

    const pollInterval = setInterval(() => {
      fetchPrograms();
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(pollInterval);
  }, [hasActiveTrack, fetchPrograms]);

  if (!program) {
    return (
      <Modal onClose={clearSelection}>
        <div className="program-detail">
          <div className="loading-spinner" />
        </div>
      </Modal>
    );
  }

  const tracks = program.tracks || [];
  const reviewTargets = tracks.filter((track) => track.stage === "AWAITING_REVIEW");
  const approvalTargets = tracks.filter((track) => track.stage === "AWAITING_APPROVAL");
  const failedTargets = tracks.filter((track) => track.stage === "FAILED" || track.status?.toLowerCase().includes("error"));

  const attentionCount = reviewTargets.length + approvalTargets.length + failedTargets.length;
  const needsAttention = program.needs_attention || failedTargets.length > 0;
  const attentionLabel = needsAttention ? "Needs Attention" : attentionCount > 0 ? "Needs Action" : "On Track";
  const attentionVariant = needsAttention ? "error" : attentionCount > 0 ? "warning" : "success";

  const avgProgress = tracks.length
    ? tracks.reduce((sum, track) => sum + (Number.isFinite(track.progress) ? track.progress : 0), 0) / tracks.length
    : 0;
  const progressPercent = Math.round(Math.min(100, Math.max(0, avgProgress)));
  const completedCount = tracks.filter((track) => ["COMPLETE", "DELIVERED"].includes(track.stage)).length;

  const primaryReview = reviewTargets[0];
  const primaryApproval = approvalTargets[0];
  const editorTarget =
    primaryReview?.job_id ||
    primaryApproval?.job_id ||
    tracks.find((track) => track.job_id)?.job_id ||
    stripExtension(program.original_filename) ||
    program.id;

  const actionQueue = [
    ...reviewTargets.map((track) => ({ track, action: "review" as const, label: "Send to Reviewer" })),
    ...approvalTargets.map((track) => ({ track, action: "approve" as const, label: "Approve Burn" })),
  ];

  const streamId = stripExtension(program.original_filename) || program.id;
  const posterUrl = program.thumbnail_path ? `${API_BASE}/api/v2/thumbnails/${program.id}` : undefined;

  const handleOpenEditor = () => {
    if (!editorTarget) return;
    router.push(`/editor/${encodeURIComponent(editorTarget)}`);
  };

  const handleSendToReview = async (trackId: string) => {
    setActionBusy(trackId);
    await sendTrackToReview(trackId);
    setActionBusy(null);
  };

  const handleApprove = async (trackId: string) => {
    setActionBusy(trackId);
    await approveTrack(trackId);
    setActionBusy(null);
  };

  return (
    <Modal onClose={clearSelection}>
      <div className="program-detail">
        <header className="detail-header">
          <div>
            <div className="detail-title">{program.title}</div>
            <div className="page-subtitle">
              {program.client && program.client !== "unknown"
                ? program.client
                : extractClientFromPath(program.video_path) || "Localization Overview"}
            </div>
          </div>
          <Button variant="ghost" onClick={clearSelection}>
            ← Back
          </Button>
        </header>

        <div className="detail-content">
          <section className="detail-main">
            <div className="detail-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Source Preview</div>
                  <div className="panel-subtitle">{program.original_filename || "Program Source"}</div>
                </div>
                <Badge label={attentionLabel} variant={attentionVariant} />
              </div>
              <div className="media-shell">
                {program.video_path ? (
                  <video
                    className="video-preview"
                    src={`${API_BASE}/api/stream/${streamId}`}
                    controls
                    poster={posterUrl}
                  />
                ) : (
                  <div className="video-preview-placeholder">
                    <span style={{ fontSize: "32px" }}>Preview</span>
                    <div>No video attached</div>
                  </div>
                )}
              </div>
            </div>

            <div className="detail-panel timeline-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Timeline</div>
                  <div className="panel-subtitle">Progress {progressPercent}%</div>
                </div>
                <div className="panel-subtitle">{formatDuration(program.duration_seconds)}</div>
              </div>
              <div className="timeline-track">
                <div className="timeline-progress" style={{ width: `${progressPercent}%` }} />
              </div>
              <div className="waveform">
                {WAVEFORM_BARS.map((height, index) => (
                  <span key={`${height}-${index}`} className="waveform-bar" style={{ height: `${height}%` }} />
                ))}
              </div>
              <div className="timeline-meta">
                <span>
                  {completedCount}/{tracks.length} delivered
                </span>
                <span>Avg progress {progressPercent}%</span>
              </div>
            </div>

            <div className="detail-panel">
              <div className="panel-title" style={{ marginBottom: "12px" }}>
                Program Details
              </div>
              <div className="info-grid">
                <div className="info-cell">
                  <span className="info-label">Client</span>
                  <span>{program.client && program.client !== "unknown"
                    ? program.client
                    : extractClientFromPath(program.video_path) || "—"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Duration</span>
                  <span>{formatDuration(program.duration_seconds)}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Due Date</span>
                  <span>{program.due_date || "—"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Style</span>
                  <span>{program.default_style || "Classic"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Tracks</span>
                  <span>{completedCount} delivered of {tracks.length}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Updated</span>
                  <span>{new Date(program.updated_at).toLocaleString()}</span>
                </div>
              </div>
            </div>
          </section>

          <aside className="detail-side">
            <div className="detail-panel action-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Command Center</div>
                  <div className="panel-subtitle">Next actions and escalations</div>
                </div>
                <Badge label={attentionLabel} variant={attentionVariant} />
              </div>

              <div className="action-stack">
                <Button variant="primary" onClick={handleOpenEditor} disabled={!editorTarget}>
                  Open Editor
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => primaryReview && handleSendToReview(primaryReview.id)}
                  disabled={!primaryReview || actionBusy === primaryReview.id}
                >
                  Send to Reviewer
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => primaryApproval && handleApprove(primaryApproval.id)}
                  disabled={!primaryApproval || actionBusy === primaryApproval.id}
                >
                  Approve Burn
                </Button>
              </div>

              <div className="action-metrics">
                <div className="metric-card">
                  <div className="metric-label">Needs Review</div>
                  <div className="metric-value">{reviewTargets.length}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Awaiting Approval</div>
                  <div className="metric-value">{approvalTargets.length}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Blocked</div>
                  <div className="metric-value">{failedTargets.length}</div>
                </div>
              </div>

              <div className="action-list">
                {actionQueue.length > 0 ? (
                  actionQueue.map((item) => (
                    <div key={`${item.action}-${item.track.id}`} className="action-item">
                      <div className="action-info">
                        <div className="action-title">{trackLabel(item.track)}</div>
                        <div className="action-subtitle">
                          {formatStage(item.track.stage)} • {item.track.status || "Queued"}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        onClick={() =>
                          item.action === "review"
                            ? handleSendToReview(item.track.id)
                            : handleApprove(item.track.id)
                        }
                        disabled={actionBusy === item.track.id}
                      >
                        {item.label}
                      </Button>
                    </div>
                  ))
                ) : (
                  <div className="empty-state">
                    <p>No pending review or approval.</p>
                  </div>
                )}
              </div>
            </div>

            <div className="detail-panel">
              <div className="panel-header" style={{ marginBottom: "6px" }}>
                <div className="panel-title">Output Tracks</div>
                <Button variant="ghost" onClick={() => setIsAddTrackOpen(true)}>
                  + Add Track
                </Button>
              </div>
              <div className="tracks-section">
                {tracks.length > 0 ? (
                  tracks.map((track) => <TrackCard key={track.id} track={track} />)
                ) : (
                  <div className="empty-state">
                    <p>No tracks yet</p>
                    <Button variant="primary" onClick={() => setIsAddTrackOpen(true)}>
                      Add Track
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </aside>
        </div>
        <AddTrackModal
          open={isAddTrackOpen}
          programId={program.id}
          onClose={() => setIsAddTrackOpen(false)}
        />
      </div>
    </Modal>
  );
}

interface TrackCardProps {
  track: Track;
}

function TrackCard({ track }: TrackCardProps) {
  const { startDubbing, recordDelivery, approveTrack } = useProgramsStore();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleStartDub = async () => {
    setBusy(true);
    await startDubbing(track.id);
    setBusy(false);
  };

  const handleDeliver = async () => {
    // Simple mock delivery flow for now
    const method = "folder";
    setBusy(true);
    await recordDelivery(track.id, method, "Client Folder", "Manual delivery via UI");
    setBusy(false);
  };

  const handleReburn = async () => {
    // Direct action for now to bypass browser automation issues with native confirm
    setBusy(true);
    await approveTrack(track.id);
    setBusy(false);
  };

  const isDubTrack = track.type === "dub";
  const canDub = isDubTrack && (track.stage === "QUEUED" || track.stage === "FAILED");
  const canDeliver = ["COMPLETE", "COMPLETED"].includes(track.stage);
  const canReburn = !isDubTrack && ["COMPLETE", "COMPLETED"].includes(track.stage);

  return (
    <div className="track-card">
      <div className="track-meta" onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        <div className="track-title">
          {trackLabel(track)}
          {track.voice_id && <span className="voice-label"> • Voice: {track.voice_id}</span>}
        </div>
        <div className="track-status">
          {formatStage(track.stage)}
          {track.status && track.status !== "Pending" && !["TRANSLATING_CLOUD", "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "CLOUD_POLISHING", "REVIEWED", "FINALIZING", "BURNING", "COMPLETE", "DELIVERED", "FINALIZED"].includes(track.stage) && (
            <span className="status-detail"> — {track.status}</span>
          )}
        </div>

        {/* Use Stepper for Cloud Translation Pipeline */}
        {["TRANSLATING_CLOUD", "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "CLOUD_POLISHING", "TRANSLATING_CLOUD_SUBMITTED", "REVIEWED", "FINALIZING", "BURNING", "COMPLETE", "DELIVERED", "FINALIZED", "APPROVED"].includes(track.stage) ? (
          <>
            <TranslationStepper track={track} />
            {/* Show Progress Bar for Burning/Finalizing below stepper */}
            {["BURNING", "FINALIZING"].includes(track.stage) && (
              <div style={{ marginTop: 8 }}>
                <ProgressBar value={track.progress} label={track.status || "Processing..."} />
              </div>
            )}
          </>
        ) : (
          <ProgressBar value={track.progress} label={`${track.language_name} progress`} />
        )}
      </div>
      <div className="track-actions">
        <Badge label={formatStage(track.stage)} variant={stageVariant(track.stage)} />

        {canDub && (
          <Button variant="secondary" onClick={handleStartDub} disabled={busy}>
            {busy ? "Starting..." : "Start Dub"}
          </Button>
        )}

        {canReburn && (
          <Button variant="secondary" onClick={handleReburn} disabled={busy} title="Regenerate verified video">
            {busy ? "Queuing..." : "Re-burn"}
          </Button>
        )}

        {canDeliver && (
          <Button variant="ghost" onClick={handleDeliver} disabled={busy}>
            {busy ? "Delivering..." : "Deliver"}
          </Button>
        )}
      </div>
      {expanded && (
        <TrackDetailPanel track={track} onClose={() => setExpanded(false)} />
      )}
    </div>
  );
}
