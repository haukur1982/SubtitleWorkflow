import omega_db
import sys

stem = "HOP_2913_INT57"
print(f"Resetting job for {stem}...")
omega_db.update(stem, stage="CLOUD_READY", status="Reset for Debug", progress=0)
print("✅ Job reset.")
