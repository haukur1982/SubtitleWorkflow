# Omega Development Roadmap
**Updated:** January 8, 2026  
**Status:** Pre-CBN Meeting Sprint  
**Architect:** Claude (AI)  
**Developer:** [To be assigned]

---

## Strategic Context

- **CBN meeting:** Next week with Mark Dijkens (CEO, CBN Europe)
- **Core product:** 3-step AI translation pipeline (validated at 9.2/10 average)
- **Goal:** Close a pilot deal, scale to 30+ programs/week

---

## Priority Order (What I Would Do)

### 🔴 CRITICAL (Before CBN Meeting)

| # | Task | Why | Effort | Delegatable? |
|---|---|---|---|---|
| 1 | **Test Remote Review Flow** | You need to demo QC handoff to client | 2 hrs | ❌ You test |
| 2 | **Fix Editor→Burn workflow** | Already done, verify it works | 30 min | ❌ You verify |
| 3 | **Prepare demo video** | Have 2 samples ready (Jerusalem Dateline) | 1 hr | ❌ You do |

### 🟡 HIGH (First Week After Meeting)

| # | Task | Why | Effort | Delegatable? |
|---|---|---|---|---|
| 4 | **Library filter tabs** | Scale UX — hide clutter | 3 hrs | ✅ Developer |
| 5 | **Library search bar** | Find programs fast | 2 hrs | ✅ Developer |
| 6 | **Retry button for failed jobs** | Quick recovery | 1 hr | ✅ Developer |
| 7 | **Inline QC warnings in editor** | Real-time error detection | 4 hrs | ✅ Developer |

### 🟢 MEDIUM (Weeks 2-3)

| # | Task | Why | Effort | Delegatable? |
|---|---|---|---|---|
| 8 | **Client/date folder structure** | Organize deliverables | 3 hrs | ✅ Developer |
| 9 | **Delivery CSV export** | Client reporting | 2 hrs | ✅ Developer |
| 10 | **Test dubbing pipeline** | Demo capability for dub markets | 4 hrs | ⚠️ Pair w/ you |
| 11 | **Add ElevenLabs provider** | Premium voice quality | 6 hrs | ✅ Developer |

### 🔵 FUTURE (After First Client)

| # | Task | Why | Effort | Delegatable? |
|---|---|---|---|---|
| 12 | **Multi-speaker diarization** | Professional dubbing | 8 hrs | ✅ Developer |
| 13 | **Client portal (read-only)** | CBN tracks their jobs | 12 hrs | ✅ Developer |
| 14 | **Slack/email notifications** | Job completion alerts | 4 hrs | ✅ Developer |

---

## Developer Handoff Specs

### Task 4: Library Filter Tabs

**File:** `omega-frontend/src/components/views/LibraryView.tsx`  
**Current:** All programs shown, sorted by update time.  
**Goal:** Add tabs: `All | In Progress | Complete | Delivered`

**Spec:**
```tsx
// Add state for active filter
const [filter, setFilter] = useState<"all" | "in_progress" | "complete" | "delivered">("all");

// Filter logic
const filteredPrograms = sortedPrograms.filter(p => {
  if (filter === "all") return true;
  if (filter === "in_progress") return !isComplete(p) && !isDelivered(p);
  if (filter === "complete") return isComplete(p) && !isDelivered(p);
  if (filter === "delivered") return isDelivered(p);
});

// Add tabs UI above grid
<div className="filter-tabs">
  <button onClick={() => setFilter("all")}>All ({programs.length})</button>
  <button onClick={() => setFilter("in_progress")}>In Progress ({countInProgress})</button>
  ...
</div>
```

**Acceptance:** Tabs work, counts update, filter persists during session.

---

### Task 5: Library Search Bar

**File:** `omega-frontend/src/components/views/LibraryView.tsx`  
**Goal:** Search by program title or client name.

