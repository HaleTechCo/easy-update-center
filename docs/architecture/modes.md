# Modes

What online and air-gapped mode change, and what air-gapped mode keeps and drops.

`MODE` only chooses defaults; every setting it affects can be set explicitly. The defaults per
mode: [Mode defaults](../reference/configuration.md#mode-defaults).

## Online

- By default the generator reads plugins and cores from repo.jenkins-ci.org, the repository the
  public update center uses. Its listing query there needs an account: a free Jenkins community
  account from https://accounts.jenkins.io. Anonymous queries are refused with HTTP 403. It can read
  [your own repository](#your-own-repository) instead.
- By default controllers download binaries from get.jenkins.io.
- Upstream data comes from the internet at every run: security warnings and suspensions from
  update-center2 on GitHub, popularity, and tool installers from updates.jenkins.io.
- A GitHub token is recommended: without one, labels from GitHub topics are missing. See
  [GitHub token](../reference/secrets.md#github-token).
- The repo-adapter is off by default: repo.jenkins-ci.org is complete, and routing its full listing
  through the adapter would gain nothing.

### Your own repository

Online mode can read your own Artifactory or Artifact Keeper instead of repo.jenkins-ci.org, and
still fetch the upstream data live. In `.env`:

```bash
REPO_TYPE=artifact-keeper            # Artifact Keeper only; artifactory is the default
REPO_URL=https://artifacts.corp.example/
REPO_KEY=jenkins-plugins-local
DOWNLOAD_BACKEND=artifactory         # controllers download from your repository, not get.jenkins.io
DOWNLOAD_BACKEND_URL=https://artifacts.corp.example/maven/jenkins-plugins-local/   # that repository by Maven path
DOWNLOAD_ACCESS=proxy                # only if controllers cannot read it without credentials
CORE_AGE_DAYS=0                      # a tier for every stored core, as air-gapped
```

- `secrets/repo_username` and `secrets/repo_password` hold that repository's account, as in
  air-gapped mode ([Secrets](../reference/secrets.md#secret-files)).
- For a procured repository, set `REPO_ADAPTER=on` so missing POMs, descriptions and release dates
  are filled in from the archives; it is always on for Artifact Keeper. See
  [repo-adapter](repo-adapter.md).
- With `DOWNLOAD_ACCESS=proxy`: [Proxy plugin downloads](../how-to/proxy-downloads.md).

## Air-gapped

- The generator reads your Artifactory or Artifact Keeper repository through the
  [repo-adapter](repo-adapter.md), which fills in the POMs, descriptions and release dates a
  procured repository tends to lack.
- Controllers download binaries from the same repository, directly or through the web container.
- Nothing is fetched from the internet at run time. Upstream data comes from the image, or from
  copies you procure: [Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md).
- Whatever the air gap makes unreachable is dropped with a warning, never a failure.

## What air-gapped mode keeps and drops

| Output or source | Air-gapped behavior |
|---|---|
| Plugin and core catalog, tiers, version routing, every JSON file, signatures | Kept, generated from your repository. |
| Security warnings, suspensions | Kept, from the image's bundled copy or a procured one; `status.json` shows their age. |
| Your own warnings and blocklist | Kept: the overlay works the same in both modes. |
| Popularity | 0 for every plugin, unless `POPULARITY_JSON_URL` points at a procured copy. |
| Labels from GitHub topics, repository checks | Dropped with a warning; labels from upstream's `label-definitions.properties` still apply. |
| Issue trackers, maintainers, health scores | Dropped with a warning. |
| `updates/` tool installers | Dropped unless `TOOL_INSTALLERS_URL` points at a procured copy; their own download links still point at the internet. |
| Links to plugins.jenkins.io and the Jenkins wiki | Present in the metadata, unreachable from the air gap (cosmetic). |

## Fast failure for blocked hosts

`compose.airgapped.yaml` maps `api.github.com`, `github.com`, `codeload.github.com`,
`raw.githubusercontent.com`, `reports.jenkins.io` and `updates.jenkins.io` to 127.0.0.1 in the
`scheduler`, `uc` and `repo-adapter` containers. A connection to any of them is refused at once, and
the generator's own handling turns that into a warning. Without this, a firewall that silently
drops packets would stall each of those clients for the operating system's connect timeout (about
two minutes each), because the generator sets no timeouts of its own. Every other name, your
repository included, still resolves normally.

## Core age

Tiers come from the core versions plugin releases declare as their minimum, when the repository holds
that core's `.war`: an LTS version `2.x.y` becomes an LTS tier and also brings the weekly tier
`2.(x+1)`, whose `.war` is not needed; a weekly version becomes a weekly tier and also brings the next
LTS release the repository holds. A required core whose `.war` is missing brings no tier: `uc doctor`
lists those, and `uc procure stage --war required` stages them
([Procure missing files](../how-to/procure-missing-files.md)). `CORE_AGE_DAYS` limits which cores count:

- online, `400`: like updates.jenkins.io. Every such core released in the last 400 days gets a tier,
  plus one older core per line (weekly, LTS) as a floor for older controllers. Without the limit, the
  public repository's full history would produce a tier for every baseline ever declared.
- air-gapped, `0` (no limit): every stored core a plugin requires gets a tier, however old. You
  chose which cores to store, air-gapped installations often trail the current LTS line by more than
  400 days, and the number of tiers cannot exceed the number of cores stored. Set `400` to match
  updates.jenkins.io exactly.

The limit comes from a small patch to update-center2
([the patches](../development/upgrading-upstream.md#the-patches)); upstream hard-codes 400 days.
