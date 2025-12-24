# Use CUDA 12.1 runtime (driver compatible with Cloud Run L4)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1

# Install Python 3.10, FFmpeg
RUN apt-get update && apt-get install -y \
    python3.10 python3.10-venv python3-pip ffmpeg git \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 \
    && update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1

WORKDIR /app

# Pin numpy to version compatible with whisperx (before 2.0)
RUN pip install --no-cache-dir numpy==1.26.4

# Install PyTorch 2.1.2 with CUDA 12.1 (stable, known-good version)
RUN pip install --no-cache-dir \
    torch==2.1.2 \
    torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu121

# Install WhisperX and GCS client
RUN pip install --no-cache-dir \
    whisperx \
    google-cloud-storage>=2.10.0

# Pre-download Whisper model
RUN python -c "import whisperx; whisperx.load_model('large-v3', 'cpu', compute_type='float32')" || true

# Copy transcriber service
COPY cloud/transcriber/transcriber_service.py /app/transcriber_service.py

ENTRYPOINT ["python", "-u", "transcriber_service.py"]



