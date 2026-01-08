# Omega Pro Subtitle System - Hardware Requirements Spec

**Purpose:** This document describes the compute workloads to help select appropriate hardware.

---

## System Overview

A broadcast subtitle production system that processes video files through:
1. **Ingest** - Watch folders, file organization
2. **Vocal Extraction** - Demucs (separates voice from music)
3. **Transcription** - AssemblyAI (cloud API)
4. **Translation** - Google Cloud Run + Gemini AI
5. **Subtitle Burn** - FFmpeg encodes video with subtitles
6. **Dashboard** - Next.js web UI for monitoring

---

## Compute Workloads

### 🔴 CPU-Intensive (Local)

| Task | Description | Duration |
|------|-------------|----------|
| **FFmpeg Subtitle Burn** | Re-encodes video with subtitles | ~1x realtime (software H.264) |
| **Audio Extraction** | Extract WAV from video for processing | Fast (seconds) |
| **File I/O** | Large video file moves/copies | Disk-bound |

### 🟡 GPU-Accelerated (Apple Silicon)

| Task | Description | Benefit |
|------|-------------|---------|
| **Demucs Vocal Extraction** | Removes background music from audio | Uses `mps` (Metal/Apple GPU) |
| **HEVC Hardware Encode** | `hevc_videotoolbox` for fast burns | **9x realtime** vs 1x software |

### 🟢 Cloud-Offloaded (No Local Compute)

| Task | Where |
|------|-------|
| **Transcription** | AssemblyAI API (cloud) |
| **Translation** | Google Cloud Run + Vertex AI |
| **AI Editing/Polish** | Google Cloud Run |

---

## Storage Requirements

| Item | Requirement |
|------|-------------|
| **Working Storage** | External SSD (currently "Extreme SSD") |
| **Speed** | USB 3.2 / Thunderbolt (video files are large) |
| **Capacity** | 1-2TB recommended (videos accumulate) |
| **Free Space Alert** | System warns below 50GB free |

**Folder Structure (on SSD):**
```
/Volumes/Extreme SSD/Omega_Work/
├── 1_INBOX/          # Drop files here
├── 2_VAULT/          # Originals + extracted audio
├── 3_EDITOR/         # Intermediate translation files
├── 4_DELIVERY/       # Final output (burned videos)
└── 99_ERRORS/        # Failed jobs
```

---

## Software Stack

| Component | Technology |
|-----------|------------|
| **OS** | macOS (launchd services) |
| **Python** | 3.11 (main), 3.9 (WhisperX fallback only) |
| **Node.js** | 18+ (for Next.js frontend) |
| **FFmpeg** | Video encoding/decoding |
| **Demucs** | Facebook's vocal separation AI |

---

## Network Requirements

| Service | Usage |
|---------|-------|
| **AssemblyAI** | Upload audio, receive transcript |
| **Google Cloud Storage** | Job artifacts (JSON) |
| **Google Cloud Run** | Translation executions |
| **Vertex AI** | Gemini API calls |

Bandwidth: Moderate (audio uploads ~10-100MB per job, video stays local)

---

## Recommended Hardware Specs

### Minimum (Budget)
- **CPU:** Apple M1 or Intel i5 (8-core)
- **RAM:** 16GB
- **Storage:** 256GB internal + 1TB external SSD
- **GPU:** Integrated (Demucs will run slower on CPU)

### Recommended (Production)
- **CPU:** Apple M2/M3 (8-core)
- **RAM:** 16-32GB (Demucs benefits from RAM)
- **Storage:** 512GB internal + 2TB external NVMe SSD
- **GPU:** Apple Silicon unified memory (enables `mps` acceleration)

### Optimal (Heavy Workloads)
- **CPU:** Apple M3 Pro or M3 Max
- **RAM:** 32GB+ (faster Demucs, parallel jobs)
- **Storage:** 1TB internal + 4TB external Thunderbolt SSD
- **GPU:** More GPU cores = faster Demucs + hardware encode

---

## Key Performance Factors

| Factor | Impact |
|--------|--------|
| **Apple Silicon GPU** | 9x faster HEVC encode, faster Demucs |
| **SSD Speed** | Faster video file I/O |
| **RAM** | Demucs vocal separation benefits from more |
| **CPU Cores** | FFmpeg software encode (H.264) uses all cores |
| **Network** | Only affects cloud API calls (not bottleneck) |

---

## What NOT Needed

- ❌ **NVIDIA GPU** - Not required (AssemblyAI handles transcription)
- ❌ **Massive internal storage** - Videos live on external SSD
- ❌ **Windows/Linux** - System uses macOS-specific services (launchd)
- ❌ **Local LLM** - Translation runs on Google Cloud

---

## Summary for Gemini

**TL;DR for computer shopping:**

> I need a Mac for a video subtitle production system. Main local tasks are:
> 1. **Demucs** vocal extraction (GPU-accelerated on Apple Silicon via MPS)
> 2. **FFmpeg** HEVC hardware encoding (VideoToolbox)
> 3. Large video file handling (external SSD)
>
> Cloud handles: transcription (AssemblyAI), translation (Gemini/Vertex AI).
>
> Recommended: M2/M3 Mac with 16-32GB RAM + fast external SSD.
