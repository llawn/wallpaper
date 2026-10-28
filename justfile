set positional-arguments

# Thin layer over `python3 -m wallman <command>`; run `just` to list the recipes.

default:
    @just --list

# hooksPath -> scripts/hooks, submodule init, download the files that are not in git
setup:
    git config core.hooksPath scripts/hooks
    git submodule update --init --recursive
    python3 -m wallman fetch

# open the web UI
serve *args:
    python3 -m wallman serve "$@"

# just add FILE DEST --artist A --title T --license L [--year Y] [--source URL] [--crop x,y,w,h]
add +args:
    python3 -m wallman add "$@"

# just add-url URL DEST --artist A --title T --license L ...
add-url +args:
    python3 -m wallman add-url "$@"

# delete wallpapers: just rm painting/marine/foo.jpg
rm +refs:
    python3 -m wallman rm "$@"

# move a wallpaper: just mv painting/marine/foo.jpg painting/nocturne
mv ref dest:
    python3 -m wallman mv "$1" "$2"

# crop a wallpaper (from its original download when it has one): just crop painting/marine/foo.jpg 0,120,3840,2160
crop ref box:
    python3 -m wallman crop "$1" "$2"

# sync wallpapers.toml with the images (--stage to stage the result)
sync *args:
    python3 -m wallman sync "$@"

validate:
    python3 -m wallman validate

# refuse staged images that may not be redistributed
guard:
    python3 -m wallman guard

# download the files that are not in git
fetch *folders:
    python3 -m wallman fetch "$@"

gitignore:
    python3 -m wallman gitignore

readme:
    python3 -m wallman readme

test:
    python3 -m unittest discover -s tests -t .

# UI performance on a synthetic collection in a temp dir (spec targets; exits 1 on a miss): just bench [--n 10000]
bench *args:
    python3 scripts/bench.py "$@"

# write a commented default web UI theme config (~/.config/wallman/config.toml); never overwrites
theme-init:
    python3 -m wallman theme-init
