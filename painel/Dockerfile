FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.3 gunicorn==22.0.0 requests==2.32.3 psycopg2-binary==2.9.9
WORKDIR /app
COPY server.py .
CMD ["gunicorn","-b","0.0.0.0:8110","-w","2","--threads","4","--timeout","120","server:app"]
