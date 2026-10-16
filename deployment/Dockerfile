# Same supervised Hermes container contract as upstream; no custom gateway loop.
FROM nousresearch/hermes-agent@sha256:a11574b7fe8e1631d7d88d556677dac693bc058abf558cdeb899155688c1bf05
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    imagemagick=8:7.1.1.43+dfsg1-1+deb13u12 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error --location \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    --output /etc/ssl/certs/rds-global-bundle.pem
WORKDIR /opt/drawer-app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Keep Hermes' existing dependency pins; add only the plugin's missing packages.
RUN /opt/hermes/.venv/bin/python -m ensurepip \
    && /opt/hermes/.venv/bin/python -m pip install --no-cache-dir --no-deps \
    psycopg==3.3.6 psycopg-binary==3.3.6 tzdata==2025.3 PyYAML==6.0.3 . \
    && /opt/hermes/.venv/bin/python -c \
    "import sys,psycopg,drawer_assistant; assert sys.version_info[:2] == (3,14)" \
    && /opt/hermes/.venv/bin/python -c \
    "from pathlib import Path; from drawer_assistant.hermes_compat import patch_telegram; patch_telegram(Path('/opt/hermes'))" \
    && magick -version
WORKDIR /opt/data
# Preserve upstream ENTRYPOINT and its s6 privilege drop and profile supervision.
