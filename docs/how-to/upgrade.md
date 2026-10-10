# Upgrade

How to move an installation to a newer release or revision of this project, and back.

Your settings (`.env` and `config/uc.toml`), secrets (`secrets/`), overlay (`config/overlay/`) and
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
make reload                                     # or: docker compose up -d
docker compose run --rm uc status               # uc_version names the new release
```

`setup` creates what the new release needs, such as a credential file it added, and leaves what
exists. `uc config --check` prints `configuration ok`, or names the setting a newer release refuses.
`make reload` checks the settings once more before it recreates anything, and recreates nothing when
they are invalid; `docker compose up -d` does not check. You can verify the new images first:
[Verify a release](verify-a-release.md).

Air-gapped hosts cannot `git fetch`, and a bundle's source is an unpacked folder, not a clone. On a
connected machine, download and verify the new bundle, carry it across, then on the Docker host
`docker load -i images.tar` and unpack the new bundle's source as in
[Get the images](../getting-started/airgapped.md#2-get-the-images). Copy your `.env` and
`config/uc.toml`, `secrets/` and `config/overlay/` into the new folder, set `UC_IMAGE` and
`WEB_IMAGE` in `.env`, and run the commands from `setup` on, in that folder. The
Compose project is named `easy-update-center` by `compose.yaml`, so the new folder keeps the data
volume of the old one.

## Upgrade from source

```bash
git fetch
git log --oneline HEAD..origin/main                # what changes
git pull
docker compose build                                 # or: make build
docker compose run --rm setup                        # or: make setup; safe to repeat
docker compose run --rm --no-deps uc config --check  # your settings, as the new revision reads them
docker compose up -d                                 # air-gapped: COMPOSE_FILE in .env selects the override
docker compose run --rm uc generate                  # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

Run `setup` after the images are rebuilt: it creates what a newer revision needs, such as
`secrets/repo_push_auth`, and leaves what exists. `make setup` builds the uc image only when it is
missing, so it does not replace an older one, which may lack `uc-setup`, the `setup` service's
entrypoint. Plain `make` prints the help and builds nothing; `make build` builds.

The last step of `setup` shares `./procurement` with the uc containers. On a filesystem without
ACLs, where uid 10001 cannot write the folder, that step can fail. The secrets and keys steps have
already run by then, and the error names the fallback: `make procure-stage`, `procure-record` and
`procure-clean`, which hand the folder over for each run. See
[Sharing ./procurement](../reference/cli.md#sharing-procurement).

Check the settings before `docker compose up -d`: a newer revision can refuse a value an older one
loaded, and a service whose settings do not load exits with an error instead of running.
`uc config --check` prints `configuration ok`, or `uc: error: ...` naming the setting to fix in
`.env` or `config/uc.toml`. Revisions since `config/uc.toml` was added refuse any `@` in `PUBLIC_URL`,
`REPO_URL`, `DOWNLOAD_BACKEND_URL` and `PROCUREMENT_SOURCE_URL`, and an `HTTPS_PROXY` or
`HTTP_PROXY` whose host and port urllib cannot use (such as `http://proxy:3128?x`, or a port that is
not a number); see `CHANGELOG.md`. Instead of `docker compose up -d` you can run `make reload`,
which checks the settings first and recreates nothing when they are invalid.

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

On Linux, if `git pull` cannot update a file under `secrets/` (such as its README), an older
`make pki-init` gave `secrets/` to uid 10001: run `make secrets-permissions` first. Until the pull,
you are on the older revision, which may not have the `setup` service yet.

## Upgrading from jenkins-update-center

The project was renamed Easy Update Center for Jenkins, and its Compose project with it, from
`jenkins-update-center` to `easy-update-center`. Docker names a stack's containers and volumes after
its Compose project, so after the rename `docker compose up -d` would start a second stack on new,
empty volumes, and fail to bind the ports the old one still holds. Move the data across once:

```bash
docker compose -p jenkins-update-center down       # the old stack; its volumes stay
git pull                                            # or check out the release you move to
docker compose build                                # or set UC_IMAGE and WEB_IMAGE to a release
docker compose run --rm --no-deps --user 0:0 --entrypoint cp \
  -v jenkins-update-center_uc-data:/from:ro uc -a /from/. /data/
docker compose run --rm setup
docker compose run --rm --no-deps uc config --check
docker compose up -d
docker compose run --rm uc status                   # the live_release you had before
```

The copy runs as root so `cp -a` keeps ownership; `web-config` is recreated from your settings, so
only `uc-data` is copied. If `.env` sets `UC_IMAGE` or `WEB_IMAGE` to `jenkins-uc/...`, change them to
`easy-update-center/uc:local` and `easy-update-center/web:local` (or a release's images) first. If
`.env` sets `COMPOSE_PROJECT_NAME`, that name wins over the new one and nothing needs moving.

Once the new stack serves what the old one did, remove what the old one left:

```bash
docker volume rm jenkins-update-center_uc-data jenkins-update-center_web-config
docker image rm jenkins-uc/uc:local jenkins-uc/uc-test:local jenkins-uc/web:local
```

uc's HTTP User-Agent changed too, from `jenkins-uc/1` to
`easy-update-center/<version> (+https://github.com/HaleTechCo/easy-update-center)`: update a proxy
or firewall rule that matched the old one.

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

To go back to an earlier source revision: revisions before `config/uc.toml` existed ignore it, so
move the settings it holds back into `.env` first, or the services start without them.

```bash
git checkout <previous commit or tag>
docker compose build      # or: make build
docker compose up -d
```

A revision from before `docker compose build` could build the images (see `CHANGELOG.md`) has no
`build:` sections, so there `docker compose build` only warns `No services to build` and
`docker compose up -d` would run the newer images: use `make build`.

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
