# Base images

Which images uc and web are built on, and why the uc image stays on `eclipse-temurin` (Ubuntu)
rather than Red Hat's Universal Base Image (UBI). For contributors, and for anyone evaluating the
project for an environment that prefers Red Hat images.

## What each image builds on

- **uc:** `eclipse-temurin:21-jre`, which is Ubuntu with Eclipse Temurin's Java runtime. The build
  adds Python, OpenSSL and tini from Ubuntu's archive.
- **web:** `httpd:2.4`, the official Apache image, which is Debian.
- **The build stage only:** `maven:3.9-eclipse-temurin-21` builds update-center2. Nothing from it
  ships except the jars it produces, so its choice bears on the build's integrity, not on what
  runs: an official image, pinned by digest, serves.

Both runtime images are fully upgraded, stripped to what they run and squashed to one layer at
build time ([Hardening](hardening.md#images)).

## The UBI 9 trial

On 2026-10-18, the uc image of 0.1.1 was rebuilt on `registry.access.redhat.com/ubi9/ubi-minimal`
(RHEL 9.8). It used the same build stage and the same rules: full upgrade, removals, one layer.
UBI needed four changes:

- **Python 3.12**, the newest RHEL 9 offers. The code targets 3.13 but runs on 3.12.
- **tini** as the static release binary, pinned by checksum, as it is not in UBI's repositories.
- **Red Hat's OpenJDK 21** in place of Temurin.
- **curl stays**, because `rpm` requires it.

| | Ubuntu (0.1.1, as released) | UBI 9 (trial) |
|---|---|---|
| Size | 492 MB | 655 MB |
| Packages | 107 | 139 |
| Java | Temurin 21.0.12.1, self-contained, 159 MB | Red Hat OpenJDK 21.0.12.1, 212 MB |
| Python | 3.14.4 | 3.12.15, plus RHEL's own 3.9 |
| OpenSSL | 3.5.5 | 3.5.8 |
| curl | removed | stays |
| trivy findings | 0 Critical, 0 High, 42 Medium, 4 Low | 0 Critical, 21 High, 244 Medium, 136 Low |
| High or Critical with a fix | 0 | 0 |
| Air-gapped integration test | passes, both repository types | passes, both repository types |
| Unit tests | all pass | 1411 of 1412: the one failure needs `tar`, which UBI minimal lacks |
| Setup checks | pass | did not run: they need `setpriv`, from util-linux |

Where the differences come from:

- **A second Python.** Red Hat's Java requires `nss`, which requires the crypto-policy scripts,
  which require RHEL's system Python 3.9. That is a second Python runtime to patch.
- **The base's own extras.** UBI minimal ships fonts (through its English language pack) and the
  `rpm` tooling, curl among it.
- **The High findings.** The 21 sat in the two Pythons (10), util-linux's libraries (8), expat (2)
  and gnutls (1). Red Hat listed each as "affected" with no fix released, or as "end of life".

The counts are a snapshot of one day: scanner databases change daily. The comparison between
the two bases is what holds.

## Conclusion

The uc image stays on `eclipse-temurin`. It is smaller, has fewer packages and fewer findings,
and runs a newer Python, and UBI added no function. Neither base had a High or Critical
vulnerability with a fix the image lacked, so UBI's more precise vulnerability data brought no
advantage here.

UBI is worth its cost where Red Hat images are required: FIPS-validated cryptography, programs
such as FedRAMP or DoD Iron Bank, or a Red Hat support contract. The trial shows it works. A UBI
build would need the four changes above, the code kept compatible with Python 3.12 (ruff's
`target-version = "py312"`), and util-linux and `tar` in the image for the setup checks and the
test stage.
