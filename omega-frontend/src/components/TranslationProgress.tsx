"use client";

import { Job } from "@/store/omega";

// =============================================================================
// Translation Phase Labels
// =============================================================================

const TRANSLATION_PHASES = [
    { key: "CLOUD_TRANSLATING", label: "Lead Translator", emoji: "🌐" },
    { key: "CLOUD_REVIEWING", label: "Chief Editor", emoji: "✏️" },
    { key: "CLOUD_POLISHING", label: "Senior Polish", emoji: "✨" },
    { key: "CLOUD_DONE", label: "Complete", emoji: "✅" },
] as const;

function getPhaseIndex(stage: string | undefined): number {
    if (!stage) return -1;
    const idx = TRANSLATION_PHASES.findIndex((p) => p.key === stage.toUpperCase());
    return idx;
}

function getProgressPercent(cloudProgress: Job["meta"]): number {
    if (!cloudProgress) return 0;
    const progress = cloudProgress.cloud_progress;
    if (progress?.progress !== undefined) {
        return Math.min(100, Math.max(0, progress.progress));
    }
    return 0;
}

// =============================================================================
// TranslationProgress Component
// =============================================================================

interface TranslationProgressProps {
    job: Job;
    compact?: boolean;
}

/**
 * Displays the current AI translation phase with progress.
 * Shows: Lead Translator → Chief Editor → Senior Polish → Complete
 */
export function TranslationProgress({ job, compact = false }: TranslationProgressProps) {
    const meta = job.meta;
    const cloudStage = meta?.cloud_stage?.toUpperCase();
    const cloudProgress = meta?.cloud_progress;

    // Only show for jobs in translation phases
    const stage = job.stage?.toUpperCase() || "";
    const isTranslating = stage.includes("TRANSLAT") || stage.includes("CLOUD") || stage.includes("REVIEW");

    if (!isTranslating && cloudStage !== "CLOUD_DONE") {
        return null;
    }

    const currentPhaseIdx = getPhaseIndex(cloudStage);
    const segmentsDone = cloudProgress?.segments_done;
    const segmentsTotal = cloudProgress?.segments_total;
    const progressPercent = getProgressPercent(meta);

    if (compact) {
        // Compact: Single line with current phase
        const currentPhase = currentPhaseIdx >= 0 ? TRANSLATION_PHASES[currentPhaseIdx] : null;
        return (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                {currentPhase && (
                    <>
                        <span>{currentPhase.emoji}</span>
                        <span style={{ color: "#9ca3af" }}>{currentPhase.label}</span>
                        {segmentsDone !== undefined && segmentsTotal !== undefined && (
                            <span style={{ color: "#6b7280", fontVariantNumeric: "tabular-nums" }}>
                                {segmentsDone}/{segmentsTotal}
                            </span>
                        )}
                    </>
                )}
            </div>
        );
    }

    // Full: Phase steps with progress bar
    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12, background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                🧠 AI Translation Progress
            </div>

            {/* Phase Steps */}
            <div style={{ display: "flex", gap: 4 }}>
                {TRANSLATION_PHASES.map((phase, idx) => {
                    const isComplete = currentPhaseIdx > idx;
                    const isCurrent = currentPhaseIdx === idx;
                    const isPending = currentPhaseIdx < idx;

                    return (
                        <div
                            key={phase.key}
                            style={{
                                flex: 1,
                                display: "flex",
                                flexDirection: "column",
                                alignItems: "center",
                                gap: 4,
                            }}
                        >
                            {/* Dot/Icon */}
                            <div
                                style={{
                                    width: 24,
                                    height: 24,
                                    borderRadius: "50%",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    fontSize: 12,
                                    background: isComplete
                                        ? "rgba(34, 197, 94, 0.2)"
                                        : isCurrent
                                            ? "rgba(82, 139, 255, 0.2)"
                                            : "rgba(255,255,255,0.05)",
                                    color: isComplete
                                        ? "#22c55e"
                                        : isCurrent
                                            ? "#528BFF"
                                            : "#6b7280",
                                    border: isCurrent ? "2px solid #528BFF" : "none",
                                }}
                            >
                                {isComplete ? "✓" : phase.emoji}
                            </div>

                            {/* Label */}
                            <span
                                style={{
                                    fontSize: 10,
                                    color: isComplete ? "#22c55e" : isCurrent ? "#f5f5f5" : "#6b7280",
                                    textAlign: "center",
                                }}
                            >
                                {phase.label}
                            </span>
                        </div>
                    );
                })}
            </div>

            {/* Progress Bar */}
            {currentPhaseIdx >= 0 && currentPhaseIdx < TRANSLATION_PHASES.length - 1 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <div
                        style={{
                            height: 4,
                            background: "rgba(255,255,255,0.1)",
                            borderRadius: 2,
                            overflow: "hidden",
                        }}
                    >
                        <div
                            style={{
                                height: "100%",
                                width: `${progressPercent}%`,
                                background: "linear-gradient(90deg, #528BFF, #22c55e)",
                                borderRadius: 2,
                                transition: "width 0.3s ease",
                            }}
                        />
                    </div>

                    {/* Segment Count */}
                    {segmentsDone !== undefined && segmentsTotal !== undefined && (
                        <div style={{ fontSize: 11, color: "#9ca3af", textAlign: "center" }}>
                            {segmentsDone} / {segmentsTotal} segments
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// =============================================================================
// Cloud Stage Badge (for job lists)
// =============================================================================

interface CloudStageBadgeProps {
    job: Job;
}

/**
 * Compact badge showing cloud translation stage.
 */
export function CloudStageBadge({ job }: CloudStageBadgeProps) {
    const cloudStage = job.meta?.cloud_stage?.toUpperCase();

    if (!cloudStage) return null;

    const phase = TRANSLATION_PHASES.find((p) => p.key === cloudStage);
    if (!phase) return null;

    const isComplete = cloudStage === "CLOUD_DONE";

    return (
        <span
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 8px",
                borderRadius: 12,
                fontSize: 11,
                fontWeight: 500,
                background: isComplete ? "rgba(34, 197, 94, 0.15)" : "rgba(82, 139, 255, 0.15)",
                color: isComplete ? "#22c55e" : "#528BFF",
            }}
        >
            {phase.emoji} {phase.label}
        </span>
    );
}
