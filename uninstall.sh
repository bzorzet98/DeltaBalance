#!/usr/bin/env bash
# DeltaBalance — uninstall.sh
#
# Desinstala lo que instaló install.sh. NO borra los datos de la app (la
# base y las preferencias): viven en otra carpeta, y quedan para una
# reinstalación.

set -euo pipefail

APP_NAME="deltabalance"
APP_ID="com.deltabalance.app"

DESTINO_DIR="$HOME/.local/opt/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications"

rm -rf "$DESTINO_DIR"
rm -f "$HOME/.local/bin/$APP_NAME"
rm -f "$DESKTOP_DIR/$APP_ID.desktop"
rm -f "$DESKTOP_DIR/$APP_NAME.desktop"   # nombre de una versión anterior del instalador, si quedó
rm -f "$HOME/.local/share/icons/$APP_NAME.png"

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "✅ DeltaBalance desinstalado"
echo "   Tus datos no se borraron: si lo volvés a instalar, siguen ahí."
