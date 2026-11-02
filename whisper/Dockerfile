FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir faster-whisper==1.1.0 requests flask==3.0.3 gunicorn==22.0.0
WORKDIR /app
COPY app.py .
ENV HF_HOME=/models
CMD ["gunicorn","-b","0.0.0.0:9000","-w","1","--threads","2","--timeout","300","app:app"]
