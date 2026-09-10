#!/usr/bin/env bash
# Build the package from this working tree instead of from the released tag.
#
# The committed PKGBUILD fetches "#tag=v${pkgver}" from GitHub, so a plain
# `makepkg` builds whatever that tag holds, not what you have locally. That is
# the right thing for a release and useless for testing a change.
#
# This builds from your clone in a scratch directory, which also keeps makepkg
# out of the repo root: $srcdir there would be ./src, the project's own source
# tree, and SRCDEST would drop a bare clone beside it.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/vpngate-pkgtest.XXXXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user; makepkg refuses to run as root." >&2
    exit 1
fi

# git+file:// clones committed state only, so anything unstaged is invisible
# to the build. Say so rather than quietly packaging the wrong thing.
if ! git -C "$repo_dir" diff-index --quiet HEAD -- 2>/dev/null; then
    echo "WARNING: you have uncommitted changes."
    echo "         The build uses committed state only - commit first, or the"
    echo "         package will not contain your edits."
    echo
fi

echo "Building $(git -C "$repo_dir" describe --always --dirty) from $repo_dir"
echo

sed -e "s|^source=.*|source=(\"\${pkgname}::git+file://${repo_dir}\")|" \
    -e '/#tag=/d' \
    "$repo_dir/PKGBUILD" > "$build_dir/PKGBUILD"

cd "$build_dir"
makepkg -f "$@"

package="$(find "$build_dir" -maxdepth 1 -name '*.pkg.tar.zst' -print -quit)"
if [[ -z $package ]]; then
    echo "makepkg produced no package." >&2
    exit 1
fi

# The scratch directory is removed on exit, so keep the result next to the repo.
mv "$package" "$repo_dir/"
package="$repo_dir/$(basename "$package")"

echo
echo "Built: $package"
echo
echo "Installed layout:"
tar tf "$package" | grep -v '^\.' | sed 's/^/  /'
echo
echo "To install it:  sudo pacman -U '$package'"
echo "To remove it:   sudo pacman -R vpn-gate-client"