**Spec:**
```tsx
const [search, setSearch] = useState("");

const searchedPrograms = filteredPrograms.filter(p =>
  p.title.toLowerCase().includes(search.toLowerCase()) ||
  (p.client || "").toLowerCase().includes(search.toLowerCase())
);

// Add search input in header
<input
  type="text"
  placeholder="Search programs..."
  value={search}
  onChange={(e) => setSearch(e.target.value)}
/>
```

**Acceptance:** Typing filters in real-time, clear button resets.

---

### Task 6: Retry Button for Failed Jobs

**File:** `omega-frontend/src/components/views/PipelineView.tsx`  
**Backend:** `dashboard.py`

**Goal:** One-click retry for FAILED stage items.

**Frontend Spec:**
```tsx
// Add retry handler
const handleRetry = async (trackId: string) => {
  await fetch(`${API_BASE}/api/v2/tracks/${trackId}/retry`, { method: "POST" });
  fetchActiveTracks(); // Refresh
};

// Add button in track row for FAILED stage
{stageKey === "FAILED" && (
  <button onClick={() => handleRetry(track.id)}>🔄 Retry</button>
)}
```

**Backend Spec:**
```python
@app.route('/api/v2/tracks/<track_id>/retry', methods=['POST'])
@admin_required
def api_v2_retry_track(track_id):
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404

    job_id = track.get("job_id")
    if job_id:
        omega_db.update(job_id, stage="QUEUED", status="Retry requested", progress=0)

    omega_db.update_track(track_id, stage="QUEUED", status="Retry requested", progress=0)
    return jsonify({"success": True})
```

**Acceptance:** Clicking retry moves item from FAILED to QUEUED.

---

### Task 7: Inline QC Warnings in Editor

**File:** `omega-frontend/src/components/SubtitleEditor.tsx`

**Goal:** Show warning icons next to segments with issues.

**Spec:**
```tsx
const getWarnings = (segment) => {
  const warnings = [];
  const text = segment.text || "";
  const duration = segment.end - segment.start;
  const cps = text.length / duration;

  if (cps > 17) warnings.push({ type: "cps", msg: `CPS: ${cps.toFixed(1)} (>17)` });
  if (text.length > 84) warnings.push({ type: "length", msg: "Too long (>84 chars)" });
  if (duration < 0.8) warnings.push({ type: "duration", msg: "Too short (<0.8s)" });

  return warnings;
};

// In segment row
{warnings.length > 0 && (
  <span className="warning-icon" title={warnings.map(w => w.msg).join(", ")}>⚠️</span>
)}
```

**Acceptance:** Warnings appear in real-time as user edits.

---

### Task 11: ElevenLabs TTS Provider

**File:** `providers/elevenlabs_tts.py` (new)

**Goal:** Add ElevenLabs as TTS option for dubbing.

**Spec:**
```python
import requests
import os

class ElevenLabsTTSProvider:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.base_url = "https://api.elevenlabs.io/v1"

    def generate_speech(self, text: str, output_path: Path, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> Path:
        url = f"{self.base_url}/text-to-speech/{voice_id}"
        headers = {"xi-api-key": self.api_key}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        }

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

        return output_path
```

**Acceptance:** Can generate speech with ElevenLabs API.

---

## Your Immediate Actions (Before Sleep)

1. ✅ Reply to Google about quota (email drafted earlier)
2. ⬜ Test the remote review link flow
3. ⬜ Verify editor→burn fix works

## Developer's First Sprint

1. Task 4: Library filter tabs  
2. Task 5: Library search bar  
3. Task 6: Retry button  

**Total effort:** ~6 hours

---

## Summary

**You focus on:** Business, demos, CBN relationship  
**Developer focuses on:** UI polish, scale readiness

The 3-step translation is your competitive advantage. Everything else is operational efficiency. Don't let engineering distract from the deal.

---

## Addendum: Developer Clarifications (Updated)

### A1. Retry Semantics (Task 6)

