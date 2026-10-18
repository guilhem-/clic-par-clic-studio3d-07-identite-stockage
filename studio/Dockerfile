# The organization's 3D studio: headless Blender 5.2 + "studio" MCP server, module 7 state.
#   docker build -t studio3d-en:7 studio
# One image for both roles and both worker containers: api.py (MCP server), worker.py (the worker's agent),
# launcher.py (the worker's "blender" container, run as the "blender" user, UID 10001, without any secret).
FROM python:3.12-slim

ARG BLENDER_VERSION=5.2.0
ARG BLENDER_SERIES=5.2
# download.blender.org refuses automated downloads (403): official mirror, checksum verified.
ARG BLENDER_MIRROR=https://ftp.nluug.nl/pub/graphics/blender/release
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 \
      libgl1 libegl1 libglib2.0-0 libgomp1 \
  && cd /tmp \
  && curl -fsSLO "${BLENDER_MIRROR}/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
  && curl -fsSL "${BLENDER_MIRROR}/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}.sha256" | grep linux-x64.tar.xz | sha256sum -c - \
  && tar -xJf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" -C /opt \
  && rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
  && mv /opt/blender-${BLENDER_VERSION}-linux-x64 /opt/blender \
  && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Marking of the renders (module 4): font of the visible banner, same rendering everywhere.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mcp==1.20.0" "pydantic==2.11.7" "redis==5.2.1" "prometheus-client==0.26.0" \
      "pillow==12.3.0" "boto3==1.43.111" "PyJWT[crypto]==2.15.1"

RUN useradd --create-home studio \
  && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin blender
WORKDIR /app
COPY api.py common.py worker.py launcher.py render.py package.py marking.py metrics.py storage.py identity.py ./
USER studio
ENV BLENDER=/opt/blender/blender PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "api.py"]
