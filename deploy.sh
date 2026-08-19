#!/usr/bin/env bash
#
# deploy.sh — pull and restart, but only once the image really is the current
# commit.
#
# `docker compose pull` does not fail when the new image has not been
# published yet: it quietly fetches the previous one and everything looks
# fine until you read the logs. The multi-arch build takes several minutes
# (arm64 runs emulated under QEMU), so pushing and deploying straight away
# silently redeploys the old build.
#
# This compares the published image's org.opencontainers.image.revision label
# against the checked-out commit and waits until they match.
#
# Usage:   ./deploy.sh [--timeout SECONDS]
#
set -euo pipefail

IMAGE="ghcr.io/davic80/yt2mp3:latest"
CONTAINER="yt2mp3-app"
INTERVAL=20
TIMEOUT=900

while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout) TIMEOUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

echo "→ fetching latest source"
git pull --ff-only

want="$(git rev-parse HEAD)"
echo "→ want image built from ${want:0:7}"

image_revision() {
    docker image inspect "$IMAGE" \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
        2>/dev/null || true
}

waited=0
while true; do
    docker compose pull -q 2>/dev/null || true
    have="$(image_revision)"

    if [[ "$have" == "$want" ]]; then
        break
    fi

    if (( waited >= TIMEOUT )); then
        echo "✗ timed out after ${TIMEOUT}s — image is still ${have:0:7}." >&2
        echo "  Check the build: gh run list --limit 1" >&2
        exit 1
    fi

    echo "  image is ${have:0:7}, waiting for the build… (${waited}s)"
    sleep "$INTERVAL"
    waited=$(( waited + INTERVAL ))
done

echo "→ starting"
docker compose up -d --remove-orphans

# Give the container a moment to boot before reading back what it is.
sleep 5

running="$(docker inspect "$CONTAINER" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)"

if [[ "$running" != "$want" ]]; then
    echo "✗ container is running ${running:0:7}, expected ${want:0:7}" >&2
    exit 1
fi

echo "✓ running ${want:0:7}"
echo
# Everything the app itself logged on this boot: the banner plus whatever
# migrations ran. The previous filter only matched two known phrases and hid
# the rest, which is exactly when you want to see them.
docker logs "$CONTAINER" 2>&1 | grep -E "INFO app(\.|:)" | tail -15 || true
