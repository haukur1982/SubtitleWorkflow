#!/usr/bin/env python3
import sys
import torch
import omegaconf

# Fix for PyTorch 2.6+ breaking pyannote.audio loading of VAD models
# This allows omegaconf.listconfig.ListConfig to be loaded with weights_only=True (default in 2.6)
try:
    torch.serialization.add_safe_globals([omegaconf.listconfig.ListConfig])
except AttributeError:
    pass # Older torch versions don't need/have this

from whisperx.__main__ import cli

if __name__ == "__main__":
    try:
        sys.exit(cli())
    except Exception as e:
        print(f"WhisperX Wrapper Error: {e}", file=sys.stderr)
        sys.exit(1)
