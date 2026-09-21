# Generate secrets:
```shell
mkdir -p secrets

openssl genrsa \
  -out secrets/update-center.key \
  4096

openssl req \
  -new \
  -x509 \
  -sha256 \
  -days 3650 \
  -key secrets/update-center.key \
  -out secrets/update-center.crt \
  -subj "/C=US/O=My Organization/OU=Jenkins Update Center/CN=jenkins-update-center"

chmod 600 secrets/update-center.key
chmod 644 secrets/update-center.crt
```
