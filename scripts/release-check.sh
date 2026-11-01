#!/usr/bin/env bash
# Checks a release tag before anything is built. Run it in a full clone (tags and origin/main
# fetched); it reads the tag's files with `git show`, so it does not need the tag checked out.
# Usage: release-check.sh <tag> <dry_run: true|false>
# Prints key=value lines (version, sha, published) on stdout; refusals go to stderr, exit 1.
# The repository has no version file: the version is the first `## <version>` heading of
# CHANGELOG.md at the tag, and the tag must equal it.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tag=${1:?usage: release-check.sh <tag> <dry_run>}
dry_run=${2:-true}
fail() { echo "release-check: $*" >&2; exit 1; }
command -v gh > /dev/null || fail "the GitHub CLI (gh) is not on PATH"

case $tag in
  *+*) fail "'$tag' is a metadata tag: a '+' tag changes no code and has no release assets. Release the tag without the '+' part." ;;
esac
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "'$tag' is not a release tag (expected vMAJOR.MINOR.PATCH)"
version=${tag#v}

sha=$(git rev-parse --verify --quiet "refs/tags/$tag^{commit}") || fail "tag '$tag' does not exist"
main_ref=${RELEASE_MAIN_REF:-origin/main}
git merge-base --is-ancestor "$sha" "$main_ref" || fail "tag '$tag' ($sha) is not an ancestor of $main_ref"

tmp="${TMPDIR:-/tmp}/release-check-changelog.$$"
trap 'rm -f "$tmp"' EXIT
git show "$sha:CHANGELOG.md" > "$tmp"
top=$(awk '/^## / { sub(/^## /, ""); sub(/ .*$/, ""); print; exit }' "$tmp")
[ "$top" = "$version" ] || fail "tag '$tag' differs from the top version of CHANGELOG.md (${top:-none})"
"$here/release-notes.sh" "$tmp" "$version" > /dev/null \
  || fail "CHANGELOG.md has no '## $version' section at '$tag'"

published=false
if out=$(gh release view "$tag" --json isDraft --jq .isDraft 2>&1); then
  [ "$out" = "false" ] && published=true
elif ! grep -qi 'not found' <<< "$out"; then
  fail "cannot read the releases of this repository: $out"
fi
if [ "$published" = true ] && [ "$dry_run" != true ]; then
  fail "a published release for '$tag' exists; it is never replaced (run with dry_run to test)"
fi

echo "version=$version"
echo "sha=$sha"
echo "published=$published"
