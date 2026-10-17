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
git checkout v<new version>                     # the Compose files, which name its images, and docs of that release
docker compose pull                             # air-gapped: docker load -i images.tar from the new bundle, and unpack its source
docker compose run --rm setup
docker compose run --rm --no-deps uc config --check
docker compose up -d
docker compose run --rm --no-deps uc --version   # the new release
```

`uc --version` names the release the images are; `uc status` shows `uc_version` of the live
release, so it names the new one only after the next generation (`docker compose run --rm uc generate`
publishes now).

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

Files the release tracks are replaced by the release's, so a change you made to one is lost. Files
you added stay:

- `.env`, `secrets/`, `config/overlay/` and `procurement/`;
- the Compose overrides you created beside `compose.yaml`, and the folders they mount, such as `tls/`.

Files a release dropped are not deleted: remove any you recognise. Then run the commands from
`setup` on, in that folder. The data volume stays the same: `compose.yaml` names the Compose
project `easy-update-center`.

## Upgrade from source

For a deployment built from source, with `compose.build.yaml` in `COMPOSE_FILE`
([Or build from source](../getting-started/online.md#or-build-from-source)):

```bash
git fetch
git checkout main
git log --oneline HEAD..origin/main                # what changes
git pull
docker compose build
docker compose run --rm setup                        # safe to repeat
docker compose run --rm --no-deps uc config --check  # your settings, as the new revision reads them
docker compose up -d
docker compose run --rm uc generate                  # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

Run `setup` after the images are rebuilt, and check the settings before `docker compose up -d`, as
for a release: a service whose settings do not load exits instead of running.

`docker compose up -d` recreates the services whose image or configuration changed. Check that
`last_run.result` is `success` and that `update_center2` names the version you expect.

Air-gapped hosts without internet access cannot `git pull`: the clone came from a bundle, so its
`origin` never changes. On a connected machine, update your clone, run `docker compose build`, and create a new image
archive and bundle as in
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

For a release, check out the previous tag, whose `compose.yaml` names its images:

```bash
git checkout v<previous version>
docker compose pull
docker compose up -d
```

Air-gapped, load the previous bundle's images and unpack its source instead of pulling.

To go back to an earlier source revision:

```bash
git checkout <previous commit or tag>
docker compose build
docker compose up -d
```

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
