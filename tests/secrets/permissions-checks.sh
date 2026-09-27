#!/usr/bin/env bash
# Permission checks for scripts/secrets-permissions.sh. Runs as root inside the uc image, on a
# Linux-native directory where ownership is real (Docker Desktop bind mounts ignore it), and acts as
# each party with setpriv: the host user (1000), the uc containers (10001), web (33) and anyone else
# (2000). Usage, from the Makefile: make test-secrets
set -uo pipefail
script=/scripts/secrets-permissions.sh
dir=/work/secrets
host=1000:1000
uc=10001:10001
web=33:33
other=2000:2000
failures=0

as() { # as UID:GID COMMAND - run COMMAND as that user, with no supplementary groups
    setpriv --reuid="${1%%:*}" --regid="${1##*:}" --clear-groups bash -c "$2" 2> /dev/null
}

allow() { # allow DESCRIPTION UID:GID COMMAND - COMMAND must succeed
    if as "$2" "$3"; then
        echo "ok: $1"
    else
        echo "FAIL: $1"
        failures=$((failures + 1))
    fi
}

deny() { # deny DESCRIPTION UID:GID COMMAND - COMMAND must fail
    if as "$2" "$3"; then
        echo "FAIL: $1"
        failures=$((failures + 1))
    else
        echo "ok: $1"
    fi
}

fresh() { # a secrets/ owned by the host user, with credentials the host user wrote and a new PKI
    rm -rf /work && mkdir -p "${dir}" && chown "${host}" /work "${dir}"
    as "${host}" "cd ${dir} && printf svc > repo_username && printf pw > repo_password && : > github_username && : > github_token && printf u:p > download_proxy_auth && printf readme > README.md && printf u:t > repo_push_auth"
    uc pki init --out "${dir}" > /dev/null # as root, as the pki-init recipe runs it
}

fix() {
    sh "${script}" "${dir}" 1000 1000
}

expect_usable() { # every party can do exactly what it needs, and no more
    local label="$1"
    allow "${label}: host user creates files in secrets/" "${host}" "touch ${dir}/new && rm ${dir}/new"
    allow "${label}: host user rewrites repo_password" "${host}" "printf pw2 > ${dir}/repo_password"
    allow "${label}: host user can move the root key" "${host}" "test -r ${dir}/root/root-ca.key && test -w ${dir}/root && test -w ${dir}"
    for f in update-center.key update-center.cert root-ca.crt repo_username repo_password github_username github_token repo_push_auth; do
        allow "${label}: uc containers read ${f}" "${uc}" "cat ${dir}/${f} > /dev/null"
    done
    deny "${label}: uc containers cannot read download_proxy_auth" "${uc}" "cat ${dir}/download_proxy_auth"
    deny "${label}: uc containers cannot read root-ca.key" "${uc}" "cat ${dir}/root/root-ca.key"
    allow "${label}: web reads download_proxy_auth" "${web}" "cat ${dir}/download_proxy_auth > /dev/null"
    for f in update-center.key repo_password repo_push_auth; do
        deny "${label}: web cannot read ${f}" "${web}" "cat ${dir}/${f}"
    done
    for f in update-center.key repo_password github_token download_proxy_auth root/root-ca.key repo_push_auth; do
        deny "${label}: another user cannot read ${f}" "${other}" "cat ${dir}/${f}"
    done
    allow "${label}: README.md left alone" "${host}" "test \"\$(stat -c %u:%g:%a ${dir}/README.md)\" = 1000:1000:644"
}

# 1. A fresh setup: credentials written by the host user, then pki-init.
fresh
fix
expect_usable "fresh"

# 2. Running it again changes nothing.
before="$(cd "${dir}" && stat -c '%n %u:%g %a' ./* ./root ./root/*)"
fix
after="$(cd "${dir}" && stat -c '%n %u:%g %a' ./* ./root ./root/*)"
if [[ "${before}" == "${after}" ]]; then echo "ok: idempotent"; else
    echo "FAIL: a second run changed permissions"
    failures=$((failures + 1))
fi

# 3. Repair: secrets/ as the old pki-init recipe left it, everything given to uid 10001.
fresh
chown -R 10001:10001 "${dir}"
fix
expect_usable "repaired"

# 4. Placeholders created by root inside a container, as the secrets-placeholders recipe does.
fresh
rm -f "${dir}/github_token" && : > "${dir}/github_token"
fix
allow "placeholder: host user writes a file root created" "${host}" "printf t > ${dir}/github_token"
allow "placeholder: uc containers read it" "${uc}" "cat ${dir}/github_token > /dev/null"

# 5. After a rotation: the new pair is usable, and the host user can delete the old one.
fresh
fix
uc pki rotate --dir "${dir}" > /dev/null
fix
allow "rotated: uc containers read the new key" "${uc}" "cat ${dir}/update-center.key > /dev/null"
deny "rotated: another user cannot read the old key" "${other}" "cat ${dir}/update-center.key.old"
allow "rotated: host user deletes the old pair" "${host}" "rm ${dir}/update-center.key.old ${dir}/update-center.cert.old"

# 6. The root key moved offline: nothing to do for root/, and no error.
fresh
rm -rf "${dir}/root"
if fix; then echo "ok: no root/ is fine"; else
    echo "FAIL: the script failed without root/"
    failures=$((failures + 1))
fi

exit "${failures}"
