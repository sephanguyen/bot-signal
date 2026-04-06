#!/bin/bash
# 🔓 Decrypt .env.age → .env (tạm thời, dùng xong xóa)

set -e

KEY_FILE="$HOME/.openclaw-key.txt"
ENCRYPTED_FILE="${1:-.env.age}"
ENV_FILE=".env"

if [ ! -f "$KEY_FILE" ]; then
    echo "❌ Key file not found: $KEY_FILE"
    exit 1
fi

if [ ! -f "$ENCRYPTED_FILE" ]; then
    echo "❌ Encrypted file not found: $ENCRYPTED_FILE"
    exit 1
fi

age -d -i "$KEY_FILE" -o "$ENV_FILE" "$ENCRYPTED_FILE"
chmod 600 "$ENV_FILE"
echo "✅ Decrypted: $ENV_FILE"
echo "⚠️  Nhớ xóa .env sau khi dùng xong: rm .env"
