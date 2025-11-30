---
description: Test and refine subtitle styling using style_lab.py
---

This workflow guides you through testing and refining the visual style of your subtitles using `style_lab.py`.

1.  **Run the Style Lab Preview**
    Run the following command to generate a quick preview of the current subtitle style. This will create a short clip with subtitles burned in and play it using `ffplay`.

    ```bash
    python style_lab.py --duration 10 --overlay
    ```

    *Note: If you want to test a specific SRT file, append the path to the command (e.g., `python style_lab.py path/to/file.srt`). Otherwise, it picks the first one in `4_FINAL_OUTPUT`.*

2.  **Review the Preview**
    Watch the playback window. Pay attention to:
    - Font size and readability
    - Background box opacity and padding
    - Vertical positioning (y-offset)
    - Shadow and corner radius

3.  **Adjust Styles (if needed)**
    If you want to change the look, edit the `PROFILES` dictionary in `subs_render_overlay.py`.

    Common settings to tweak in `AppleTV_IS` profile:
    - `font_size`: Size of the text (default: 42)
    - `box_opacity`: Transparency of the background box (0.0 - 1.0)
    - `padding_x` / `padding_y`: Space around the text
    - `y_offset`: Distance from the bottom of the screen

4.  **Iterate**
    Repeat step 1 to see your changes instantly. The tool re-renders the overlay and plays the clip immediately.

5.  **Finalize**
    Once satisfied, the changes in `subs_render_overlay.py` will automatically apply to all future runs of the main pipeline (`publisher.py`).
