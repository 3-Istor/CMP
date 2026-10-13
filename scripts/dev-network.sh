#!/usr/bin/env bash
# Routes the OpenStack network through an SSH bastion for local dev without the WireGuard VPN.
# SSH LocalForwards are not enough: Keystone's catalog returns 192.168.1.x endpoints that the
# SDK calls directly, so the whole subnet has to be routed.
set -euo pipefail

SSH_HOST="${CMP_SSH_HOST:-pae-node-1}"
SUBNET="${CMP_OPENSTACK_SUBNET:-192.168.1.0/24}"

if ! command -v sshuttle >/dev/null 2>&1; then
    cat >&2 <<'EOF'
sshuttle is required. Install it with one of:
  nix shell nixpkgs#sshuttle      (NixOS: add sshuttle to home.packages)
  brew install sshuttle           (macOS)
  sudo apt install sshuttle       (Debian/Ubuntu)
  pipx install sshuttle           (anything with Python)
EOF
    exit 1
fi

echo "Routing $SUBNET through $SSH_HOST (sudo is asked for the local firewall rules). Ctrl-C to stop."
# ClearAllForwardings: the bastion's SSH config may already bind LocalForward ports in another session.
exec sshuttle -r "$SSH_HOST" -e 'ssh -o ClearAllForwardings=yes' "$SUBNET"
