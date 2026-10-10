#!/bin/sh
# Give secrets/ to the host user, and let each container read only what it needs, through its group:
# the uc image's services (scheduler, uc, repo-adapter) run as 10001:10001, web as 33:33. The host
# user keeps writing the directory and every file, so no step needs sudo, and credentials can be
# written before or after the keys. Idempotent; it also gives back a secrets/ that another user,
# such as root, owns.
#
# Runs as root in the setup service: uc-setup runs it after secrets, pki-init and pki-rotate, for
# the owner of ./config. Usage: secrets-permissions.sh DIR HOST_UID HOST_GID
set -eu

dir="$1"
owner="$2:$3"
uc_group=10001
web_group=33

set_perms() { # set_perms OWNER:GROUP MODE FILE... - files that do not exist are skipped
    ids="$1"
    mode="$2"
    shift 2
    for file in "$@"; do
        [ -e "${file}" ] || continue
        chown "${ids}" "${file}"
        chmod "${mode}" "${file}"
    done
}

# Every file first goes back to the host user, modes untouched (README.md, anything else). The
# files the containers read then get their group and mode below.
chown "${owner}" "${dir}"
find "${dir}" -maxdepth 1 -type f -exec chown "${owner}" {} +
set_perms "$2:${uc_group}" 0640 "${dir}/update-center.key" "${dir}/update-center.key.old" \
    "${dir}/repo_username" "${dir}/repo_password" "${dir}/github_username" "${dir}/github_token" \
    "${dir}/repo_push_auth"
set_perms "$2:${uc_group}" 0644 "${dir}/update-center.cert" "${dir}/update-center.cert.old" "${dir}/root-ca.crt"
set_perms "$2:${web_group}" 0640 "${dir}/download_proxy_auth"
set_perms "${owner}" 0644 "${dir}/repo-ca.crt" "${dir}/proxy-ca.crt"

# The root CA's key is the host user's alone. Skipped when root/ is not writable, as when
# `make pki-rotate` mounts the offline copy read-only.
if [ -d "${dir}/root" ] && [ -w "${dir}/root" ]; then
    chown -R "${owner}" "${dir}/root"
    chmod 0700 "${dir}/root"
    find "${dir}/root" -type f -exec chmod 0600 {} +
fi
