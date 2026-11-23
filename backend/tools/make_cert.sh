#!/bin/sh
# Make a self-signed certificate for serving the page over HTTPS.
#
#     sh tools/make_cert.sh 192.168.22.8 OMDEV3
#
# Give every address and name the page is opened by: IP addresses and host
# names, in any order. A browser refuses the certificate for any other.
#
# Writes data/tls/cert.pem (public: import it into the browsers) and
# data/tls/key.pem (secret: never copy it anywhere, never commit it; "data/"
# is ignored by git). Needs openssl (on Windows: run it in Git Bash).
# An existing certificate is not overwritten: delete the files first.
set -eu

if [ $# -eq 0 ]; then
    echo "usage: sh tools/make_cert.sh ADDRESS_OR_NAME [...]" >&2
    exit 2
fi

dir="$(cd "$(dirname "$0")/../.." && pwd)/data/tls"
if [ -e "$dir/key.pem" ] || [ -e "$dir/cert.pem" ]; then
    echo "There is a certificate in $dir already. Delete cert.pem and key.pem to make a new one." >&2
    exit 1
fi

san=""
for name in "$@"; do
    case "$name" in
        *[!0-9.]*) entry="DNS:$name" ;;
        *) entry="IP:$name" ;;
    esac
    san="${san:+$san,}$entry"
done

mkdir -p "$dir"
umask 077
# MSYS_NO_PATHCONV: Git Bash would otherwise turn "/CN=..." into a file path
# (which is also why the files are named without their folder here).
# 825 days: the longest validity every browser accepts.
cd "$dir"
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 825 \
    -keyout key.pem -out cert.pem \
    -subj "/CN=$1/O=RobotClientWeb (self-signed)" \
    -addext "subjectAltName=$san" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null || {
    echo "openssl could not make the certificate." >&2
    exit 1
}

echo "Certificate: $dir/cert.pem"
echo "Key (secret): $dir/key.pem"
openssl x509 -in cert.pem -noout -enddate -fingerprint -sha256 -ext subjectAltName
