# Use NVIDIA's PyTorch container - includes properly configured CUDA, cuDNN, PyTorch
FROM nvcr.io/nvidia/pytorch:23.10-py3

ENV PYTHONUNBUFFERED=1
ENV TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1

# Install FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install WhisperX (PyTorch is already in base image)
RUN pip install --no-cache-dir \
    whisperx \
    google-cloud-storage>=2.10.0

# Pre-download models to bake into image (faster cold starts)
RUN python -c "import whisperx; whisperx.load_model('large-v3', 'cpu', compute_type='float32')" || true

# Copy transcriber service
COPY cloud/transcriber/transcriber_service.py /app/transcriber_service.py

ENTRYPOINT ["python", "-u", "transcriber_service.py"]


