# Releasing

Maintainer guide: cutting a release of Easy Update Center for Jenkins with the Release workflow
(`.github/workflows/release.yml`), and recovering when a run fails. The repository must be set up
first: [GitHub setup](github-setup.md).

## Versions

Versions are SemVer, tagged `vX.Y.Z`. The first release is `0.1.0`. Until 1.0, a minor release may
change the interface (settings, commands, Compose files), and its CHANGELOG entry says how to move.

A version must be above every existing version tag, release candidates included, so there are no
patch releases of an older minor: a fix always goes out as the next version. Every release moves
the `X.Y` and `latest` image tags and is the GitHub Release marked latest.

A release candidate, `X.Y.Z-rc.N`, rehearses `X.Y.Z`: it is published as a GitHub pre-release, its
images are tagged `X.Y.Z-rc.N` only, and it never moves `X.Y` or `latest`.

## Cut a release

1. On an up-to-date `main` with CI green, make the release commit on a branch:

   ```bash
   git switch -c release-X.Y.Z
   git fetch --tags
   python3 tools/release.py prepare X.Y.Z   # or X.Y.Z-rc.N; no v
   git commit -am "Release X.Y.Z"
   git push -u origin release-X.Y.Z
   ```

   For a release, `prepare` moves CHANGELOG's Unreleased entries under `## [X.Y.Z] - <today>` and
   sets `compose.yaml`'s images to `X.Y.Z`; it refuses a version CHANGELOG already has. For a
   candidate, it only sets the images to `X.Y.Z-rc.N` and refuses an empty Unreleased section; the
   CHANGELOG stays as it is. Either way it refuses a malformed version, and one that is not above
   the highest version tag, release candidates included: `0.1.0` may follow `0.1.0-rc.2`, but
   `0.1.0-rc.2` may not follow `0.1.0-rc.3`.
2. Open the pull request "Release X.Y.Z", let CI pass, and merge it. Check its diff: the CHANGELOG
   heading and the four image lines in `compose.yaml`, nothing else (for a candidate, only the four
   image lines).
3. Tag the commit the merge put on `main`, and push the tag. Only admins can push release tags
   ([GitHub setup](github-setup.md), step 5):

   ```bash
   git switch main && git pull
   git log --oneline -10                     # find the "Release X.Y.Z" commit: copy its SHA
   git tag -s vX.Y.Z -m "Release X.Y.Z" <sha>   # -s signs it; -a if you have no signing key
   git push origin vX.Y.Z
   ```

   Name the SHA, and take it from the release pull request's commit, not from `git log -1`: after
   the pull, `main` may already hold another pull request's merge, which an unnamed tag would label
   as the release. The commit's subject depends on how the pull request was merged (the repository
   allows rebase and squash merges only), so if the log is unclear, copy the commit SHA that the
   pull request page shows after the merge.

The tag starts the Release workflow. Its jobs run in this order; each starts only if the ones before
it succeeded.

| Job | What it does | What it leaves behind |
|---|---|---|
| `check` | Refuses a tag on a commit not on `main` and, through `tools/release.py check`, a version not above every other tag, a release without its CHANGELOG section, a candidate without Unreleased entries, and a `compose.yaml` naming other images. Works out `prerelease`. | Nothing. |
| `images` | Builds the `uc` and `web` images per architecture on a native runner, without cache, and pushes each by digest. | Untagged per-architecture images. |
| `index` | Joins the architectures into one index per image, from exactly one digest per architecture. | Each index, tagged `build-<commit>` only. |
| `ci` | Runs the CI workflow against those exact images, the linux/amd64 ones only: the arm64 images are built and signed but not tested. | Nothing. |
| `sign` | Signs the indexes with cosign and attests their provenance and SBOMs (an SBOM lists the linux/amd64 image's contents, attested on the multi-architecture digest). | Signatures and attestations. |
| `bundle` | Builds the air-gapped bundle per architecture from the signed digests. | Workflow artifacts, kept 7 days. |
| `draft` | Writes and signs `SHA256SUMS`, and creates or updates the draft Release with the bundles, SBOMs and checksums (a pre-release for a candidate). | A draft only maintainers see. |
| `publish` | Checks that the tag has one Release, still a draft, holding exactly the two bundles, the two SBOMs, `SHA256SUMS` and its signature. Tags the images `X.Y.Z`, then `X.Y` and `latest` for a release, each tag for both images before the next, and checks each tag names the signed digest. Publishes the Release. | The release. |

How operators check what this produces: [Verify a release](../how-to/verify-a-release.md). How they
move to it: [Upgrade](../how-to/upgrade.md).

## When a run fails

- **`check` refused the tag:** nothing was published. Delete the tag
  (`git push --delete origin vX.Y.Z`, then `git tag -d vX.Y.Z`), fix the cause, and tag again:
  usually the tag was on the wrong commit, or the release pull request was not merged first.
- **A later job failed for a transient reason** (a network error, a registry hiccup): open the run
  and choose **Re-run failed jobs** within 7 days. It keeps the jobs that succeeded and their
  outputs.
- **A later job failed because of the workflow or the code, before `publish`'s "Tag the images"
  step:** no version, `X.Y` or `latest` image tag exists yet (the `build-<commit>` indexes do), and
  no Release is published. Delete the draft Release and the tag, fix it on `main`, and tag the next
  rc or the next version. If the abandoned version was a release, its CHANGELOG section stays:
  add a line to Unreleased saying `X.Y.Z` was never published, so that it goes under the next
  version.
- **`publish` failed inside "Tag the images":** a transient error (network, registry): re-run
  `publish` with **Re-run failed jobs** while no newer version tag exists (an older release's
  re-run would move `X.Y` and `latest` back); the step is idempotent. A digest mismatch (the log
  says a tag "is X, not the signed Y"): the run stops at the first tag that names another digest,
  and the tags before it are good. Delete the draft Release, the git tag, and the mismatched
  image's package version in GHCR (the package's settings page). If the bad tag was `X.Y` or
  `latest`, point it back with
  `docker buildx imagetools create --tag <repo>:<tag> <repo>:<previous version>`. Then find the
  cause and cut the next version.
