# Rotate the signing certificate

How to issue a new signing certificate before the current one expires, and how to replace the root CA.

## When

The signing certificate lasts 365 days by default. Watch `signing_cert.days_left` in `status.json`
(or `docker compose run --rm uc status`) and rotate well before it reaches
`CERTIFICATE_MINIMUM_VALID_DAYS` (30): from then on every generation refuses to run, so the site
stops updating until you rotate. A calendar reminder 45 days before `signing_cert.not_after` works.

Controllers need no change: they trust the root CA, and the new certificate is issued by the same
root.

## Issue a new signing certificate

You need the root key from offline storage, here mounted from `/offline/root` (the directory holding
`root-ca.key`):

```bash
docker run --rm --user 0:0 --entrypoint sh -v "$PWD/secrets:/pki" -v /offline/root:/pki/root \
  jenkins-uc/uc:local -c 'uc pki rotate --dir /pki && chown -R 10001:10001 /pki'
```

This writes a new `secrets/update-center.key` and `secrets/update-center.cert` and keeps the
previous pair as `*.old`. From Git Bash on Windows, run it with `MSYS_NO_PATHCONV=1` set.

Then make the running services use the new files. Compose mounts each secret file as it was when
the container started, so recreate the scheduler:

```bash
docker compose up -d --force-recreate scheduler
docker compose run --rm uc generate
docker compose run --rm uc status        # signing_cert.not_after is a year out
```

(Air-gapped, add `-f compose.yaml -f compose.airgapped.yaml`, or set `COMPOSE_FILE`.) Return the root
key to offline storage. Once the new release is live, delete `secrets/*.old`.

## Replace the root CA

The root CA lasts ten years. Replacing it (at expiry, or if its key may have leaked) needs an
overlap, because controllers reject metadata signed under a root they do not trust yet:

1. Create a new PKI in a separate directory:

   ```bash
   mkdir -p newpki
   docker run --rm --user 0:0 --entrypoint sh -v "$PWD/newpki:/pki" \
     jenkins-uc/uc:local -c 'uc pki init --out /pki && chown -R 10001:10001 /pki'
   ```

2. Add `newpki/root-ca.crt` to `update-center-rootCAs/` on every controller, **keeping the old
   root** there. Jenkins trusts every certificate in that directory.
3. Once every controller has it, switch signing: move `newpki/update-center.key`,
   `newpki/update-center.cert` and `newpki/root-ca.crt` into `secrets/`, replacing the old files,
   then recreate the scheduler and generate as above.
4. Move `newpki/root/root-ca.key` offline.
5. Later, remove the old root from every controller.

## Where the root key lives

`make pki-init` writes the root CA's private key to `secrets/root/root-ca.key`. It is needed only to
issue signing certificates, once a year. Move `secrets/root/` to offline storage (an encrypted USB
key, a vault) right after `make pki-init`: anyone holding it can sign metadata your controllers
accept. Keep a backup of it; losing it means replacing the root CA on every controller.
