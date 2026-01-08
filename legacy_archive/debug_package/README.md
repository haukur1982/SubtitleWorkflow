# Omega TV Icelandic Subtitle Pipeline

A production-grade, automated system for translating English sermon audio into Icelandic subtitles and burning them onto broadcast videos. Designed for Omega TV, it integrates Google Vertex AI (Gemini models) for contextual translation, Python for normalization, and FFmpeg for high-fidelity compositing. Supports watch-folder automation, theological glossary enforcement, and Apple TV+-inspired styling.

## Features
- **AI-Powered Translation**: Batch-processes transcriptions with Gemini 2.5/3 Pro, handling low-resource Icelandic morphology and sermon-specific terms.
- **Broadcast Compliance**: Enforces EBU/Netflix standards (e.g., 42 chars/line, 17 CPS, title-safe positioning).
- **Modular Rendering**: ProRes 4444 alpha overlays for pixel-precise styling (SF Pro Display, rounded translucent boxes), with ASS fallback.
- **Automation**: Headless monitoring of ingest folders; parallel processing for efficiency.
- **Testing Tools**: CLI previews and error logging for rapid iteration.

## Folder Structure
```
project_root/
├── 1_INBOX/                 # Raw audio/video ingest (e.g., DONE_{stem}.mp3, {stem}.mp4)
├── 2_READY_FOR_CLOUD/       # Processed videos; skeletons for translation
│   └── processed/           # Normalized videos
├── 3_TRANSLATED_DONE/       # Translated JSONs from Gemini
├── 4_FINAL_OUTPUT/          # Normalized SRTs and JSONs
├── 5_DELIVERABLES/          # Final burned MP4s
├── 99_ERRORS/               # Failed files with reasons
├── logs/                    # Processing logs (e.g., burn_log.txt)
├── service_account.json     # Google Cloud credentials (env: GOOGLE_APPLICATION_CREDENTIALS)
├── cloud_brain.py           # Translation orchestrator
├── finalize.py              # SRT/JSON normalization
├── publisher.py             # Subtitle burning and delivery
├── subs_render_overlay.py   # ProRes 4444 overlay generator
├── burn_in.py               # Video compositing
├── test_burn.py             # Testing CLI
├── style_lab.py             # Style preview tool
└── README.md                # This file
```

## Prerequisites
- Python 3.10+ (venv recommended: `python -m venv venv; source venv/bin/activate; pip install pillow srt google-cloud-storage vertexai`)
- FFmpeg 5.0+ (Homebrew: `brew install ffmpeg`)
- Google Cloud: Project ID (`sermon-translator-system`), bucket (`audio-hq-sermon-translator-55`), service account JSON.
- SF Pro Display font (system path: `/System/Library/Fonts/SFProDisplay-Semibold.otf`).

## Setup
1. Clone/place files in project root.
2. Set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json`.
3. Update `BASE_DIR` in scripts if needed.
4. For Gemini 3 Pro: In `cloud_brain.py`, set `model_id = "gemini-3.0-pro"`.

## Workflow
1. **Ingest**: Place skeleton JSONs (`{stem}_SKELETON.json`) and MP3s in `2_READY_FOR_CLOUD`.
2. **Translate**: Run `python cloud_brain.py` (watches loop); outputs to `3_TRANSLATED_DONE`.
3. **Normalize**: Run `python finalize.py`; generates SRTs/JSONs in `4_FINAL_OUTPUT`.
4. **Burn**: Run `python publisher.py`; composites to `5_DELIVERABLES/{stem}_SUBBED.mp4`.
5. **Test**: `python test_burn.py path/to/srt.srt [video.mp4] [output.mp4] --render-only` for previews.

## Configuration
- **Translation**: Edit `GLOSSARY` in `cloud_brain.py`; adjust `BATCH_SIZE=40`, `MAX_WORKERS=10`.
- **Styling**: Profiles in `subs_render_overlay.py` (e.g., font_size=42, radius=20, opacity=0.65).
- **Encoding**: CRF=19, slow preset in `burn_in.py` for quality/size balance.

## Troubleshooting
- **Gemini Truncation**: Increases retries/splits in `translate_batch_smart`.
- **Font Errors**: Fallback to "Arial.ttf"; verify path.
- **FFmpeg Issues**: Check `get_ffmpeg_binary`; test with `ffmpeg -version`.
- **Logs**: Monitor `burn_log.txt`; errors route to `99_ERRORS`.

## Extensions
- Multilingual: Add profiles/languages in `GLOSSARY`.
- Profiles: YAML for variants (e.g., social media sizing).
- QC: Integrate frame diffs in `test_burn.py`.

## License
Proprietary for Omega TV. Contact for contributions.

*Last Updated: November 23, 2025*
