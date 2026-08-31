#!/bin/sh
set -eu

test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM
mkdir "$test_dir/bin"

cat >"$test_dir/bin/docker" <<'EOF'
#!/bin/sh
set -eu

if [ "$1 $2" = "image ls" ]; then
  case $4 in
    reference=ghcr.io/sunxu/media-bundle:*) echo 'ghcr.io/sunxu/media-bundle:direct' ;;
    reference=gh-proxy.org/docker/ghcr.io/sunxu/media-bundle:*) echo 'gh-proxy.org/docker/ghcr.io/sunxu/media-bundle:proxy' ;;
    *) exit 1 ;;
  esac
elif [ "$1 $2 $3" = "image rm --" ]; then
  printf '%s\n' "$4" >>"$DELETE_LOG"
else
  exit 1
fi
EOF
chmod +x "$test_dir/bin/docker"

printf 'no\n' | PATH="$test_dir/bin:$PATH" DELETE_LOG="$test_dir/deleted" ./bin/media-clean >/dev/null
[ ! -e "$test_dir/deleted" ]

printf 'yes\n' | PATH="$test_dir/bin:$PATH" DELETE_LOG="$test_dir/deleted" ./bin/media-clean >/dev/null
[ "$(cat "$test_dir/deleted")" = "$(printf '%s\n%s' \
  'gh-proxy.org/docker/ghcr.io/sunxu/media-bundle:proxy' \
  'ghcr.io/sunxu/media-bundle:direct')" ]
