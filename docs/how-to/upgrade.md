# Upgrade

How to move an installation to a newer release or revision of this project, and back.

Your settings (`.env`), secrets (`secrets/`), overlay (`config/overlay/`) and
the data volume carry over; only the images and the Compose files change. Read `CHANGELOG.md` for
the release or revision you move to first.

## Upgrade to a newer release

Read the release's section of `CHANGELOG.md` first: it lists what changed and anything you must do.
Then, from the folder you deployed in:

```bash
git fetch --tags
git checkout v<new version>                     # the Compose files and docs of that release
# in .env: UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:<new version>
#          WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:<new version>
docker compose pull                             # air-gapped: docker load -i images.tar from the new bundle, and unpack its source
docker compose run --rm setup
docker compose run --rm --no-deps uc config --check
docker compose up -d
docker compose run --rm uc status               # uc_version names the new release
```

`setup` creates what the new release needs, such as a credential file it added, and leaves what
exists. `uc config --check` prints `configuration ok`, or names the setting a newer release refuses.
You can verify the new images first:
[Verify a release](verify-a-release.md).

Air-gapped hosts cannot `git fetch`, and a bundle's source is an unpacked folder, not a clone. On a
connected machine, download and verify the new bundle, carry it across, then on the Docker host
`docker load -i images.tar`, then unpack the new bundle's source over the folder you deployed in,
without making a new folder:

```bash
tar -xzf easy-update-center-<new version>.tar.gz --strip-components=1 -C <deployment folder>
```

Files you added stay (`.env`, `secrets/`, `config/overlay/`, `procurement/`, the
Compose overrides you created beside `compose.yaml` and the folders they mount, such as `tls/`);
files the release tracks are replaced by the release's, so a change you made to one of them is lost.
Files a release dropped are not deleted: remove any you recognise. Then set `UC_IMAGE` and
`WEB_IMAGE` in `.env` and run the commands from `setup` on, in that folder. The Compose project is
named `easy-update-center` by `compose.yaml`, so the data volume stays the same.

## Upgrade from source

```bash
git fetch
git checkout main
git log --oneline HEAD..origin/main                # what changes
git pull
docker compose build                                 # or: make build
docker compose run --rm setup                        # safe to repeat
docker compose run --rm --no-deps uc config --check  # your settings, as the new revision reads them
docker compose up -d                                 # air-gapped: COMPOSE_FILE in .env selects the override
docker compose run --rm uc generate                  # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

Run `setup` after the images are rebuilt: it creates what a newer revision needs, such as a
credential file it added, and leaves what exists.

Check the settings before `docker compose up -d`: a newer revision can refuse a value an older one
loaded, and a service whose settings do not load exits with an error instead of running.
`uc config --check` prints `configuration ok`, or `uc: error: ...` naming the setting to fix in
`.env`.

`docker compose up -d` recreates the services whose image or configuration changed. Check that
`last_run.result` is `success` and that `update_center2` names the version you expect.

Air-gapped hosts without internet access cannot `git pull`: the clone came from a bundle, so its
`origin` never changes. On a connected machine, update your clone, run `docker compose build` (or
`make build`), and create a new image archive and bundle as in
[Or build from source](../getting-started/airgapped.md#or-build-from-source).
Then, on the air-gapped host:

```bash
git pull ../easy-update-center.bundle main            # the Compose files and docs change too
gunzip -c ../easy-update-center-images.tar.gz | docker load
docker compose run --rm setup
docker compose run --rm --no-deps uc config --check
docker compose up -d
docker compose run --rm uc generate
```

## Go back to the previous revision

For a release, check out the previous tag and set the previous image tags in `.env`:

```bash
git checkout v<previous version>
# in .env: UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:<previous version>
#          WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:<previous version>
docker compose pull
docker compose up -d
```

Air-gapped, load the previous bundle's images and unpack its source instead of pulling.

To go back to an earlier source revision:

```bash
git checkout <previous commit or tag>
docker compose build      # or: make build
docker compose up -d
```

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
