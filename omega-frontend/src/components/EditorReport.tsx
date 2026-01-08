"use client";

import { Job } from "@/store/omega";

// =============================================================================
// EditorReport Component - Displays AI quality feedback
// =============================================================================

interface EditorReportData {
    summary?: string;
    quality_score?: number;
    rating?: number;
    quality_tier?: string;
    notes?: string[];
    corrections_count?: number;
    flagged_segments?: Array<{
        id: number;
        reason: string;
        severity?: "low" | "medium" | "high";
    }>;
    [key: string]: unknown;
}

function parseEditorReport(job: Job): EditorReportData | null {
    const raw = job.editor_report;
    if (!raw) return null;

    try {
        const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
        return parsed as EditorReportData;
    } catch {
        return null;
    }
}

function getQualityColor(score: number): string {
    if (score >= 9) return "#22c55e"; // Excellent - green
    if (score >= 7) return "#3b82f6"; // Good - blue
    if (score >= 5) return "#f59e0b"; // Fair - amber
    return "#ef4444"; // Needs work - red
}

function getQualityLabel(score: number): string {
    if (score >= 9) return "Excellent";
    if (score >= 8) return "Broadcast Ready";
    if (score >= 7) return "Good";
    if (score >= 5) return "Fair";
    return "Needs Review";
}

interface EditorReportProps {
    job: Job;
    compact?: boolean;
}

/**
 * Displays the AI editor quality report with rating, notes, and flagged segments.
 */
export function EditorReport({ job, compact = false }: EditorReportProps) {
    const report = parseEditorReport(job);

    if (!report) return null;

    const score = report.quality_score ?? report.rating;
    const tier = report.quality_tier;
    const summary = report.summary;
    const notes = report.notes;
    const flaggedCount = report.flagged_segments?.length || 0;
    const correctionsCount = report.corrections_count;

    // If nothing to show, return null
    if (!score && !summary && !notes?.length && !flaggedCount) {
        return null;
    }

    if (compact) {
        // Compact: Just show score badge
        if (!score) return null;
        return (
            <div
                style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 10px",
                    borderRadius: 12,
                    fontSize: 12,
                    fontWeight: 600,
                    background: `${getQualityColor(score)}20`,
                    color: getQualityColor(score),
                }}
            >
                <span style={{ fontSize: 14 }}>✨</span>
                {score.toFixed(1)}/10
            </div>
        );
    }

    // Full: Quality panel
    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                gap: 12,
                padding: 12,
                background: "rgba(255,255,255,0.03)",
                borderRadius: 8,
                marginTop: 8,
            }}
        >
            <div
                style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "#6b7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                }}
            >
                ✨ AI Quality Report
            </div>

            {/* Score Badge */}
            {score !== undefined && (
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div
                        style={{
                            width: 48,
                            height: 48,
                            borderRadius: "50%",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: `${getQualityColor(score)}20`,
                            border: `2px solid ${getQualityColor(score)}`,
                        }}
                    >
                        <span
                            style={{
                                fontSize: 16,
                                fontWeight: 700,
                                color: getQualityColor(score),
                            }}
                        >
                            {score.toFixed(1)}
                        </span>
                    </div>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: "#f5f5f5" }}>
                            {tier || getQualityLabel(score)}
                        </div>
                        {correctionsCount !== undefined && (
                            <div style={{ fontSize: 12, color: "#9ca3af" }}>
                                {correctionsCount} corrections applied
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Summary */}
            {summary && (
                <p style={{ fontSize: 12, color: "#9ca3af", margin: 0, lineHeight: 1.5 }}>
                    {summary}
                </p>
            )}

            {/* Notes */}
            {notes && notes.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {notes.slice(0, 3).map((note, i) => (
                        <div
                            key={i}
                            style={{
                                fontSize: 11,
                                color: "#9ca3af",
                                padding: "4px 8px",
                                background: "rgba(255,255,255,0.03)",
                                borderRadius: 4,
                                borderLeft: "2px solid #528BFF",
                            }}
                        >
                            {note}
                        </div>
                    ))}
                    {notes.length > 3 && (
                        <div style={{ fontSize: 11, color: "#6b7280" }}>
                            +{notes.length - 3} more notes
                        </div>
                    )}
                </div>
            )}

            {/* Flagged Segments Warning */}
            {flaggedCount > 0 && (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 10px",
                        background: "rgba(245, 158, 11, 0.1)",
                        borderRadius: 6,
                        fontSize: 12,
                        color: "#f59e0b",
                    }}
                >
                    <span>⚠️</span>
                    {flaggedCount} segment{flaggedCount > 1 ? "s" : ""} flagged for review
                </div>
            )}
        </div>
    );
}

/**
 * Compact quality badge for job lists.
 */
export function QualityBadge({ job }: { job: Job }) {
    return <EditorReport job={job} compact />;
}
