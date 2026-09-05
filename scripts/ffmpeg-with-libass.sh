#!/usr/bin/env bash

set -euo pipefail

# The macOS Homebrew FFmpeg available on this machine does not include the
# subtitles/libass filter. Reuse the already-built application image for the
# rendering command while keeping the input/output paths on the host. The
# renderer writes every temporary input and output file into one directory,
# so mounting the first existing absolute-path argument is sufficient.

image_name="${AI_VIDEO_FFMPEG_DOCKER_IMAGE:-ai-video-generation-api}"
mount_dir="${AI_VIDEO_FFMPEG_DOCKER_MOUNT_DIR:-}"

if [[ -z "$mount_dir" ]]; then
    for argument in "$@"; do
        if [[ "$argument" == /* ]]; then
            if [[ -d "$argument" ]]; then
                mount_dir="$argument"
            else
                mount_dir="$(dirname "$argument")"
            fi
            break
        fi
    done
fi

docker_args=(run --rm)
if [[ -n "$mount_dir" ]]; then
    docker_args+=(--volume "${mount_dir}:${mount_dir}")
fi

exec docker "${docker_args[@]}" "$image_name" ffmpeg "$@"
