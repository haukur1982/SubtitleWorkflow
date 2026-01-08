import { useState, useEffect } from "react";
import { Mic, Play, Download, Loader2, Music } from "lucide-react";
import { Job } from "@/store/omega";

interface DubbingPanelProps {
    jobId: string;
    status: string;
}

const VOICES = [
    { id: "alloy", name: "Alloy", gender: "Neutral" },
    { id: "echo", name: "Echo", gender: "Male" },
    { id: "fable", name: "Fable", gender: "British Male" },
    { id: "onyx", name: "Onyx", gender: "Deep Male" },
    { id: "nova", name: "Nova", gender: "Female" },
    { id: "shimmer", name: "Shimmer", gender: "Female" },
];

export function DubbingPanel({ jobId, status }: DubbingPanelProps) {
    const [selectedVoice, setSelectedVoice] = useState("alloy");
    const [isDubbing, setIsDubbing] = useState(false);

    const isDubbingInProgress = status.startsWith("Dubbing");
    const isComplete = status === "Dubbing Complete";

    const handleDub = async () => {
        setIsDubbing(true);
        try {
            const res = await fetch("/api/action/dub", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ jobId, voice: selectedVoice }),
            });
            if (!res.ok) throw new Error("Failed to start dubbing");
        } catch (e) {
            alert("Error starting dubbing: " + e);
            setIsDubbing(false);
        }
    };

    return (
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#f5f5f5", fontSize: 13, fontWeight: 600 }}>
                <Mic size={14} className="text-[#528BFF]" />
                <span>AI Dubbing (Preview)</span>
            </div>

            <p style={{ margin: 0, fontSize: 12, color: "#9ca3af", lineHeight: 1.5 }}>
                Generate a synthetic voice track mixed with the original background audio.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <label style={{ fontSize: 11, fontWeight: 500, color: "#71717a", textTransform: "uppercase" }}>Voice</label>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    {VOICES.map(v => (
                        <button
                            key={v.id}
                            onClick={() => setSelectedVoice(v.id)}
                            disabled={isDubbingInProgress}
                            style={{
                                padding: "8px",
                                borderRadius: 6,
                                border: selectedVoice === v.id ? "1px solid #528BFF" : "1px solid rgba(255,255,255,0.1)",
                                background: selectedVoice === v.id ? "rgba(82,139,255,0.1)" : "transparent",
                                color: selectedVoice === v.id ? "#f5f5f5" : "#d4d4d4",
                                fontSize: 12,
                                cursor: "pointer",
                                textAlign: "left"
                            }}
                        >
                            <div style={{ fontWeight: 500 }}>{v.name}</div>
                            <div style={{ fontSize: 10, opacity: 0.7 }}>{v.gender}</div>
                        </button>
                    ))}
                </div>
            </div>

            <button
                onClick={handleDub}
                disabled={isDubbingInProgress || isDubbing}
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    padding: "10px",
                    borderRadius: 6,
                    background: isDubbingInProgress ? "rgba(255,255,255,0.05)" : "#528BFF",
                    color: isDubbingInProgress ? "#71717a" : "#fff",
                    border: "none",
                    fontWeight: 500,
                    fontSize: 13,
                    cursor: isDubbingInProgress ? "not-allowed" : "pointer",
                    marginTop: 8
                }}
            >
                {isDubbingInProgress ? (
                    <>
                        <Loader2 size={14} className="animate-spin" />
                        Generating Audio...
                    </>
                ) : (
                    <>
                        <Music size={14} />
                        Start Dubbing
                    </>
                )}
            </button>

            {isComplete && (
                <div style={{ padding: 12, borderRadius: 6, background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.2)", marginTop: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                        <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e" }} />
                        <span style={{ fontSize: 12, fontWeight: 500, color: "#22c55e" }}>Dubbing Ready</span>
                    </div>
                    {/* In a real app, this would link to the dubbed file */}
                    <p style={{ margin: 0, fontSize: 11, color: "#d4d4d4" }}>
                        Video with dubbed audio has been generated in the job folder.
                    </p>
                </div>
            )}
        </div>
    );
}
