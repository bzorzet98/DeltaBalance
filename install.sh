#!/usr/bin/env bash
# DeltaBalance — install.sh
#
# Instala DeltaBalance (el bundle de `flet build linux`) para el usuario
# actual, sin sudo:
#   ~/.local/opt/deltabalance/                    el bundle completo
#   ~/.local/bin/deltabalance                     lanzador (para la terminal)
#   ~/.local/share/applications/<app id>.desktop  entrada del menú
#   ~/.local/share/icons/deltabalance.png         ícono
#
# El ejecutable de build/linux/ NO funciona suelto: busca lib/ (Flutter y
# plugins), data/, app/ y Python al lado suyo. Por eso se copia la carpeta
# entera y en ~/.local/bin va un lanzador que la ejecuta.
#
# Se instala en ~/.local/opt y no en ~/.local/share/<algo>: ahí guarda la
# app su carpeta de datos (la base), y uninstall.sh borra la instalación
# entera — así nunca puede tocar los datos.
#
# Reinstalar (ej. una versión nueva) reemplaza el bundle; los datos quedan.
#
# Para armar el archivo que se reparte (desde la raíz del proyecto, después
# de `flet build linux`), con esta misma estructura:
#   tar czf DeltaBalance-linux.tar.gz install.sh uninstall.sh README_INSTALACION.md build/linux

set -euo pipefail

APP_NAME="deltabalance"
APP_ID="com.deltabalance.app"   # APPLICATION_ID del build (bundle_id de pyproject.toml)

ORIGEN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$ORIGEN_DIR/build/linux"

DESTINO_DIR="$HOME/.local/opt/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
ICON_FILE="$ICON_DIR/$APP_NAME.png"

if [ ! -x "$BUNDLE_DIR/$APP_NAME" ]; then
    echo "❌ No encontré la app en $BUNDLE_DIR/$APP_NAME."
    echo "   Corré este script desde la carpeta descomprimida (la que tiene build/linux/)."
    exit 1
fi

# Ícono: el que viaja dentro del bundle; si no está, el de assets/ (raíz del proyecto).
ICONO_ORIGEN="$BUNDLE_DIR/app/assets/icon.png"
[ -f "$ICONO_ORIGEN" ] || ICONO_ORIGEN="$ORIGEN_DIR/assets/icon.png"

echo "Instalando DeltaBalance en $DESTINO_DIR ..."

# --- Bundle (se reemplaza entero: nada de una versión vieja queda mezclado) ---
mkdir -p "$(dirname "$DESTINO_DIR")"
rm -rf "$DESTINO_DIR"
cp -a "$BUNDLE_DIR" "$DESTINO_DIR"
chmod +x "$DESTINO_DIR/$APP_NAME"

# --- Lanzador para la terminal ---
# Arranca desde el home, igual que desde el menú: la app guarda sus
# preferencias (sesión incluida) en el directorio de trabajo
# (ui/utils/prefs.py), así que las dos formas de abrirla usan las mismas —
# y nunca quedan dentro de $DESTINO_DIR, que se reemplaza al reinstalar.
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/$APP_NAME" << EOF
#!/bin/sh
cd "\$HOME" || exit 1
exec "$DESTINO_DIR/$APP_NAME" "\$@"
EOF
chmod +x "$BIN_DIR/$APP_NAME"

# --- Ícono ---
if [ -f "$ICONO_ORIGEN" ]; then
    mkdir -p "$ICON_DIR"
    cp "$ICONO_ORIGEN" "$ICON_FILE"
else
    echo "⚠️  No encontré el ícono: la entrada del menú va a quedar sin ícono."
fi

# --- Entrada del menú de aplicaciones ---
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=DeltaBalance
Comment=Finanzas personales para la vida real
Exec=$DESTINO_DIR/$APP_NAME
Icon=$ICON_FILE
Terminal=false
Categories=Office;Finance;
StartupWMClass=$APP_NAME
EOF

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "✅ DeltaBalance instalado correctamente"
echo "   Abrilo desde el menú de aplicaciones."
case ":$PATH:" in
    *":$BIN_DIR:"*)
        echo "   O desde una terminal, corriendo: $APP_NAME" ;;
    *)
        echo "   Para abrirlo desde una terminal con '$APP_NAME', cerrá sesión y volvé a entrar"
        echo "   (así se agrega $BIN_DIR al PATH); mientras tanto: $BIN_DIR/$APP_NAME" ;;
esac
