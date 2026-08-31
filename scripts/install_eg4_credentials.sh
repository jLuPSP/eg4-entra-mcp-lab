#!/usr/bin/env bash
set -euo pipefail

secret_dir=${1:-./secrets}
runtime_uid=${EG4_RUNTIME_UID:-10001}
runtime_gid=${EG4_RUNTIME_GID:-10001}
username_path=$secret_dir/eg4_username
password_path=$secret_dir/eg4_password

umask 077
mkdir -p "$secret_dir"

read -r -p 'EG4 Monitor username: ' username </dev/tty
read -r -s -p 'EG4 Monitor password: ' password </dev/tty
printf '\n' >/dev/tty

if [[ -z "$username" || -z "$password" ]]; then
    printf 'Username and password must both be nonempty.\n' >&2
    exit 1
fi

username_tmp=$(mktemp "$secret_dir/eg4_username.tmp.XXXXXX")
password_tmp=$(mktemp "$secret_dir/eg4_password.tmp.XXXXXX")
cleanup() {
    rm -f -- "$username_tmp" "$password_tmp"
}
trap cleanup EXIT

printf '%s' "$username" >"$username_tmp"
printf '%s' "$password" >"$password_tmp"
chown "$runtime_uid:$runtime_gid" "$username_tmp" "$password_tmp"
chmod 600 "$username_tmp" "$password_tmp"
mv -f -- "$username_tmp" "$username_path"
mv -f -- "$password_tmp" "$password_path"
trap - EXIT
unset username password

printf 'EG4 credentials installed with mode 600 for the Energy API user.\n'
