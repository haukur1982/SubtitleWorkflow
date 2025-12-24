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

# 1. Install numpy first (before anything that might upgrade it)
RUN pip install --no-cache-dir numpy==1.26.4

# 2. Install PyTorch 2.3.0 with CUDA 12.1 (known stable)
RUN pip install --no-cache-dir \
    torch==2.3.0+cu121 \
    torchaudio==2.3.0+cu121 \
    --index-url https://download.pytorch.org/whl/cu121

# 3. Install ctranslate2 4.4.0 specifically (last cuDNN 8 compatible)
RUN pip install --no-cache-dir ctranslate2==4.4.0

# 4. Install faster-whisper compatible with ctranslate2 4.4.0
RUN pip install --no-cache-dir faster-whisper==1.0.3

# 5. Install whisperx WITHOUT its dependencies (we installed them above)
RUN pip install --no-cache-dir --no-deps whisperx==3.3.0

# 6. Install remaining whisperx dependencies
RUN pip install --no-cache-dir \
    pyannote.audio==3.3.2 \
    nltk \
    transformers \
    google-cloud-storage>=2.10.0

# Pre-download Whisper model
RUN python -c "import whisperx; whisperx.load_model('large-v3', 'cpu', compute_type='float32')" || true

COPY cloud/transcriber/transcriber_service.py /app/transcriber_service.py

ENTRYPOINT ["python", "-u", "transcriber_service.py"]





