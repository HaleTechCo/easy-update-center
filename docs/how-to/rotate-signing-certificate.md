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

You need the root key from offline storage. Point `ROOT` at the directory holding `root-ca.key`, here
`/media/offline-usb/jenkins-uc-root`:

```bash
make pki-rotate ROOT=/media/offline-usb/jenkins-uc-root
```

This writes a new `secrets/update-center.key` and `secrets/update-center.cert`, keeps the previous
pair as `*.old`, and sets their permissions like the rest of `secrets/`. The root key is mounted
read-only and only read; nothing on the offline media changes.

Then make the running services use the new files. Compose mounts each secret file as it was when
the container started, so recreate the scheduler:

```bash
docker compose up -d --force-recreate scheduler
docker compose run --rm uc generate
docker compose run --rm uc status        # signing_cert.not_after is a year out
```

(Air-gapped, with `COMPOSE_FILE` set in `.env`, as in [Deploy air-gapped](../getting-started/airgapped.md#3-configure).) Return the root
key to offline storage. Once the new release is live, delete `secrets/*.old`.

## Replace the root CA

The root CA lasts ten years. Replacing it (at expiry, or if its key may have leaked) needs an
overlap, because controllers reject metadata signed under a root they do not trust yet:

1. Create a new PKI in a separate directory:

   ```bash
   mkdir -p newpki
   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/newpki:/pki" jenkins-uc/uc:local pki init --out /pki
   ```

2. Add `newpki/root-ca.crt` to `update-center-rootCAs/` on every controller, **keeping the old
   root** there. Jenkins trusts every certificate in that directory.
3. Once every controller has it, switch signing: move the new files into `secrets/`, replacing the
   old ones, set their permissions, then recreate the scheduler and generate as above:

   ```bash
   mv newpki/update-center.key newpki/update-center.cert newpki/root-ca.crt secrets/
   make secrets-permissions
   ```
4. Move `newpki/root/root-ca.key` offline.
5. Later, remove the old root from every controller.

## Where the root key lives

`make pki-init` writes the root CA's private key to `secrets/root/root-ca.key`. It is needed only to
issue signing certificates, once a year. Move `secrets/root/` to offline storage (an encrypted USB
key, a vault) right after `make pki-init`: anyone holding it can sign metadata your controllers
accept. Keep a backup of it; losing it means replacing the root CA on every controller.
