#!/bin/sh
set -eu

test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM
mkdir "$test_dir/bin"

cat >"$test_dir/bin/gh" <<'EOF'
#!/bin/sh
set -eu

case " $* " in
  *' --method DELETE '*)
    for argument do endpoint=$argument; done
    printf '%s\n' "$endpoint" >>"$DELETE_LOG"
    ;;
  *)
    printf '101\tfirst\t2026-08-31T00:00:00Z\n102\tsecond,latest\t2026-08-31T01:00:00Z\n'
    ;;
esac
EOF
chmod +x "$test_dir/bin/gh"

printf 'no\n' | PATH="$test_dir/bin:$PATH" DELETE_LOG="$test_dir/deleted" ./bin/media-clean-ghcr >/dev/null
[ ! -e "$test_dir/deleted" ]

printf 'yes\n' | PATH="$test_dir/bin:$PATH" DELETE_LOG="$test_dir/deleted" ./bin/media-clean-ghcr >/dev/null
[ "$(cat "$test_dir/deleted")" = "$(printf '%s\n%s' \
  '/users/sunxu/packages/container/media-bundle/versions/101' \
  '/users/sunxu/packages/container/media-bundle/versions/102')" ]
