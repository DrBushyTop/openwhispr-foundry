#!/bin/sh
# One-time: creates a self-signed code-signing certificate in your login
# keychain and trusts it for code signing. macOS ties permissions (microphone,
# system audio, accessibility) to the signing certificate, so a patched build
# signed with the same certificate keeps its permissions across rebuilds.
#
#   openwhispr-patch/create-signing-cert.sh
#
# macOS asks for your password once, to trust the certificate. Remove it later
# in Keychain Access (login > My Certificates).
set -eu

NAME="OpenWhispr Patched Local Signing"  # build.sh signs with this name (SIGN_ID)
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning | grep -q "\"$NAME\""; then
  echo "Signing identity \"$NAME\" already exists."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -config "$TMP/cert.cnf" 2>/dev/null
PASS="$(openssl rand -hex 16)"
# OpenSSL 3 needs -legacy for a PKCS#12 file macOS can import; LibreSSL has no such flag.
openssl pkcs12 -export -legacy -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
    -out "$TMP/id.p12" -passout "pass:$PASS" 2>/dev/null \
  || openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
    -out "$TMP/id.p12" -passout "pass:$PASS"

security import "$TMP/id.p12" -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign
echo "Trusting the certificate for code signing (macOS asks for your password)..."
security add-trusted-cert -p codeSign -k "$KEYCHAIN" "$TMP/cert.pem"

security find-identity -v -p codesigning | grep "\"$NAME\""
echo "Done. openwhispr-patch/build.sh signs with this identity."
