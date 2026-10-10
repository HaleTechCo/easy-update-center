Easy Update Center for Jenkins @VERSION@: air-gapped bundle for linux/@ARCH@

  images.tar                            the uc and web images, for docker load
  easy-update-center-@VERSION@.tar.gz   the source: Compose files, setting examples, docs

1. Verify the bundle before carrying it across, on a machine with cosign and internet access
   (docs/how-to/verify-a-release.md), with SHA256SUMS and SHA256SUMS.sigstore.json from the
   same Release:

     cosign verify-blob --bundle SHA256SUMS.sigstore.json \
       --certificate-identity https://github.com/HaleTechCo/easy-update-center/.github/workflows/release.yml@refs/heads/main \
       --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
     sha256sum --check --ignore-missing SHA256SUMS

2. On the Docker host, load the images and unpack the source:

     docker load -i images.tar
     tar -xzf easy-update-center-@VERSION@.tar.gz
     cd easy-update-center-@VERSION@
     cp .env.airgapped.example .env

3. In .env, set PUBLIC_URL, REPO_URL and REPO_KEY, uncomment COMPOSE_FILE, and set:

     UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:@VERSION@
     WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:@VERSION@

4. Run docker compose run --rm setup, then continue with docs/getting-started/airgapped.md
   from "Add credentials".
