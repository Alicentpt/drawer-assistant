# Build plugin packages separately; preserve Hermes' pinned dependency versions.
FROM nousresearch/hermes-agent@sha256:a11574b7fe8e1631d7d88d556677dac693bc058abf558cdeb899155688c1bf05 AS plugin-build
USER root
WORKDIR /opt/drawer-app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN /opt/hermes/.venv/bin/python -m ensurepip \
    && /opt/hermes/.venv/bin/python -m pip install --no-cache-dir --no-deps \
    --target /opt/drawer-python \
    psycopg==3.3.6 psycopg-binary==3.3.6 tzdata==2025.3 PyYAML==6.0.3 .

FROM nousresearch/hermes-agent@sha256:a11574b7fe8e1631d7d88d556677dac693bc058abf558cdeb899155688c1bf05
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    imagemagick=8:7.1.1.43+dfsg1-1+deb13u12 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error --location \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    --output /etc/ssl/certs/rds-global-bundle.pem
COPY --from=plugin-build /opt/drawer-python /opt/drawer-python
# Hermes narrows PYTHONPATH for tool subprocesses; .pth also covers those workers.
RUN printf '%s\n' /opt/drawer-python \
    > /opt/hermes/.venv/lib/python3.14/site-packages/drawer.pth
ENV PYTHONPATH=/opt/drawer-python:/opt/hermes
ENV PATH=/opt/drawer-python/bin:${PATH}
RUN /opt/hermes/.venv/bin/python -I -c \
    "import sys,psycopg,drawer_assistant; assert sys.version_info[:2] == (3,14)" \
    && /opt/hermes/.venv/bin/python -c \
    "from pathlib import Path; from drawer_assistant.hermes_compat import patch_telegram; patch_telegram(Path('/opt/hermes'))" \
    && magick -version
WORKDIR /opt/data
# Preserve upstream ENTRYPOINT and its s6 privilege drop and profile supervision.