- **`publish` failed after "Tag the images" (when publishing the Release):** the image tags
  already exist and name the signed digests, so re-run `publish` with **Re-run failed jobs** while
  no newer version tag exists, otherwise cut the next version; do not delete the tag. If it says
  the release is "already published", the edit may have succeeded without the run seeing the
  answer: if this run published it, the release is done; otherwise cut the next version.
- **A published release is never changed** (Immutable Releases): fix it with the next version.

## Security fixes in the base images

A release's images keep the base images and packages they were built with, so a fix reaches
operators only in a new release. Two things say when one is due:

- Dependabot's weekly `images` pull request moves the base images to their tags' newest digests
  (they are pinned: [Pinning policy](../reference/dependencies.md#pinning-policy)). Merge it when CI
  passes.
- The Scheduled workflow's `image-scan` job scans the latest release's images every week with
  Grype. When it finds a High or Critical vulnerability that has a fix, it opens the issue
  "Fixable vulnerabilities in the published images", listing them, or updates it; a later scan
  that finds none closes it. Before the first release it skips with a notice.

While that issue is open, merge Dependabot's pull request if one is waiting, then cut the next
version with a CHANGELOG entry under `### Security` naming the vulnerabilities. A fix in a package
the base image ships arrives with the base image's next build, so if the rebuilt images still have
it, wait for Dependabot's next pull request.

## First-release rehearsal (0.1.0)

Only GitHub can show that the workflows work, so the first release is a rehearsal, run on release
candidates:

- [ ] Open a pull request and see `ci` pass; merge it.
- [ ] Cut `0.1.0-rc.1` as above: `prepare 0.1.0-rc.1`, its pull request, the merge, the tag.
- [ ] Pulls of both images work for `--platform linux/amd64` and `--platform linux/arm64`. Until the
      packages are public, sign in first with `docker login ghcr.io`.
- [ ] `docker run --rm ghcr.io/haletechco/easy-update-center-uc:0.1.0-rc.1 --version` prints
      `uc 0.1.0-rc.1`.
- [ ] The commands of [Verify a release](../how-to/verify-a-release.md) pass for the rc tag. They
      use `cosign verify-attestation` on the attestations that `actions/attest` pushed to the
      registry; that cosign reads them is expected (the same bundle format) but unproven. If it
      finds no attestation, first check `cosign version` (3 or later) and whether
      `cosign verify-attestation` needs `--new-bundle-format`; if it does, add the flag to the
      commands in verify-a-release.md. If it still finds none, switch `release.yml`'s attestations
      to `cosign attest`: `cosign attest --type slsaprovenance1 --predicate <provenance predicate>`
      (the predicate from `actions/attest-build-provenance/predicate`) and
      `cosign attest --type https://spdx.dev/Document/v2.3 --predicate <sbom>` (`--type spdxjson`
      would record `https://spdx.dev/Document`, which the verify commands do not match), then cut
      the next rc.
- [ ] Download the rc bundle, unpack it
      (`tar -xf easy-update-center-0.1.0-rc.1-airgapped-amd64.tar`), load its images
      (`docker load -i images.tar`, inside the unpacked folder), and in a checkout of the rc tag
      run:

      ```bash
      make it-airgapped PREBUILT=1 \
        UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:0.1.0-rc.1 \
        WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:0.1.0-rc.1 \
        UC_TEST_IMAGE=easy-update-center/uc-test:0.1.0-rc.1
      ```

      `PREBUILT=1` keeps the loaded images. The test image, named for the rc so that it does not
      exist yet, is built on the rc's uc image, as the Release workflow's `ci` job builds it, so
      every container runs the rc's images (with the checkout's `uc` code mounted, the rc tag's).

      Then check each bundle's architecture: load its `images.tar` (the arm64 bundle last, since
      it overwrites the same tags) and run
      `docker image inspect --format '{{.Os}}/{{.Architecture}}' ghcr.io/haletechco/easy-update-center-uc:0.1.0-rc.1`
      (and the same for the web image). It prints `linux/amd64` for the amd64 bundle and
      `linux/arm64` for the arm64 one.
- [ ] Deploy the rc end to end ([Deploy online](../getting-started/online.md) or
      [Deploy air-gapped](../getting-started/airgapped.md)). `uc status` shows a `live_release` and
      `uc_version` `0.1.0-rc.1`.
- [ ] The pre-release's notes match the Unreleased section, and their links open.
- [ ] Watch `docker save` on both paths, which may need `--platform` when its images came from
      multi-architecture indexes: in `ci.yml`'s `images` job (to which the Release passes images
      pulled from the indexes) and in `release.yml`'s `bundle` job. If either fails or saves the wrong
      architecture, add `--platform` there.
- [ ] If anything fails, fix it on `main` and cut `0.1.0-rc.2`; repeat until an rc passes.
- [ ] Cut `0.1.0` (`prepare 0.1.0` moves the CHANGELOG), then make the packages public
      ([GitHub setup](github-setup.md), step 8).
