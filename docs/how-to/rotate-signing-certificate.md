# Rotate the signing certificate

How to keep the PKI that signs the metadata: where the root key lives, how to issue the next signing
certificate, how to replace the root, and what to do if a key may have leaked.

## Where the root key lives

Anyone holding the root CA's key, or the signing key, can sign metadata that every controller trusting
your root accepts, and so put any plugin on them. Jenkins checks no revocation, so neither key can be
withdrawn once it leaks ([If a key may have leaked](#if-a-key-may-have-leaked)). For production,
create the PKI on an offline machine, so the root key never touches the update center's host.

First-time setup (`docker compose run --rm setup`) creates the PKI on the host, for trials: it writes
the root key to `secrets/root/root-ca.key`, and every run of `setup` warns while it is there. Move
`secrets/root/` to offline storage (an encrypted USB key, a vault) and keep a backup of it: losing it
means replacing the root CA on every controller.

## Create the PKI on an offline machine

On a machine with Docker and no network access, with the uc image loaded (`docker load -i images.tar`
from the air-gapped bundle, or `docker save` of the image you run):

```bash
mkdir pki
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/pki:/out" \
  ghcr.io/haletechco/easy-update-center-uc:<version> pki init --out /out --display-dir pki
```

Copy `pki/root-ca.crt`, `pki/update-center.key` and `pki/update-center.cert` into `secrets/` on the
update center's host, then run `docker compose run --rm setup` there: it keeps the signing keys,
creates the credential files and sets the modes. `pki/` and its `root/` stay on the offline machine.

## When to rotate

The signing certificate lasts 180 days by default. Watch `signing_cert.days_left` in `status.json`
(or `docker compose run --rm uc status`) and rotate well before it reaches
`CERTIFICATE_MINIMUM_VALID_DAYS` (30): from then on every generation refuses to run, so the site
stops updating until you rotate. A calendar reminder 45 days before `signing_cert.not_after` works:
about every four and a half months.

Controllers need no change: they trust the root CA, and the new certificate is issued by the same
root.

## Issue a new signing certificate

On the offline machine, in the folder holding `pki/`:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/pki:/pki" \
  ghcr.io/haletechco/easy-update-center-uc:<version> pki rotate --dir /pki --root /pki/root --display-dir pki
```

It writes a new `pki/update-center.key` and `pki/update-center.cert` and keeps the previous pair as
`*.old`. It never asks for a passphrase: decrypt a passphrase-protected root key into a new folder on
the offline machine, pass that folder as `--root`, and delete the decrypted copy straight afterwards.

Copy the new `update-center.key` and `update-center.cert` into `secrets/` on the host, then make the
services use them. Compose mounts each secret file as it was when the container started, so recreate
the scheduler:

```bash
docker compose run --rm setup secrets            # the files' modes
docker compose up -d --force-recreate scheduler
docker compose run --rm uc generate
docker compose run --rm uc status                # signing_cert.not_after is 180 days out
```

### With the root key on removable media

`setup` can issue the certificate on the host from media you mount there. Mount the directory holding
`root-ca.key` (not the key file, which is refused) read-only at `/setup/root`, here
`/media/offline-usb/easy-update-center-root`:

```bash
docker compose run --rm -v /media/offline-usb/easy-update-center-root:/setup/root:ro setup pki-rotate
```

This writes a new `secrets/update-center.key` and `secrets/update-center.cert`, keeps the previous
pair as `*.old`, and sets the modes of everything in `secrets/`. The root key is only read. When
anything fails, such as a root key that does not belong to `secrets/root-ca.crt`, it stops with an
error naming the file and leaves `secrets/` as it was. A passphrase-protected root key needs the
offline machine above. Then recreate the scheduler and generate as above, and unmount the media.

Once the new release is live, delete `secrets/*.old`.

## Replace the root CA

The root CA lasts ten years. Replacing it on schedule needs an overlap, because controllers reject
metadata signed under a root they do not trust yet. (If a key may have leaked, there is no overlap:
see the next section.)

1. Create a new PKI on the offline machine, in a new folder, as in
   [Create the PKI on an offline machine](#create-the-pki-on-an-offline-machine).
2. Add its `root-ca.crt` to `update-center-rootCAs/` on every controller, **keeping the old root**
   there. Jenkins trusts every certificate in that directory.
3. Once every controller has it, switch signing: copy the new `root-ca.crt`, `update-center.key` and
   `update-center.cert` into `secrets/` on the host, replacing the old ones, run
   `docker compose run --rm setup secrets`, then recreate the scheduler and generate as above.
4. Later, remove the old root from every controller.

## If a key may have leaked

Jenkins checks no revocation. A leaked signing key signs metadata your controllers accept until its
certificate expires (up to 180 days), and a leaked root key until the root is removed from them;
issuing a new signing certificate does not stop the old one. The only remedy is a new root, without
the overlap. This applies whether the signing key or the root key leaked:

1. Stop the scheduler: `docker compose stop scheduler`.
2. Create a new PKI on the offline machine, as in
   [Create the PKI on an offline machine](#create-the-pki-on-an-offline-machine).
3. Copy its `root-ca.crt`, `update-center.key` and `update-center.cert` into `secrets/` on the host,
   run `docker compose run --rm setup secrets`, start the scheduler and run `uc generate`.
4. On every controller, in one change: add the new `root-ca.crt` to `update-center-rootCAs/` **and
   remove the old root**. A controller refuses this update center's metadata until it has the new
   root; the leaked key is worthless to it from then on.
5. Find out how the key leaked before you trust the host again.
