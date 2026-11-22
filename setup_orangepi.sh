#!/usr/bin/env bash
set -euo pipefail

echo "=== Przygotowanie hosta Orange Pi 5 (Ubuntu 24.04) pod Barcode Verifier ==="
echo "Architektura: $(uname -m)"

mkdir -p data captures

echo "[1/4] Instalacja reguł udev dla kamer Luxonis OAK (idVendor=03e7)..."
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "[2/4] Wykryte urządzenia USB (szukanie Logitech / Movidius Luxonis):"
if command -v lsusb >/dev/null 2>&1; then
    lsusb || true
else
    echo "Brak lsusb (opcjonalnie: sudo apt install usbutils)"
fi

echo "[3/4] Wykryte węzły wideo V4L2:"
ls -l /dev/video* 2>/dev/null || echo "Brak węzłów /dev/video* (czy kamera USB jest podłączona?)"

echo "[4/4] Sprawdzanie Dockera..."
if ! command -v docker >/dev/null 2>&1; then
    echo "[UWAGA] Docker nie jest zainstalowany."
    echo "Zainstaluj Docker Engine + Compose plugin:"
    echo "  curl -fsSL https://get.docker.com | sh"
    echo "  sudo usermod -aG docker \$USER"
    exit 1
fi

docker --version
docker compose version

echo ""
echo "Gotowe! Uruchom system poleceniem:"
echo "  docker compose up -d --build"
echo "A następnie otwórz podgląd GUI w przeglądarce:"
echo "  http://<IP_ORANGE_PI>:6080/vnc.html?autoconnect=true&resize=scale"
