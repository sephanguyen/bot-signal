#!/bin/bash
# ============================================
# 🔐 Mã hóa .env file bằng age
# age: modern encryption tool, nhẹ, chạy tốt trên ARM/Pi
# https://github.com/FiloSottile/age
# ============================================

set -e

ENV_FILE="${1:-.env}"
ENCRYPTED_FILE="${ENV_FILE}.age"
KEY_FILE="$HOME/.openclaw-key.txt"

# Install age nếu chưa có
if ! command -v age &> /dev/null; then
    echo "📦 Installing age..."
    sudo apt-get install -y age
fi

# Tạo key nếu chưa có
if [ ! -f "$KEY_FILE" ]; then
    echo "🔑 Generating encryption key..."
    age-keygen -o "$KEY_FILE" 2>&1
    chmod 600 "$KEY_FILE"
    echo "   Key saved to: $KEY_FILE"
    echo "   ⚠️  BACKUP key file này! Mất key = mất data."
fi

# Lấy public key
PUBLIC_KEY=$(grep "public key:" "$KEY_FILE" | awk '{print $NF}')

# Encrypt
echo "🔐 Encrypting ${ENV_FILE}..."
age -r "$PUBLIC_KEY" -o "$ENCRYPTED_FILE" "$ENV_FILE"

# Xóa plaintext
rm -f "$ENV_FILE"
echo "🗑️  Deleted plaintext ${ENV_FILE}"
echo "✅ Encrypted: ${ENCRYPTED_FILE}"
echo ""
echo "Để decrypt khi cần:"
echo "  age -d -i $KEY_FILE -o .env .env.age"
