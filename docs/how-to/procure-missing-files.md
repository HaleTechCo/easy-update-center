# Procure missing POMs, dependencies and cores

How to let `uc doctor` download what your repository lacks, review it, and push it into the repository.

## When to use it

`uc doctor` reports what the generator will drop or degrade: plugins without a usable POM, required
dependencies the repository lacks, and cores plugins require (without which their tiers are missing).
It can also fetch those files from repo.jenkins-ci.org into a staging folder, `./procurement/`, for your
team to review, then push exactly the reviewed files to the repository. Nothing reaches the repository
without the separate push step.

## Before you start

- The update center must run **online**: downloads come from repo.jenkins-ci.org. Air-gapped, they are
  refused unless `PROCUREMENT_SOURCE_URL` points at a copy the update center can reach.
- The repository must be your own (`REPO_URL`, `REPO_KEY`): push never writes to repo.jenkins-ci.org.
- An account that can write to the repository, as `user:token`, in `secrets/repo_push_auth` (Artifact
  Keeper: a user name and an API token with the `write:artifacts` scope). Keep it separate from the
  read-only `repo_username`/`repo_password`: only the one-shot `uc` service ever receives it.

  ```bash
  make secrets-placeholders        # creates secrets/repo_push_auth if missing
  printf '%s' 'svc-upload:token' > secrets/repo_push_auth
  ```

- On Artifactory, give that account **deploy** permission but not delete or overwrite permission:
  push only uploads paths that do not exist yet, but Artifactory offers no create-only upload, so an
  account allowed to overwrite could replace a file uploaded by someone else in the moment between
  push's check and its own upload.

## 1. Stage

```bash
make procure-download ARGS="--fetch --download-poms --download-dependencies --download-war required"
```

It prints the doctor report, then one line per file. Keep `--fetch`: it reads the POMs and manifests
the generator has not cached yet, and without them the run cannot see every missing dependency or
required core (a `warning` line before the summary then says how many releases went unchecked). The
download options combine freely:

| Option | Stages |
|---|---|
| `--download-poms` | The real `.pom` of every plugin whose POM is missing, derived from its archive, or unusable. |
| `--download-dependencies` | Each required dependency the repository lacks or holds only in an older version, at the highest minimum any plugin declares, with its `.pom`; then their dependencies, until nothing is missing. Optional dependencies are skipped. |
| `--download-war VERSION` | That core's `.war` and `.pom`. Repeatable. |
| `--download-war required` | Every core plugins require that the repository lacks, at or above the oldest core it holds, or `--since VERSION`. With no core in the repository, `--since` is needed: use the oldest version your controllers run. It is also refused for the public repository: name the versions instead. |

`make procure-download` lends `./procurement/` to the container (uid 10001) for the run and hands it
back to you afterwards. On Docker Desktop, plain Compose works too:
`docker compose run --rm uc doctor --fetch --download-poms`. A run exits 0 when everything planned is
staged, 1 otherwise; files it could not get are listed as `FAILED` with the reason. Running it again
keeps what is already staged and downloads only what is missing or changed.

### Files you place by hand

Push uploads only what `procurement.json` lists, so a file you copy into `./procurement/` yourself (a
WAR downloaded elsewhere, a plugin from another source) must be recorded first. Put it at its Maven
path, then:

```bash
make procure-download ARGS=--stage-local
```

It records every `.hpi`, `.jpi`, `.war` and `.pom` in the folder that `procurement.json` does not list
yet, with the reason `added by hand`, after the checks a download gets: a plugin's manifest must name
the group, artifact and version of its path, a `.pom` must be a usable POM for its path, and a `.war`
must be a readable archive. Anything refused is listed as `REFUSED` with the reason, and the run then
exits 1. It needs no network access, so it works air-gapped too. `--stage-local` runs on its own:
download and record hand-placed files in separate runs.

## 2. Review

Everything staged is in `./procurement/`, in the paths it will have in the repository, and listed in
`./procurement/procurement.json` with its source URL, sha1 and reason:

- `reason`: why it is there, for example `active-directory 2.967.v1 requires mailer 534.v1`;
- `required_core`: for a plugin, the Jenkins version it needs; the run also points out any newer than
  the newest core in the repository;
- `replaces_unusable`: the repository holds an unusable file at this path (such as a saved error page).
  Delete that file in the repository before pushing: push never overwrites. On Artifact Keeper,
  deleting a release leaves a tombstone that refuses different bytes at that path (HTTP 409,
  `Artifact version already exists and is immutable`) until its storage garbage collection has run:
  as an Artifact Keeper administrator, `POST /api/v1/repositories/<key>/storage-gc` with
  `{"dry_run": true}`, then `{"dry_run": false}` (it also runs on Artifact Keeper's own schedule).

Remove what your process rejects by deleting its entry from `procurement.json` (and the file). Push
refuses a file whose content changed, or that went missing, after it was downloaded.

## 3. Push

```bash
make procure-push ARGS=--dry-run   # check each file, upload nothing
make procure-push
```

The first time you push to a repository, run the dry run first, then a real push, then
`uc doctor --fetch` (next section), so a misconfigured `repo_push_auth` or a changed file shows up
before anything is written.

Push sends each file's sha1 with the upload (`X-Checksum-Sha1`), so Artifactory can verify it, and
never uploads over a path the repository already holds, even when the repository publishes no
checksum for it.

| Result | Meaning |
|---|---|
| `uploaded` | Uploaded, and the repository's sha1 matches. |
| `present` | The repository already holds the same file; also shown when the same file arrives from elsewhere while pushing (an HTTP 409 whose sha1 matches). |
| `would put` | Dry run: it would be uploaded. |
| `DIFFERENT` | The repository holds another file at that path; it is left alone. |
| `TAMPERED` | The staged file changed, or is missing, since it was downloaded: download it again. Or it cannot be read: check its permissions. |
| `ERROR`, `FAILED` | The repository refused or failed (for example 401: check `secrets/repo_push_auth`), or holds the path but publishes no checksum for it (check that file by hand). `FAILED` shows the repository's own reason when it gives one; a 409 from Artifact Keeper after you deleted a file is its tombstone (see [Review](#2-review)). |
| `unlisted` | In the folder but not in `procurement.json`, so not pushed: record it with `--stage-local` ([Files you place by hand](#files-you-place-by-hand)) or delete it. The summary counts these. |

Push exits 0 when every listed file ends up in the repository with the recorded content; unlisted
files do not change the exit code.

## 4. Confirm

```bash
docker compose run --rm uc doctor --fetch
```

The next generation publishes the new files; `docker compose run --rm uc generate` publishes at once.

## Limits

- Versions: a dependency is staged at the minimum plugins declare, not the newest. Security warnings
  still flag old versions you stock.
- Plugins never published on repo.jenkins-ci.org (internal ones) cannot be downloaded from it.
- Plugins Jenkins adds implicitly for plugins built against old cores ("detached" plugins) are not
  followed.
