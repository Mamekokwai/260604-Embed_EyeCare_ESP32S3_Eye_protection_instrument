#!/usr/bin/env bash
# Per-device USB Serial-JTAG authorization helper.
# It never places a shared unlock file on the TF card.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TOOL="$SCRIPT_DIR/unlock_token.py"
KEY_DIR="${EYECARE_KEY_DIR:-$PROJECT_ROOT/info/HTML/key}"
PRIVATE_KEY="${EYECARE_PRIVATE_KEY:-$KEY_DIR/eyecare_unlock_ecdsa_p256}"
PUBLIC_HEADER="${EYECARE_PUBLIC_HEADER:-$PROJECT_ROOT/main/include/unlock_public_key.h}"
PYTHON="${EYECARE_PYTHON:-python3}"
MAC=""
SERIAL=""
NONCE=""
PORT=""
OUTPUT="${EYECARE_TOKEN_OUTPUT:-$PWD/device.token}"
SKIP_KEYGEN=0

die() { printf '[ERR] %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: unlock_provision.sh --mac MAC --serial HEX32 --nonce HEX32 [options]

The MAC and nonce come from READ_ID and CHALLENGE on the target's USB
Serial-JTAG console. The script signs one device-bound ACTIVATE frame.
Options: --port /dev/ttyACM0 (send frame), --output FILE, --skip-keygen,
         --private-key FILE, --python PYTHON
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mac) [[ $# -ge 2 ]] || die '--mac requires a value'; MAC="$2"; shift 2 ;;
        --serial) [[ $# -ge 2 ]] || die '--serial requires a value'; SERIAL="$2"; shift 2 ;;
        --nonce) [[ $# -ge 2 ]] || die '--nonce requires a value'; NONCE="$2"; shift 2 ;;
        --port) [[ $# -ge 2 ]] || die '--port requires a value'; PORT="$2"; shift 2 ;;
        --output) [[ $# -ge 2 ]] || die '--output requires a value'; OUTPUT="$2"; shift 2 ;;
        --private-key) [[ $# -ge 2 ]] || die '--private-key requires a value'; PRIVATE_KEY="$2"; shift 2 ;;
        --python) [[ $# -ge 2 ]] || die '--python requires a value'; PYTHON="$2"; shift 2 ;;
        --skip-keygen) SKIP_KEYGEN=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[[ -n "$MAC" && -n "$SERIAL" && -n "$NONCE" ]] || { usage; exit 2; }
"$PYTHON" -c 'import cryptography' 2>/dev/null || die 'Python cryptography package is required'
if [[ ! -f "$PRIVATE_KEY" ]]; then
    [[ $SKIP_KEYGEN -eq 0 ]] || die "private key not found: $PRIVATE_KEY"
    mkdir -p "$(dirname "$PRIVATE_KEY")"
    "$PYTHON" "$TOOL" generate-key --private-key "$PRIVATE_KEY"
fi
"$PYTHON" "$TOOL" export-public --private-key "$PRIVATE_KEY" --output "$PUBLIC_HEADER"

ISSUE_LOG="$(mktemp)"
trap 'rm -f "$ISSUE_LOG"' EXIT
"$PYTHON" "$TOOL" issue --private-key "$PRIVATE_KEY" --output "$OUTPUT" \
    --mac "$MAC" --serial "$SERIAL" --nonce "$NONCE" | tee "$ISSUE_LOG"
COMMAND="$(awk '/^ACTIVATE / {sub(/^ACTIVATE /, ""); print; exit}' "$ISSUE_LOG")"
[[ -n "$COMMAND" ]] || die 'unlock_token.py did not produce an ACTIVATE frame'

if [[ -n "$PORT" ]]; then
    [[ -w "$PORT" ]] || die "USB Serial-JTAG port is not writable: $PORT"
    printf 'ACTIVATE %s\r\n' "$COMMAND" > "$PORT"
    printf 'sent ACTIVATE; monitor %s for ATC_OK/INVALID_FOR_DEVICE/ERR_LOCKED\n' "$PORT"
else
    printf 'send through USB Serial-JTAG: ACTIVATE %s\n' "$COMMAND"
fi
printf 'token file is an audit artifact only: %s (never copy it to TF card)\n' "$OUTPUT"
