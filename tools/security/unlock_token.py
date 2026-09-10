#!/usr/bin/env python3
"""Issue device-bound P-256 authorization frames for USB Serial-JTAG.

The private key stays in the offline provisioning workstation.  The device
must first provide READ_ID and CHALLENGE values; the resulting frame is valid
only for that MAC, NVS serial and one challenge nonce.
"""

from __future__ import annotations

import argparse
import os
import secrets
import struct
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ModuleNotFoundError as exc:
    raise SystemExit("missing Python dependency 'cryptography'") from exc

MAGIC = b"EYEUNLK2"
VERSION = 2
KEY_ID = 1
SERIAL_SIZE = 16
PAYLOAD = struct.Struct("<8sBB2x16s6s16s")
SIGNATURE_MAX = 72


def load_p256_private(path: Path) -> ec.EllipticCurvePrivateKey:
    data = path.read_bytes()
    # The GUI imports PKCS#8 PEM (BEGIN PRIVATE KEY), while older factory
    # scripts generated OpenSSH PEM.  Accept both so a provisioning key can
    # be shared by the offline CLI and Two-factor_authentication without
    # converting it manually.
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        try:
            key = serialization.load_ssh_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise SystemExit(
                "unlock signing requires an unencrypted PKCS#8 or OpenSSH P-256 key"
            ) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise SystemExit("unlock signing requires an ECDSA P-256 key")
    return key


def load_p256_public(path: Path) -> ec.EllipticCurvePublicKey:
    data = path.read_bytes()
    try:
        key = serialization.load_ssh_public_key(data)
    except ValueError:
        try:
            key = serialization.load_pem_public_key(data)
        except ValueError as exc:
            raise SystemExit(
                "unlock verification requires an SSH or PEM P-256 public key"
            ) from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise SystemExit("unlock verification requires an ECDSA P-256 SSH public key")
    return key


def hex_bytes(value: str, size: int, label: str) -> bytes:
    value = value.replace(":", "").replace("-", "")
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise SystemExit(f"{label} is not hexadecimal") from exc
    if len(data) != size:
        raise SystemExit(f"{label} must contain exactly {size} bytes")
    return data


def verify_token_data(public_key: ec.EllipticCurvePublicKey, token: bytes) -> None:
    if len(token) <= PAYLOAD.size + 1:
        raise SystemExit("token is truncated")
    magic, version, key_id, nonce, mac, serial = PAYLOAD.unpack(token[: PAYLOAD.size])
    if magic != MAGIC or version != VERSION or key_id != KEY_ID:
        raise SystemExit("token header is invalid")
    signature_size = token[PAYLOAD.size]
    signature = token[PAYLOAD.size + 1 :]
    if not 1 <= signature_size <= SIGNATURE_MAX or len(signature) != signature_size:
        raise SystemExit("token signature length is invalid")
    try:
        public_key.verify(signature, token[: PAYLOAD.size], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SystemExit("token signature is invalid") from exc
    print(f"nonce={nonce.hex().upper()}")
    print(f"mac={mac.hex(':').upper()}")
    print(f"serial={serial.hex().upper()}")


def generate_key(path: Path) -> None:
    if path.exists() or path.with_name(path.name + ".pub").exists():
        raise SystemExit(f"refusing to overwrite existing key: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        # PKCS#8 is accepted by WebCrypto and is the interchange format used
        # by the Two-factor_authentication GUI.  The loader above keeps
        # backward compatibility with existing OpenSSH factory keys.
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.with_name(path.name + ".pub").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" eyecare-production-unlock\n"
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"generated private key: {path}")
    print(f"generated public key:  {path}.pub")


def issue_token(key_path: Path, output: Path, mac: str, serial: str, nonce: str | None) -> None:
    key = load_p256_private(key_path)
    mac_bytes = hex_bytes(mac, 6, "MAC")
    serial_bytes = hex_bytes(serial, SERIAL_SIZE, "serial")
    nonce_bytes = hex_bytes(nonce, 16, "nonce") if nonce else secrets.token_bytes(16)
    payload = PAYLOAD.pack(MAGIC, VERSION, KEY_ID, nonce_bytes, mac_bytes, serial_bytes)
    signature = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    if len(signature) > SIGNATURE_MAX:
        raise SystemExit("unexpected P-256 DER signature length")
    token = payload + bytes([len(signature)]) + signature
    verify_token_data(key.public_key(), token)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(token)
    print(f"wrote device-bound token: {output}")
    print(f"ACTIVATE {token.hex().upper()}")


def verify_token(public_key_path: Path, token_path: Path) -> None:
    verify_token_data(load_p256_public(public_key_path), token_path.read_bytes())
    print("valid device-bound unlock token")


def export_public(key_path: Path, output: Path) -> None:
    key = load_p256_private(key_path)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    rows = []
    for offset in range(0, len(der), 12):
        values = der[offset : offset + 12]
        rows.append("    " + ", ".join(f"0x{value:02x}" for value in values) + ",")
    output.write_text(
        "#pragma once\n\n#include <stddef.h>\n#include <stdint.h>\n\n"
        "/* Generated public key only; never place the matching private key in firmware. */\n"
        "static const uint8_t EYECARE_UNLOCK_PUBLIC_KEY_DER[] = {\n"
        + "\n".join(rows)
        + "\n};\n\nstatic const size_t EYECARE_UNLOCK_PUBLIC_KEY_DER_LEN =\n"
        "    sizeof(EYECARE_UNLOCK_PUBLIC_KEY_DER);\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"exported public-key header: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate-key")
    generate.add_argument("--private-key", type=Path, required=True)
    issue = sub.add_parser("issue", help="issue a token for one device challenge")
    issue.add_argument("--private-key", type=Path, required=True)
    issue.add_argument("--output", type=Path, required=True)
    issue.add_argument("--mac", required=True, help="6-byte MAC, e.g. AA:BB:CC:DD:EE:FF")
    issue.add_argument("--serial", required=True, help="16-byte NVS serial as 32 hex digits")
    issue.add_argument("--nonce", help="16-byte CHALLENGE value as 32 hex digits")
    export = sub.add_parser("export-public")
    export.add_argument("--private-key", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--token", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate-key":
        generate_key(args.private_key)
    elif args.command == "issue":
        issue_token(args.private_key, args.output, args.mac, args.serial, args.nonce)
    elif args.command == "export-public":
        export_public(args.private_key, args.output)
    else:
        verify_token(args.public_key, args.token)


if __name__ == "__main__":
    main()
