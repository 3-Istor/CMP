#!/usr/bin/env bash
# Routes the OpenStack network through an SSH bastion for local dev without the WireGuard VPN.
# SSH LocalForwards are not enough: Keystone's catalog returns 192.168.1.x endpoints that the
# SDK calls directly, so the whole subnet has to be routed.
set -euo pipefail

SSH_HOST="${CMP_SSH_HOST:-pae-node-1}"
SUBNET="${CMP_OPENSTACK_SUBNET:-192.168.1.0/24}"

if command -v sshuttle >/dev/null 2>&1; then
    SSHUTTLE=(sshuttle)
elif command -v nix >/dev/null 2>&1; then
    SSHUTTLE=(nix run nixpkgs#sshuttle --)
else
    cat >&2 <<'EOF'
sshuttle is required. Install it with one of:
  sudo apt install sshuttle       (Debian/Ubuntu)
  brew install sshuttle           (macOS)
  pipx install sshuttle           (anything with Python)
EOF
    exit 1
fi

echo "Routing $SUBNET through $SSH_HOST (sudo is asked for the local firewall rules)."
# ClearAllForwardings: the bastion's SSH config may already bind LocalForward ports in another session.
# Extra arguments go to sshuttle, e.g. --daemon --pidfile=<path>.
exec "${SSHUTTLE[@]}" -r "$SSH_HOST" -e 'ssh -o ClearAllForwardings=yes' "$@" "$SUBNET"
