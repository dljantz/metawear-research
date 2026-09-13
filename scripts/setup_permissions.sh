#!/bin/bash
# setup_permissions.sh
# Grants Python the necessary Linux network capabilities to access Bluetooth LE
# hardware and raw sockets WITHOUT requiring sudo.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Configuring Linux BLE Permissions (No-Sudo Setup) ==="

# 1. Resolve target Python binary
if [ -n "$1" ]; then
    VENV_PYTHON="$1"
elif [ -n "$VIRTUAL_ENV" ]; then
    VENV_PYTHON="$VIRTUAL_ENV/bin/python3"
elif [ -f "$PROJECT_ROOT/metawear_39_env/bin/python3" ]; then
    VENV_PYTHON="$PROJECT_ROOT/metawear_39_env/bin/python3"
elif [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"
elif [ -f "$PROJECT_ROOT/venv/bin/python3" ]; then
    VENV_PYTHON="$PROJECT_ROOT/venv/bin/python3"
else
    VENV_PYTHON="$(which python3)"
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Python executable not found at: $VENV_PYTHON"
    echo "Usage: ./scripts/setup_permissions.sh [/path/to/venv/bin/python3]"
    exit 1
fi

REAL_PYTHON=$(readlink -f "$VENV_PYTHON")

echo "1. Resolving Python binary:"
echo "   Virtualenv target: $VENV_PYTHON"
echo "   Real executable:   $REAL_PYTHON"

echo "2. Setting capabilities 'cap_net_raw,cap_net_admin+eip' on real binary..."
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$REAL_PYTHON"

# If the venv binary is a symlink, create a standalone binary copy in the venv and setcap on it
if [ -L "$VENV_PYTHON" ]; then
    echo "3. Copying real binary to virtualenv to allow direct setcap execution..."
    sudo rm -f "$VENV_PYTHON"
    sudo cp "$REAL_PYTHON" "$VENV_PYTHON"
    sudo setcap 'cap_net_raw,cap_net_admin+eip' "$VENV_PYTHON"
    sudo chown "$USER:$USER" "$VENV_PYTHON"
fi

echo "4. Ensuring user '$USER' is in 'bluetooth' group..."
sudo usermod -aG bluetooth "$USER"

echo ""
echo "✓ Permissions configured successfully!"
echo "You can now run scripts directly without sudo:"
echo "  $VENV_PYTHON pipeline/cli.py list"
