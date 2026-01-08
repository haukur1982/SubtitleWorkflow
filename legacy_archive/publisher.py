from pathlib import Path

import config
from workers import publisher as _publisher

# Compatibility wrapper for legacy scripts. Core logic lives in workers/publisher.py.
BASE_DIR = config.BASE_DIR


def get_ffmpeg_binary() -> str:
    return config.FFMPEG_BIN


def burn_subtitles(srt_file, video_path=None, style=None, output_dir=None):
    srt_path = Path(srt_file)
    if video_path:
        video = Path(video_path)
        out_dir = Path(output_dir) if output_dir else None
        normalized_style = None
        if style:
            style_str = str(style).strip()
            if style_str in config.BURN_METHOD_MAP:
                normalized_style = style_str
            else:
                remap = {
                    "RUV_BOX": "Classic",
                    "RuvBox": "Classic",
                    "OMEGA_MODERN": "Modern",
                    "Apple": "Apple",
                    "Apple_TV": "Apple",
                }
                normalized_style = remap.get(style_str, style_str)
        return _publisher.publish(
            video,
            srt_path,
            subtitle_style=normalized_style or "Classic",
            output_dir=out_dir,
        )
    return _publisher.burn(srt_path, forced_style=style)


# Re-export helpers used by tooling.
ASS_HEADER = _publisher.ASS_HEADER
srt_to_ass = _publisher.srt_to_ass
generate_ass_from_srt = _publisher.generate_ass_from_srt
find_video_file = _publisher.find_video_file


def main():
    import sys

    print("publisher.py is a compatibility wrapper. Use workers/publisher.py for core logic.")
    if len(sys.argv) > 1:
        burn_subtitles(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
