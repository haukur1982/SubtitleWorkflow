
export interface JobMeta {
    ingest_time: string;
    original_filename: string;
    source_path: string;
    duration?: string;
    resolution?: string;
    target_language?: string;
    review_required?: boolean;
    remote_review_required?: boolean;
    remote_review_url?: string;
    review_url?: string;
    burn_approved?: boolean;
    [key: string]: unknown;
}

export interface EditorReport {
    rating: number;
    verdict: string;
    summary: string;
    major_issues?: string[];
}

export interface Job {
    file_stem: string;
    status: string;
    stage: string;
    priority?: number;
    progress: number;
    meta: JobMeta;
    editor_report?: string | null; // It comes as a JSON string
    updated_at: string;
    target_language?: string;
    program_profile?: string;
    subtitle_style?: string;
    client?: string;
    due_date?: string;
}

// Helper to parse the report safely
export function parseEditorReport(jsonString?: string | null): EditorReport | null {
    if (!jsonString) return null;
    try {
        return JSON.parse(jsonString);
    } catch {
        return null;
    }
}
