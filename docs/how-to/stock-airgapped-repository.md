# Stock an air-gapped repository

What to put in your Artifactory or Artifact Keeper repository, and where, so the update center offers it.

## What the repository must contain

- One Artifactory repository (local or virtual) or one Artifact Keeper Maven repository, named by
  `REPO_KEY`.
- Each plugin's `.hpi` (or `.jpi`) in Maven layout, under the group its own manifest names.
- Each Jenkins core `.war` you want to offer, in Maven layout.
- POMs are optional: the [repo-adapter](../architecture/repo-adapter.md) uses the one inside each
  plugin. Only plugins built without Maven need their real `.pom`.
- An account that can read the repository.

The update center offers exactly what the repository holds, including every version of every
plugin, filtered by the core each tier targets. To hide something without deleting it, use the
[overlay](block-plugins-and-add-warnings.md).

## Plugins

Store each plugin at:

```text
<group path>/<artifactId>/<version>/<artifactId>-<version>.hpi
```

The group is the plugin's own `Group-Id`, with dots as directories. Plugins do not share one group:
`caffeine-api` is `io.jenkins.plugins`, `git` is `org.jenkins-ci.plugins`. For example:

```text
io/jenkins/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.hpi
org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi
```

A file downloaded from updates.jenkins.io or get.jenkins.io is named `<id>.hpi`; rename it to
`<artifactId>-<version>.hpi`. A plugin stored under another group than its manifest names is
**misplaced**: it is left out, and `uc doctor` names the path to move it to.

Plugins need their dependencies too. A plugin whose required plugins (or required versions) are
missing is still offered, but Jenkins cannot install it; `uc doctor` lists those.

## Read a plugin's coordinates

A plugin is a zip file. Its coordinates are in `META-INF/MANIFEST.MF`:

```bash
unzip -p caffeine-api.hpi META-INF/MANIFEST.MF | grep -E '^(Group-Id|Artifact-Id|Plugin-Version):'
```

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead("$PWD\caffeine-api.hpi")
(New-Object IO.StreamReader($zip.GetEntry('META-INF/MANIFEST.MF').Open())).ReadToEnd() -split "`r?`n" |
  Select-String '^(Group-Id|Artifact-Id|Plugin-Version):'
$zip.Dispose()
```

## Jenkins core

Store each core version you want to offer at:

```text
org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war
```

Its POM is optional. Which stored cores get a tier: [Core age](../architecture/modes.md#core-age).
`uc doctor` lists the required cores the repository lacks, and `uc procure stage --war required`
stages them ([Procure missing files](procure-missing-files.md)). Without any core the site offers
plugins only, with no tiers and no core upgrade.

## POMs

With the repo-adapter on (the air-gapped default), a plugin needs no `.pom`: the adapter uses the one
built into the `.hpi`, and ignores a stored `.pom` that is not a real POM for the same coordinates
([Filling gaps](../architecture/repo-adapter.md#filling-gaps)). Only plugins built with Gradle need
their real `.pom`, stored beside the `.hpi`: `uc doctor` lists them with its URL. Parent POMs and
`.jar` files are optional.

## The service account

What the account needs, for each repository type: [Secrets](../reference/secrets.md#secret-files). A
repository behind a private CA also needs its CA bundle:
[Repository behind a private CA](../getting-started/airgapped.md#repository-behind-a-private-ca).

## Check with uc doctor

Before the first generation, and after each procurement:

```bash
docker compose run --rm uc doctor --fetch
```

`--fetch` reads each archive's manifest and POM, so dependencies can be checked before the first
generation. Read the report top down:

| Section | What to do |
|---|---|
| Jenkins cores with a POM: none | Store at least one `jenkins-war`, unless a plugins-only site is what you want. |
| no POM in the repository or the archive | Procure the real `.pom` from the URL shown (`uc procure stage --poms` stages it) and store it beside the `.hpi`. |
| misplaced | Move the file to the path shown. |
| skipped | Usually larger than `REPO_MAX_ARCHIVE_MB`; raise it or remove the file. |
| not in Maven layout | Rename or move the file into Maven layout. |
| Dependency problems | Procure the missing plugins or versions (`uc procure stage --dependencies` stages them). |
| Cores plugins require that the repository lacks | Store them for their tiers (`uc procure stage --war required` stages them). |
| older required cores below the oldest core in the repository | Only for controllers that old: the line names the `--since` that stages them. Once stored, the oldest becomes the floor for every later `uc procure stage --war required`. |
| POMs filled in from archives, `.pom` files ignored | Nothing required: the embedded POM is used. To store the real `.pom` instead, procure it from the URL shown and store it beside the archive, replacing an ignored one. |
| dates from the repository | Information only. |

Every section: [uc doctor](../reference/cli.md#uc-doctor).
