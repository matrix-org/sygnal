#!/bin/sh
set -eu

cd "$(dirname "$0")"

if [ ! -d out ]; then
  mkdir out
  chmod ugo+rwX out
fi

if [ ! -d mitmproxy ]; then
  mkdir mitmproxy
  chmod ugo+rwX mitmproxy
fi

if [ ! -f mitmproxy/mitmproxy-ca.pem ]; then
  openssl genrsa --out mitmproxy/ca.key 4096
  # Generate a mitmproxy CA
  # According to instructions from https://docs.mitmproxy.org/stable/concepts/certificates/
  openssl req -x509 -new -nodes -key mitmproxy/ca.key -sha256 -out mitmproxy/ca.crt -addext keyUsage=critical,keyCertSign -subj '/CN=MyOrg Root CA/C=GB/ST=MySt/L=MyL/O=MyOrg'
  cat mitmproxy/ca.key mitmproxy/ca.crt > mitmproxy/mitmproxy-ca.pem
  chmod ugo+rwX mitmproxy/ca.crt mitmproxy/ca.key mitmproxy/mitmproxy-ca.pem
fi

