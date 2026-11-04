FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

WORKDIR /FileToLink

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ffmpeg aria2 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /requirements.txt

COPY . /FileToLink

CMD ["python", "bot.py"]
