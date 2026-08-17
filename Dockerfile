# ----- stage 1: build the bgutil POT provider server -----
FROM node:20-bookworm-slim AS potbuild
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
WORKDIR /src/bgutil-ytdlp-pot-provider/server
RUN npm ci && npx tsc

# ----- stage 2: runtime (python app + node POT server + deno EJS) -----
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime yt-dlp uses to solve YouTube's JS challenges (EJS)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# yt-dlp POT plugin (bgutil provider) installed as a yt-dlp plugin package
COPY plugin/yt_dlp_plugins /app/yt_dlp_plugins

# the prebuilt POT provider server
COPY --from=potbuild /src/bgutil-ytdlp-pot-provider/server /opt/bgutil/server

COPY app.py start.sh ./
RUN chmod +x start.sh

ENV STREAM_MODE=proxy \
    CLIENTS=web_music,web_safari,web,android,ios,tv \
    YTDLP_CACHE=/tmp/ytdlp-cache

CMD ["./start.sh"]
