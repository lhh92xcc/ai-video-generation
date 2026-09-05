FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Debian's FFmpeg package is built with libass support. The CJK font package
# prevents Chinese subtitles from rendering as missing-glyph boxes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fontconfig \
        fonts-noto-cjk \
        libass9 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir "uv==0.11.2"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY config ./config
COPY prompts ./prompts

RUN python -m compileall -q app \
    && test -f prompts/story-bible-generation.txt \
    && test -f prompts/episode-planning.txt \
    && test -f prompts/scene-script-generation.txt \
    && test -f prompts/scene-script-generation-repair.txt \
    && test -f prompts/shot-list-generation.txt \
    && test -f prompts/shot-list-generation-repair.txt \
    && test -f prompts/script-generation.txt \
    && test -f prompts/script-generation-repair.txt \
    && ffmpeg -hide_banner -filters | grep -E "(^|[[:space:]])subtitles([[:space:]]|$)" \
    && ffprobe -version >/dev/null

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
