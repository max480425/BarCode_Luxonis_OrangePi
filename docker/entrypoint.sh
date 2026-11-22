#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/data /app/captures

# Jeśli przekazano własną komendę (np. python verify_oak.py / pytest), uruchom ją bezpośrednio
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

GUI_MODE="${GUI_MODE:-novnc}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
NOVNC_RES="${NOVNC_RES:-1600x960x24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PORT="${VNC_PORT:-5900}"
VNC_PASSWORD="${VNC_PASSWORD:-}"

PIDS=()
APP_PID=""

cleanup() {
    echo "[entrypoint] Zatrzymywanie usług..."
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -TERM "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    for pid in "${PIDS[@]:-}"; do
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill -TERM "${pid}" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}

trap cleanup SIGTERM SIGINT EXIT

case "${GUI_MODE}" in
    novnc)
        export DISPLAY=":${DISPLAY_NUM}"
        export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

        rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" || true

        echo "[entrypoint] Uruchamianie Xvfb na DISPLAY=${DISPLAY} (${NOVNC_RES})..."
        Xvfb "${DISPLAY}" -screen 0 "${NOVNC_RES}" -ac +extension GLX +render -noreset &
        PIDS+=($!)

        for _ in $(seq 1 50); do
            if [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
                break
            fi
            sleep 0.1
        done

        echo "[entrypoint] Uruchamianie menedżera okien fluxbox..."
        fluxbox -display "${DISPLAY}" >/tmp/fluxbox.log 2>&1 &
        PIDS+=($!)

        VNC_ARGS=(-display "${DISPLAY}" -forever -shared -rfbport "${VNC_PORT}" -noxdamage)
        if [ -n "${VNC_PASSWORD}" ]; then
            VNC_ARGS+=(-passwd "${VNC_PASSWORD}")
        else
            VNC_ARGS+=(-nopw)
        fi

        echo "[entrypoint] Uruchamianie x11vnc na porcie ${VNC_PORT}..."
        x11vnc "${VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
        PIDS+=($!)

        echo "[entrypoint] Uruchamianie noVNC na porcie ${NOVNC_PORT} (http://<IP>:${NOVNC_PORT}/vnc.html?autoconnect=true&resize=scale)..."
        websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" >/tmp/novnc.log 2>&1 &
        PIDS+=($!)
        ;;
    x11)
        export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
        echo "[entrypoint] Tryb X11 hosta (DISPLAY=${DISPLAY:-nieustawione})..."
        ;;
    offscreen)
        export QT_QPA_PLATFORM="offscreen"
        export START_MAXIMIZED="0"
        echo "[entrypoint] Tryb headless offscreen (bez serwera X)..."
        ;;
    *)
        echo "[entrypoint] Nieznany GUI_MODE='${GUI_MODE}' (dozwolone: novnc, x11, offscreen)" >&2
        exit 1
        ;;
esac

echo "[entrypoint] Uruchamianie aplikacji Python (python -m src.app)..."
python -m src.app &
APP_PID=$!

set +e
wait "${APP_PID}"
EXIT_CODE=$?
set -e
APP_PID=""
exit "${EXIT_CODE}"
