# Timing Controls (Subtitle Finalizer)

This document summarizes the subtitle timing controls and QA signals.

## Modes

Default behavior is readability-first (balanced). This uses word timing when available and allows small extensions to hit CPS targets.

- `OMEGA_TIMING_MODE=balanced` (default)
  - Allows timing extension to meet CPS targets.
  - Uses word timings when present.

- `OMEGA_TIMING_MODE=strict`
  - Uses word end times as the primary out-time.
  - Prevents CPS-driven extensions unless a small tail is explicitly allowed.

## Strict Mode Controls

- `OMEGA_TIMING_STRICT_MAX_EXTEND` (seconds, default `0.0`)
  - Allows a small tail after the last word, if you want readability with tight sync.
  - Example: `0.15` gives a 150ms tail when space allows.

- `OMEGA_TIMING_STRICT_FRAGMENT_SHIFT` (seconds, default `0.0`)
  - Optional fallback when fragment timing is missing.

These can be set in `start_omega.sh` (commented in the file) or via environment variables.

## Timing QA Metrics

Each job stores `qa_timing` in the job meta. Key fields:

- `with_words` / `missing_words`: Word-timing coverage.
- `start_delta_*` / `end_delta_*`: Offset from word start/end.
- `overlaps`, `min_gap`, `max_gap`: Basic cadence checks.

If you see high `end_cutoff` or `start_late`, the subtitles may feel “laggy” or “clipped.”
