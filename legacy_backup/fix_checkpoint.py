import torch
from pathlib import Path

# Path to the checkpoint
ckpt_path = Path("venv/lib/python3.11/site-packages/whisperx/assets/pytorch_model.bin")

if ckpt_path.exists():
    print(f"Processing {ckpt_path}...")
    try:
        # Load with map_location='cpu' to avoid CUDA error, and allow globals
        checkpoint = torch.load(ckpt_path, map_location=torch.device('cpu'), weights_only=False)
        
        # Save it back
        torch.save(checkpoint, ckpt_path)
        print("✅ Checkpoint fixed and saved.")
    except Exception as e:
        print(f"❌ Error fixing checkpoint: {e}")
else:
    print(f"⚠️ Checkpoint not found at {ckpt_path}")
