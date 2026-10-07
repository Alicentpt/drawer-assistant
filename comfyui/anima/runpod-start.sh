#!/usr/bin/env bash
# Run inside runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda13.0 after uploading
# this script, models.json and the UI workflow to /workspace/drawer-assistant.
set -euo pipefail
STATE=/workspace/drawer-assistant
COMFY=/opt/drawer-comfyui
PYTHON=/opt/drawer-venv/bin/python
REV=b0b743566f65daafc423b4fea8a2fbda94b3384a

# Preserve the pod's existing SSH-key access after replacing its entrypoint.
if [ -n "${PUBLIC_KEY:-}" ]; then
    install -d -m 700 /root/.ssh
    printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    ssh-keygen -A
    service ssh start
fi

# Global Store rejects venv symlinks: keep executables on the container disk.
if [ ! -x "$PYTHON" ]; then
    python3.12 -m venv --system-site-packages /opt/drawer-venv
fi
if [ ! -d "$COMFY/.git" ]; then
    git clone --depth 1 --branch v0.39.0 https://github.com/Comfy-Org/ComfyUI.git "$COMFY"
fi
test "$(git -C "$COMFY" rev-parse HEAD)" = "$REV"
env -u PIP_CONSTRAINT -u PIP_BUILD_CONSTRAINT "$PYTHON" -m pip install -r "$COMFY/requirements.txt"

# Validate downloads before caching; use local copies for model loading.
while IFS=$'\t' read -r target url digest; do
    model="$COMFY/models/$target"
    cached="$STATE/models/$target"
    mkdir -p "$(dirname "$model")" "$(dirname "$cached")"
    if [ ! -s "$model" ]; then
        if [ -s "$cached" ]; then
            cp "$cached" "$model.part"
        else
            curl --fail --location --retry 3 --output "$model.part" "$url"
        fi
        printf '%s  %s\n' "$digest" "$model.part" | sha256sum --check --status
        mv "$model.part" "$model"
    fi
    printf '%s  %s\n' "$digest" "$model" | sha256sum --check
    if [ ! -s "$cached" ]; then
        cp "$model" "$cached.part"
        printf '%s  %s\n' "$digest" "$cached.part" | sha256sum --check --status
        mv "$cached.part" "$cached"
    fi
done < <("$PYTHON" -c 'import json; from pathlib import Path; data=json.loads(Path("/workspace/drawer-assistant/models.json").read_text()); [print(m["target"],m["url"],m["sha256"],sep="\t") for m in data["models"]]')

mkdir -p "$STATE/output" "$STATE/input" "$STATE/user/default/workflows"
cp "$STATE/nova-anime-am-v20-text-img2img.ui.json" "$STATE/user/default/workflows/Jessica - Nova Anime AM v2 - Text and Reference.json"
cd "$COMFY"
exec "$PYTHON" main.py --listen 0.0.0.0 --port 8188 --gpu-only \
    --disable-async-offload --disable-pinned-memory --disable-all-custom-nodes \
    --output-directory "$STATE/output" \
    --input-directory "$STATE/input" --user-directory "$STATE/user"
