FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

WORKDIR /FileToLink

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ffmpeg aria2 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /requirements.txt

# bump YTDLP_REFRESH (or build with --no-cache) to always get the newest yt-dlp - old versions cause Dailymotion 401
ARG YTDLP_REFRESH=1
RUN pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]"

COPY . /FileToLink

CMD ["python", "bot.py"]
