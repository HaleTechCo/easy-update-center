# Rotate the signing certificate

How to issue a new signing certificate before the current one expires, how to replace the root CA, and
how to create the PKI so its root key never reaches the update center's host.

## When

The signing certificate lasts 365 days by default. Watch `signing_cert.days_left` in `status.json`
(or `docker compose run --rm uc status`) and rotate well before it reaches
`CERTIFICATE_MINIMUM_VALID_DAYS` (30): from then on every generation refuses to run, so the site
stops updating until you rotate. A calendar reminder 45 days before `signing_cert.not_after` works.

Controllers need no change: they trust the root CA, and the new certificate is issued by the same
root.

## Issue a new signing certificate

You need the root key from offline storage. Mount the directory holding `root-ca.key` (not the key
file, which is refused) read-only at `/setup/root`, here `/media/offline-usb/easy-update-center-root`:

```bash
docker compose run --rm -v /media/offline-usb/easy-update-center-root:/setup/root:ro setup pki-rotate
```

This writes a new `secrets/update-center.key` and `secrets/update-center.cert`, keeps the previous
pair as `*.old`, and sets the modes of everything in `secrets/`, as `setup secrets` does. The
root key is mounted read-only and only read; nothing on the offline media changes. If the directory
does not hold the key of `secrets/root-ca.crt` (a typo, or an old root after replacing it), it stops
with an error naming the file. The new pair is issued in a temporary directory inside `secrets/` and
only then renamed into place, so when a check, a step or a rename fails, `secrets/` is left as it
was.

Then make the running services use the new files. Compose mounts each secret file as it was when
the container started, so recreate the scheduler:

```bash
docker compose up -d --force-recreate scheduler
docker compose run --rm uc generate
docker compose run --rm uc status        # signing_cert.not_after is a year out
```

(Air-gapped, with `COMPOSE_FILE` set in `.env`, as in [Deploy air-gapped](../getting-started/airgapped.md#3-configure).) Return the root
key to offline storage. Once the new release is live, delete `secrets/*.old`.

### A passphrase-protected root key

`pki-rotate` never asks for a passphrase. It refuses an encrypted root key with
`uc: error: the root CA key <ROOT>/root-ca.key is encrypted; decrypt it to a temporary file for the
rotation (...)`. Decrypt it into a new directory only you can read, rotate with that directory
mounted as above, and delete the decrypted copy straight afterwards:

```bash
mkdir -m 700 /tmp/uc-root
openssl pkey -in /media/offline-usb/easy-update-center-root/root-ca.key -out /tmp/uc-root/root-ca.key  # asks for the passphrase
docker compose run --rm -v /tmp/uc-root:/setup/root:ro setup pki-rotate
rm -rf /tmp/uc-root
```

The encrypted key on the offline media is only read. Then go on as above.

## Replace the root CA

The root CA lasts ten years. Replacing it (at expiry, or if its key may have leaked) needs an
overlap, because controllers reject metadata signed under a root they do not trust yet:

1. Create a new PKI in a separate directory, `newpki/` in this repository (created if missing, and
   ignored by git, as `secrets/` is, since it holds the new root key):

   ```bash
   mkdir -p newpki
   docker compose run --rm -v "$PWD/newpki:/setup/out" setup pki-init --out /setup/out
   ```

   It works as the signing keys of first-time setup do: existing files are not overwritten, and the
   files get the modes they have in `secrets/`.
   Another directory name works too, but git does not ignore it.

2. Add `newpki/root-ca.crt` to `update-center-rootCAs/` on every controller, **keeping the old
   root** there. Jenkins trusts every certificate in that directory.
3. Once every controller has it, switch signing: move the new files into `secrets/`, replacing the
   old ones, set their modes, then recreate the scheduler and generate as above:

   ```bash
   mv newpki/update-center.key newpki/update-center.cert newpki/root-ca.crt secrets/
   docker compose run --rm setup secrets
   ```
4. Move `newpki/root/root-ca.key` offline.
5. Later, remove the old root from every controller.

## Where the root key lives

First-time setup (`docker compose run --rm setup`) writes the root CA's private key
to `secrets/root/root-ca.key`. It is needed only to issue signing certificates, once a year. Move
`secrets/root/` to offline storage (an encrypted USB key, a vault) right after setup: anyone holding
it can sign metadata your controllers accept. Keep a backup of it; losing it means replacing the
root CA on every controller.

## Create the PKI on an offline machine

Recommended: the root CA's key never touches the update center's host. On a machine with Docker and
no network access, with the uc image loaded (`docker load -i images.tar` from the air-gapped bundle,
or `docker save` of the image you run):

```bash
mkdir pki
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/pki:/out" \
  ghcr.io/haletechco/easy-update-center-uc:<version> pki init --out /out --display-dir pki
```

Copy `pki/root-ca.crt`, `pki/update-center.key` and `pki/update-center.cert` into `secrets/` on the
update center's host, then run `docker compose run --rm setup` there: it keeps the signing keys,
creates the credential files and sets the modes. Keep `pki/root/` on the offline machine (or offline
media): rotation mounts it read-only ([Issue a new signing certificate](#issue-a-new-signing-certificate)).
