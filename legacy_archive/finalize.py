from pathlib import Path

# Compatibility wrapper for legacy scripts. Core logic lives in workers/finalizer.py.
from workers import finalizer as _finalizer


def json_to_srt(json_file):
    return _finalizer.finalize(Path(json_file))


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python finalize.py <APPROVED.json>")
        return 1
    json_to_srt(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
