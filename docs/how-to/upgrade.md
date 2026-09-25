# Upgrade

How to move an installation to a newer revision of this project, and back.

Your settings (`.env`), secrets (`secrets/`), overlay (`config/overlay/`) and the data volume carry
over; only the images and the Compose files change. Read `CHANGELOG.md` for the revision you move to
first.

## Upgrade to a newer revision

```bash
git fetch
git log --oneline HEAD..origin/master     # what changes
git pull
make build
docker compose up -d                      # air-gapped: COMPOSE_FILE in .env selects the override
docker compose run --rm uc generate       # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

`docker compose up -d` recreates the services whose image or configuration changed. Check that
`last_run.result` is `success` and that `update_center2` names the version you expect.

Air-gapped hosts without internet access cannot `git pull`: the clone came from a bundle, so its
`origin` never changes. On a connected machine, update your clone, `make build`, and create a new
image archive and bundle as in [Build the images](../getting-started/airgapped.md#2-build-the-images).
Then, on the air-gapped host:

```bash
git pull ../jenkins-uc.bundle master            # the Compose files and docs change too
gunzip -c ../jenkins-uc-images.tar.gz | docker load
docker compose up -d
docker compose run --rm uc generate
```

On Linux, if `git pull` cannot update a file under `secrets/` (such as its README) because
`secrets/` belongs to the containers' user, run it with `sudo` or fix that file's ownership first.

## Go back to the previous revision

```bash
git checkout <previous commit or tag>
make build
docker compose up -d
```

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
