# Releasing

Maintainer guide: cutting a release of Easy Update Center for Jenkins with the Release workflow
(`.github/workflows/release.yml`), and recovering when a run fails. The repository must be set up
first: [GitHub setup](github-setup.md).

## Versions

Versions are SemVer, tagged `vX.Y.Z`. The first release is `0.1.0`. Until 1.0, a minor release may
change the interface (settings, commands, Compose files), and its CHANGELOG entry says how to move.

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
   heading and the two image versions, nothing else (for a candidate, only the two image versions).
3. Tag the commit the merge put on `main`, and push the tag. Only admins can push release tags
   ([GitHub setup](github-setup.md), step 5):

   ```bash
   git switch main && git pull
   git tag -s vX.Y.Z -m "Release X.Y.Z"     # -s signs it; -a if you have no signing key
   git push origin vX.Y.Z
   ```

The tag starts the Release workflow. Its jobs run in this order; each starts only if the ones before
it succeeded.

| Job | What it does | What it leaves behind |
|---|---|---|
| `check` | Refuses a tag on a commit not on `main` and, through `tools/release.py check`, a version not above every other tag, a release without its CHANGELOG section, a candidate without Unreleased entries, and a `compose.yaml` naming other images. Works out `prerelease`, `latest` and `minor_latest`. | Nothing. |
| `images` | Builds the `uc` and `web` images per architecture on a native runner, without cache, and pushes each by digest. | Untagged per-architecture images. |
| `index` | Joins the architectures into one index per image, from exactly one digest per architecture. | Each index, tagged `build-<commit>` only. |
| `ci` | Runs the CI workflow against those exact images. | Nothing. |
| `sign` | Signs the indexes with cosign and attests their provenance and SBOMs. | Signatures and attestations. |
| `bundle` | Builds the air-gapped bundle per architecture from the signed digests. | Workflow artifacts, kept 7 days. |
| `draft` | Writes and signs `SHA256SUMS`, and creates or updates the draft Release with the bundles, SBOMs and checksums (a pre-release for a candidate). | A draft only maintainers see. |
| `publish` | Checks that the tag has one Release, the draft, holding exactly the two bundles, the two SBOMs, `SHA256SUMS` and its signature. Tags the images `X.Y.Z`, then `X.Y` and `latest` when this is the highest release, each tag for both images before the next, and checks each tag names the signed digest. Publishes the Release. | The release. |

How operators check what this produces: [Verify a release](../how-to/verify-a-release.md). How they
move to it: [Upgrade](../how-to/upgrade.md).

## When a run fails

- **`check` refused the tag:** nothing was published. Delete the tag
  (`git push --delete origin vX.Y.Z`, then `git tag -d vX.Y.Z`), fix the cause, and tag again:
  usually the tag was on the wrong commit, or the release pull request was not merged first.
- **A later job failed for a transient reason** (a network error, a registry hiccup): open the run
  and choose **Re-run failed jobs** within 7 days. It keeps the jobs that succeeded and their
  outputs.
- **A later job failed because of the workflow or the code:** nothing anyone can pull or download
  exists yet. Delete the draft Release and the tag, fix it on `main`, and tag the next rc or patch
  version.
- **A published release is never changed** (Immutable Releases): fix it with the next patch
  version.

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

While that issue is open, merge Dependabot's pull request if one is waiting, then cut a patch
release with a CHANGELOG entry under `### Security` naming the vulnerabilities. A fix in a package
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
      finds no attestation, switch `release.yml`'s attestations to `cosign attest` (the provenance
      predicate from `actions/attest-build-provenance/predicate`, the SBOM with `--type spdxjson`)
      and cut the next rc.
- [ ] Download the rc bundle, `docker load -i images.tar`, and in a checkout of the rc tag run:

      ```bash
      make it-airgapped PREBUILT=1 \
        UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:0.1.0-rc.N \
        WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:0.1.0-rc.N \
        UC_TEST_IMAGE=easy-update-center/uc-test:0.1.0-rc.N
      ```

      `PREBUILT=1` keeps the loaded images. The test image, named for the rc so that it does not
      exist yet, is built on the rc's uc image, as the Release workflow's `ci` job builds it, so
      every container runs the rc's images (with the checkout's `uc` code mounted, the rc tag's).
- [ ] Deploy the rc end to end ([Deploy online](../getting-started/online.md) or
      [Deploy air-gapped](../getting-started/airgapped.md)). `uc status` shows a `live_release` and
      `uc_version` `0.1.0-rc.1`.
- [ ] The pre-release's notes match the Unreleased section, and their links open.
- [ ] If anything fails, fix it on `main` and cut `0.1.0-rc.2`; repeat until an rc passes.
- [ ] Cut `0.1.0` (`prepare 0.1.0` moves the CHANGELOG), then make the packages public
      ([GitHub setup](github-setup.md), step 8).
