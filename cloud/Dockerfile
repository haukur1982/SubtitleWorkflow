FROM python:3.11-slim

WORKDIR /app

COPY cloud/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy only what the cloud worker needs.
COPY config.py /app/config.py
COPY profiles.py /app/profiles.py
COPY gcp_auth.py /app/gcp_auth.py
COPY gcs_jobs.py /app/gcs_jobs.py
COPY omega_cloud_worker.py /app/omega_cloud_worker.py

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-u", "omega_cloud_worker.py"]
