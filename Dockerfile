FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    OPENBLAS_CORETYPE=ARMV8 \
    QT_X11_NO_MITSHM=1 \
    DEPTHAI_DISABLE_CRASHDUMP_COLLECTION=1 \
    TZ=Europe/Warsaw

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    python3 \
    python3-venv \
    python3-pip \
    libusb-1.0-0 \
    usbutils \
    v4l-utils \
    libgl1 \
    libegl1 \
    libglib2.0-0 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    libxcb1 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xinerama0 \
    libxcb-xfixes0 \
    libxcb-glx0 \
    libxcb-shm0 \
    libxcb-sync1 \
    libx11-6 \
    libx11-xcb1 \
    libxext6 \
    libxrender1 \
    libsm6 \
    libice6 \
    libpulse0 \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-noto-color-emoji \
    xvfb \
    fluxbox \
    x11vnc \
    novnc \
    python3-websockify \
    xauth \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --upgrade pip && pip install -r requirements-docker.txt

COPY src/ ./src/
COPY tests/ ./tests/
COPY verify_oak.py .
COPY docker/entrypoint.sh ./docker/entrypoint.sh

RUN sed -i 's/\r$//' /app/docker/entrypoint.sh && \
    chmod +x /app/docker/entrypoint.sh && \
    mkdir -p /app/data /app/captures

EXPOSE 4840 5900 6080

ENTRYPOINT ["/app/docker/entrypoint.sh"]
