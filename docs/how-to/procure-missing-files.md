# Procure missing POMs, dependencies and cores

How to stage what your repository lacks with `uc procure`, review it, push it into the repository,
and clean up afterwards.

## When to use it

`uc doctor` reports what the generator will drop or degrade: plugins without a usable POM, required
dependencies the repository lacks, and cores plugins require (without which their tiers are missing).
`uc procure stage` fetches those files from repo.jenkins-ci.org into a staging folder,
`./procurement/`, for your team to review, and `uc procure push` then pushes exactly the reviewed
files to the repository. Files you obtain elsewhere go through the same folder. Nothing reaches the
repository without the separate push step, and once the files are in, `uc procure clean` deletes
their staged copies.

| Stage | Command |
|---|---|
| [1. Check](#1-check) | `uc doctor` |
| [2. Stage](#2-stage) | `uc procure stage`, and `uc procure record` for files you add by hand |
| [3. Review](#3-review) | none: you read `./procurement/procurement.json` |
| [4. Push](#4-push) | `uc procure push` |
| [5. Confirm](#5-confirm) | `uc generate`, then `uc doctor` |
| [6. Clean up](#6-clean-up) | `uc procure clean` |

To run these stages with an approval before the push, and a record of who approved it:
[Run procurement from a pipeline](run-procurement-from-a-pipeline.md). Every command and option:
[uc procure](../reference/cli.md#uc-procure).

## Before you start

- The update center must run **online**: downloads come from repo.jenkins-ci.org. Air-gapped, they are
  refused unless `PROCUREMENT_SOURCE_URL` points at a copy the update center can reach.
- The repository must be your own (`REPO_URL`, `REPO_KEY`): push never writes to repo.jenkins-ci.org.
- An account that can write to the repository, as `user:token`, in `secrets/repo_push_auth` (Artifact
  Keeper: a user name and an API token with the `write:artifacts` scope). Push writes with it; clean
  only reads with it. Keep it separate from the read-only `repo_username`/`repo_password`: only the
  one-shot `uc` service ever receives it.

  ```bash
  docker compose run --rm setup secrets      # creates secrets/repo_push_auth if missing
  printf '%s' 'svc-upload:token' > secrets/repo_push_auth
  ```

- On Artifactory, give that account **deploy** permission, not delete or overwrite: Artifactory has
  no create-only upload, so an account allowed to overwrite could replace someone else's upload
  between push's check and its own.
- `./procurement/` must be writable by the uc containers and by you: every container runs as
  `UC_UID`:`UC_GID`, which on Linux you set to your own `id -u` and `id -g` in `.env`.

## 1. Check

```bash
docker compose run --rm uc doctor --fetch
```

It prints what the generator drops, and the POMs, dependencies and cores to procure, each with its
repo.jenkins-ci.org URL ([uc doctor](../reference/cli.md#uc-doctor); what to do about each section:
[Check with uc doctor](stock-airgapped-repository.md#check-with-uc-doctor)). When it finds something
to procure, it ends with a `next:` line naming the `uc procure stage` options for it; air-gapped
without `PROCUREMENT_SOURCE_URL`, the line says to place the files by hand instead
([Files you place by hand](#files-you-place-by-hand)). Its exit code 1 is the reason to procure, not
an error.

## 2. Stage

```bash
docker compose run --rm uc procure stage --poms --dependencies --war required
```

It prints the doctor report,
then one line per file. Choose what to stage with options; they combine freely:

| Option | Stages |
|---|---|
| `--poms` | The real `.pom` of every plugin whose POM is missing, derived from its archive, or unusable. |
| `--dependencies` | Each required dependency the repository lacks or holds only in an older version, at the highest minimum any plugin declares, with its `.pom`; then their dependencies, until nothing is missing. Optional dependencies are skipped. |
| `--war VERSION` | That core's `.war` and `.pom`. Repeatable. |
| `--war required` | Every core plugins require that the repository lacks, at or above the floor. |

With nothing chosen, the run stages nothing and exits 2, naming the options.

The floor of `--war required` is the oldest core in the repository, or `--since VERSION`
(`DOCTOR_SINCE`):

- With no core in the repository, `--since` is needed: the oldest version your controllers run.
- Older required cores are left out; the report names the oldest and the `--since` that stages them.
- Once an older core is in the repository, it is the floor for every later run.
- For the public repository it is refused: name the versions instead.

Before it stages, it reads the POMs and manifests the generator has not cached yet: without them the run cannot see every missing dependency or
required core, and a `warning` line before the summary then says how many releases went unchecked.
`--no-fetch` skips it for one run.

Each file lands in `./procurement/` at the path it will have in the repository, checked against the
`.sha1` published beside it, and is listed in `./procurement/procurement.json`. Staged files are
yours to edit or delete: they are readable by everyone and writable by you and the uc containers. A
run exits 0 when everything planned is staged, 1 otherwise; files it could not get are listed as
`FAILED` with the reason. Running it again keeps what is already staged and downloads only what is
missing or changed. It ends with the next step, also after a run with problems, since what it did
stage still needs review:

```text
next: review ./procurement (procurement.json lists 12 files), then uc procure push
```

### Files you place by hand

Push uploads only what `procurement.json` lists, so a file you copy into `./procurement/` yourself (a
WAR downloaded elsewhere, a plugin from another source) must be recorded first. Files and folders you
move or copy into it must be writable by the uc containers' user, `UC_UID`, so they can move and
delete them. Then:

```bash
docker compose run --rm uc procure record         # check and record; report misplaced files
docker compose run --rm uc procure record --fix   # move misplaced files first, then record them
```


It records every `.hpi`, `.jpi`, `.war` and `.pom` in the folder that `procurement.json` does not list
yet, with the reason `added by hand`, after the checks a download gets: a plugin's manifest must name
the group, artifact and version of its path, a core's `Jenkins-Version` (when its manifest has one)
the version of its path, and a `.pom` must be a usable POM for its path; archives must be readable.
Anything refused is listed as `REFUSED` with the reason, and the run then exits 1.

A file at the wrong path, or outside the Maven layout (such as the folder's top level), is refused
with the Maven path it belongs at, from the coordinates its manifest or POM declares. Move it there
yourself, or add `--fix`: each such file is moved (`moved`, with its new path), any folder it leaves
empty is removed, and it is then checked and recorded. `--fix` never moves a file onto a path that
holds another file, that `procurement.json` lists, or that leads out of the folder; it refuses that
file instead. A file that declares no coordinates, such as a plugin whose manifest names no group,
stays where it is.

It needs no network access, so it works air-gapped too. It ends with the same `next:` line as a
stage run.

## 3. Review

Everything staged is in `./procurement/`, in the paths it will have in the repository, and listed in
`./procurement/procurement.json` with its source URL, sha1, size, kind and reason:

- `reason`: why it is there, for example `active-directory 2.967.v1 requires mailer 534.v1`, or
  `added by hand`;
- `required_core`: for a plugin, the Jenkins version it needs; the stage run also points out any
  newer than the newest core in the repository;
- `replaces_unusable`: the repository holds an unusable file at this path (such as a saved error page).
  Delete that file in the repository before pushing: push never overwrites. On Artifact Keeper,
  deleting a release leaves a tombstone that refuses different bytes at that path (HTTP 409,
  `Artifact version already exists and is immutable`) until its storage garbage collection has run:
  as an Artifact Keeper administrator, `POST /api/v1/repositories/<key>/storage-gc` with
  `{"dry_run": true}`, then `{"dry_run": false}` (it also runs on Artifact Keeper's own schedule).

Remove what your process rejects by deleting its entry from `procurement.json` (and the file), as
yourself: what uc staged is yours, no `sudo` needed. Push refuses a
file whose content changed, or that went missing, after it was staged.

## 4. Push

```bash
docker compose run --rm uc procure push --dry-run   # check each file, upload nothing
docker compose run --rm uc procure push
```

The first time you push to a repository,
run the dry run first, then a real push, then `uc doctor --fetch` ([Confirm](#5-confirm)), so a
misconfigured `repo_push_auth` or a changed file shows up before anything is written.

Push sends each file's sha1 with the upload (`X-Checksum-Sha1`), so Artifactory can verify it, and
never uploads over a path the repository already holds, even when the repository publishes no
checksum for it. The one exception replaces nothing: bytes Artifact Keeper stored without recording
them, when they are the staged file's ([Large files](#large-files)).

| Result | Meaning |
|---|---|
| `uploaded` | Uploaded, and the repository's sha1 matches. With `Artifact Keeper had stored the same bytes without recording them`, the upload recorded a copy an earlier upload left behind ([Large files](#large-files)). |
| `present` | The repository already holds the same file; also shown when the same file arrives from elsewhere while pushing (an HTTP 409 whose sha1 matches). |
| `would put` | Dry run: it would be uploaded. |
| `DIFFERENT` | The repository holds another file at that path, or Artifact Keeper stored other bytes there without recording them; it is left alone. |
| `TAMPERED` | The staged file changed, or is missing, since it was downloaded: stage or record it again. Or it cannot be read: check its permissions. |
| `ERROR`, `FAILED` | The repository refused or failed (for example 401: check `secrets/repo_push_auth`), or Artifactory holds the path but publishes no checksum for it (check that file by hand). `FAILED` shows the repository's own reason when it gives one; a 409 from Artifact Keeper after you deleted a file is its tombstone (see [Review](#3-review)); a 503 `request timed out`, a 502 or a broken connection on a large file is a timeout ([Large files](#large-files)). |
| `unlisted` | In the folder but not in `procurement.json`, so not pushed: record it with `uc procure record` ([Files you place by hand](#files-you-place-by-hand)) or delete it. The summary counts these. |

Push exits 0 when every listed file ends up in the repository with the recorded content; unlisted
files do not change the exit code. After a push that failed partway, run it again: files already in
the repository show as `present`. To keep only what is still missing, [clean up](#6-clean-up) first.
A real push with no problem ends with the next steps:

```text
next: uc generate (or wait for the schedule), then uc procure clean
```

### Large files

Jenkins WARs are close to 100 MB, and push sends each file in a single request. Two limits between
push and the repository can cut such an upload off:

- **Artifact Keeper's request timeout.** Raise `GLOBAL_REQUEST_TIMEOUT_SECS` on the Artifact Keeper
  server (default 120 seconds), for example to `600`: Artifact Keeper aborts any request to its Maven
  path that takes longer, however much of the file has arrived. On a slow uplink a WAR then fails
  with `HTTP 503: Server overloaded, please retry: request timed out`, a broken pipe, a dropped
  connection or an HTTP 502 from a proxy in front. The same limit cuts core downloads that controllers
  make through the update center, which fetches from the same path.
- **A proxy's upload limit.** Cloudflare's proxy accepts at most 100 MB per request on its Free and
  Pro plans. When the repository's hostname goes through it, push with a hostname that bypasses the
  proxy for that run (one that reaches Artifact Keeper directly, with a certificate the uc container
  trusts): `docker compose run --rm -e REPO_URL=https://<direct-host>/ uc procure push`.

Push recovers the bytes a timed-out upload can leave stored but unrecorded in Artifact Keeper
(served on download, but invisible to its UI, its `.sha1`, the update center and its garbage
collection): when they are the staged file's, it uploads them again, which records them. When they
differ, it reports `DIFFERENT`, and only deleting them from Artifact Keeper's storage on its server
clears the path. Clean keeps the staged file for push.

## 5. Confirm

```bash
docker compose run --rm uc generate        # publish now, or wait for the next scheduled generation
docker compose run --rm uc doctor --fetch
```

The next generation publishes the new files; `uc generate` publishes at once. `uc doctor` should no
longer list what you pushed.

## 6. Clean up

Once files are in the repository, their staged copies can go:

```bash
docker compose run --rm uc procure clean --dry-run   # list what would be deleted, change nothing
docker compose run --rm uc procure clean
```

It checks every `.hpi`, `.jpi`, `.war`
and `.pom` in `./procurement/`, listed in `procurement.json` or placed by hand, against the
repository. It deletes only the files the repository holds with the same content (the same sha1),
with their `procurement.json` entries and any folder left empty, so what remains is what still needs
pushing. Like push, it reads the repository with `secrets/repo_push_auth`.

| Result | Meaning |
|---|---|
| `removed` | The repository holds the same file: the staged copy and its entry are deleted. With `already gone from the folder`, only the entry was left. |
| `would remove` | Dry run: it would be deleted. |
| `kept` | The repository does not hold it yet, or Artifact Keeper stored it without recording it ([Large files](#large-files)): push it (or, when it is missing from the folder too, stage or record it again). |
| `DIFFERENT` | The repository holds another file at that path, or the staged file changed since it was downloaded: kept for you to look into. |
| `ERROR` | It could not be checked (for example 401: check `secrets/repo_push_auth`) or deleted: kept. |

It exits 0 unless a file is `DIFFERENT` or an `ERROR`. `procurement.json`, unfinished downloads and
files outside the Maven layout are left alone.

## Limits

- Versions: a dependency is staged at the minimum plugins declare, not the newest. Security warnings
  still flag old versions you stock.
- Plugins never published on repo.jenkins-ci.org (internal ones) cannot be downloaded from it.
- Plugins Jenkins adds implicitly for plugins built against old cores ("detached" plugins) are not
  followed.