**Behavior:** When retry is clicked:
1. Clear failure metadata: `meta.last_error = ""`, `meta.failed_at = ""`
2. Clear cloud progress markers: `meta.cloud_stage = ""`, `meta.cloud_progress = {}`  
3. Set `status = "Retry requested"`, `progress = 0`
4. Track a `meta.retry_count` guardrail (if > 3, require manual intervention)
5. Reset to the best available checkpoint (file existence check):
   - If `4_DELIVERY/SRT/{stem}.srt` exists → retry at **FINALIZED** (burn again)
   - Else if `3_TRANSLATED_DONE/{stem}_APPROVED.json` exists → retry at **REVIEWED**
   - Else if `2_VAULT/Data/{stem}_SKELETON_DONE.json` exists → retry at **TRANSCRIBED**
   - Else if `2_VAULT/Data/{stem}_SKELETON.json` exists → retry at **TRANSCRIBING**
   - Else → **QUEUED**

**Note:** Track stages are separate from job stages. Track retry should still move the track to `QUEUED`, while the job stage should use the pipeline checkpoints above.

---

### A2. Library Filter Definitions (Tasks 4-5)

**Program-level status is derived from tracks:**

| Filter | Logic |
|---|---|
| **In Progress** | `any(track.stage not in ["COMPLETE", "DELIVERED", "FAILED"])` |
| **Complete** | `all(track.stage in ["COMPLETE", "DELIVERED"])` AND `any(track.stage == "COMPLETE")` |
| **Delivered** | `all(track.stage == "DELIVERED")` |
| **Failed** | Show a badge if `any(track.stage == "FAILED")` |

**Mixed-status programs:** If a program has 3 tracks where 1 is COMPLETE, 1 is BURNING, 1 is FAILED:
- Show in **In Progress** (primary)
- Show a **Failed** warning badge

No separate "Failed" tab — badge-only.

```tsx
const getFilterStatus = (program: Program): "in_progress" | "complete" | "delivered" => {
  const tracks = program.tracks || [];
  if (tracks.length === 0) return "in_progress";

  const allDelivered = tracks.every(t => t.stage === "DELIVERED");
  if (allDelivered) return "delivered";

  const allDone = tracks.every(t => ["COMPLETE", "DELIVERED"].includes(t.stage));
  if (allDone) return "complete";

  return "in_progress";
};
```

---

### A3. QC Warning Thresholds (Task 7)

Use the same constants as `subtitle_standards.py`.

| Rule | Threshold | Source |
|---|---|---|
| Min Duration | `MIN_DURATION = 1.0` | `subtitle_standards.py` |
| Max Line Length | `MAX_CHARS_PER_LINE = 42` | `subtitle_standards.py` |
| Max Total Length | `MAX_CHARS_TOTAL = 84` | `subtitle_standards.py` |
| CPS Limits | `get_cps_for_language(lang)` | `subtitle_standards.py` |

For Icelandic (`is`): ideal CPS = **14.0**, tight CPS = **17.0**.

---

### A4. Stage Enum (Tracks)

Canonical track stages (UI + API v2):

```
QUEUED
INGESTING
TRANSCRIBING
TRANSLATING
CLOUD_TRANSLATING
CLOUD_EDITING
CLOUD_POLISHING
AWAITING_REVIEW
AWAITING_APPROVAL
FAILED
FINALIZING
BURNING
DUBBING
COMPLETE
DELIVERED
```

**Note:** Job stages include additional legacy values like `TRANSLATED`, `REVIEWING`, `REVIEWED`, `FINALIZED`, and `COMPLETED`.

---

### A5. ElevenLabs Integration

**Deferred until after CBN meeting.** Not needed for demo.

---

### A6. CBN Demo Runbook (5 minutes)

1. **Open Dashboard** → Show Pipeline view (empty = "all caught up")
2. **Library** → Click on Jerusalem Dateline program
3. **Program Detail** → Show track status, click to expand TrackDetailPanel
4. **Open in Finder** → Show output SRT and video files
5. **Play Video** → 30 seconds of burned subtitles
6. **Editor** → Open subtitle editor, show Copilot, make one edit
7. **Save** → Show re-finalize confirmation
8. **Close with:** "This took 20 minutes from upload to delivery"

**Fallback:** If anything fails, play a pre-rendered clip directly from disk (no-network mode).
