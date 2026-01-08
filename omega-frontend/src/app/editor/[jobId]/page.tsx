"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { SubtitleEditor } from "@/components/SubtitleEditor";
import { AssistantPanel } from "@/components/AssistantPanel";
import { Loader2, AlertTriangle } from "lucide-react";

export default function EditorPage() {
    const params = useParams();
    const jobId = params?.jobId as string; // Next.js 13+ params are objects

    const [segments, setSegments] = useState([]);
    const [graphicZones, setGraphicZones] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!jobId) return;

        const loadData = async () => {
            try {
                const res = await fetch(`/api/editor/${jobId}`);
                if (!res.ok) throw new Error("Failed to load job data");
                const data = await res.json();
                setSegments(data.segments || []);
                setGraphicZones(data.graphic_zones || []);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        loadData();
    }, [jobId]);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-screen bg-omega-base text-omega-text-primary">
                <Loader2 className="w-8 h-8 animate-spin text-omega-primary" />
                <span className="ml-3 font-mono text-sm">INITIALIZING WORKSTATION...</span>
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex items-center justify-center h-screen bg-omega-base text-omega-text-primary">
                <div className="flex flex-col items-center bg-omega-panel p-8 rounded-lg border border-omega-border">
                    <AlertTriangle className="w-12 h-12 text-omega-alert mb-4" />
                    <h2 className="text-xl font-bold">PROJECT LOAD FAILED</h2>
                    <p className="text-omega-text-secondary mt-2 font-mono text-sm">{error}</p>
                </div>
            </div>
        );
    }

    return <SubtitleEditor jobId={jobId} initialSegments={segments} initialGraphicZones={graphicZones} />;
}
