#!/bin/sh
# End-to-end check of the installed package's command line, as on a server: no desktop, no Qt.
# Uses a throwaway home folder with one session in it and a stand-in for `claude`.
set -eu

if python3 -c "import PySide6" 2>/dev/null; then
    echo "PySide6 is installed; this check is for a machine without it" >&2
    exit 1
fi

export HOME="$(mktemp -d)"
unset DISPLAY WAYLAND_DISPLAY
cd "$HOME"

work="$HOME/work/proj"
id=0b5e1c1e-1111-4222-8333-944455556666
project="$HOME/.claude/projects/$(printf %s "$work" | sed 's/[^A-Za-z0-9]/-/g')"
mkdir -p "$work" "$project" "$HOME/bin"
printf '%s\n' "{\"parentUuid\":null,\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"hello from the server\"},\"cwd\":\"$work\",\"sessionId\":\"$id\",\"entrypoint\":\"cli\"}" \
    > "$project/$id.jsonl"

cat > "$HOME/bin/claude" <<'EOF'
#!/bin/sh
echo "claude $* in $(pwd)"
EOF
chmod +x "$HOME/bin/claude"
export PATH="$HOME/bin:$PATH"

step() { echo; echo "\$ $*"; "$@"; }

step ccorg --version
step ccorg list | tee list.txt
grep -q "hello from the server" list.txt
step claude-code-organizer list >/dev/null           # same command line, under the long name
step ccorg group Server 1
ccorg groups | grep -q "Server  (1)"
step ccorg rename 1 Renamed on server
ccorg show 1 | tee show.txt
grep -q "Renamed on server" show.txt
grep -q "Server" show.txt

echo; echo "\$ ccorg resume 1"
result="$(ccorg resume 1)"
echo "$result"
test "$result" = "claude --resume $id in $work"

step ccorg archive 1
ccorg list --archived | grep -q Archived
step ccorg restore 1
step ccorg delete --yes 1
test -e "$HOME/.local/share/Trash/files/$id.jsonl"
test -e "$HOME/.local/share/Trash/info/$id.jsonl.trashinfo"
test -z "$(ccorg groups 2>/dev/null)"                   # the deleted session left its group

echo; echo "\$ claude-code-organizer </dev/null    # no display, no Qt: lists and exits"
claude-code-organizer </dev/null

echo; echo "CLI check passed"
