FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py ./
ENV STREAM_MODE=proxy \
    CLIENTS=ios,android,tv,web \
    YTDLP_CACHE=/tmp/ytdlp-cache

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:${PORT:-10000}", "--workers", "1", "--threads", "8", "--timeout", "180"]
