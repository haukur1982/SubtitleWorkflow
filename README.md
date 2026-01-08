# Omega TV SubtitleWorkflow
**The Professional AI-Powered Subtitle Engine**

Cloud-first subtitle pipeline for Omega TV: ingest → transcribe → 3-phase AI translation (Gemini) → editorial review → GPU-accelerated burn.

## 🚀 Quick Start (Production)
1. **Start Backend Services**:
   ```bash
   ./start_omega.sh   # Starts manager, dashboard API, and pre-flight check
   ```
2. **Start Omega Pro Frontend**:
   ```bash
   cd omega-frontend
   npm run dev        # Starts the Next.js Workstation at http://localhost:3000
   ```

## 🛠 Next-Gen Architecture
*   **Backend (Python/Flask)**: `dashboard.py` (Port 8080). Handles job processing, database management, and AI coordination.
*   **Frontend (Next.js 14)**: Situated in `/omega-frontend`. Provides the **Project Bin** (Dashboard) and **Workstation** (Editor).
*   **AI Engine (Gemini Pro)**: Integrated via `AssistantPanel.tsx` for real-time editorial assistance.

## 📦 Key System Components
- **Project Bin**: High-density dashboard for tracking project status at a glance.
- **The Workstation**: 3-pane unified editor (Source Viewer, Copilot Assistant, Timeline).
- **Omega Copilot**: Integrated AI chat that understands context and helps with translation/polish.
- **System Diagnostics**: Built-in logs viewer and manager control center.

## 📂 Folder Structure
```
1_INBOX/              # Drop videos here for processing
2_VAULT/              # Working storage (Videos, Data, Audio)
3_EDITOR/             # Working JSONs for the Manual Editor
4_DELIVERY/           # Final output (SRT, VIDEO)
omega-frontend/       # NEW: The Next.js 14 Pro Interface
workers/              # Core processing (Demucs, WhisperX, AssemblyAI)
```

## 📋 Environment
Ensure `.omega_secrets` contains:
- `OMEGA_CLOUD_PROJECT`
- `GEMINI_LOCATION`
- `ASSEMBLYAI_API_KEY`
- `OMEGA_DASH_PORT=8080`

## 🌟 Future Vision
**Aesthetic Pivot**: Moving from Technical/Density to **Minimalist/Premium**.
*   **Minimalist Dashboard**: Fewer borders, better typography, more whitespace (Apple-style).
*   **Fluid Timeline**: Smooth, high-performance subtitle scrolling.
*   **Advanced AI**: Direct subtitle manipulation via voice and natural language instructions.

---
*Last Updated: 2025-12-30*

