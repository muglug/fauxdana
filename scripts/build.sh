#!/bin/sh
# Rebuild the four Dejardana faces into ttfs/ from a directory of DejaVu Sans
# TTFs (DejaVuSans.ttf, DejaVuSans-Bold.ttf, DejaVuSans-Oblique.ttf,
# DejaVuSans-BoldOblique.ttf). Run from anywhere:
#   scripts/build.sh /usr/share/fonts/truetype/dejavu
set -eu
src=${1:?usage: build.sh DEJAVU_TTF_DIR}
root=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-$root/.venv/bin/python}
make="$py $root/scripts/make_dejardana.py"
metrics="$root/verdana-metrics.json"
accents="$root/verdana-accents.json"
$make "$src/DejaVuSans.ttf"             "$metrics" "$root/ttfs/Dejardana-Regular.ttf"                   --accents "$accents"
$make "$src/DejaVuSans-Bold.ttf"        "$metrics" "$root/ttfs/Dejardana-Bold.ttf"       --bold          --accents "$accents"
$make "$src/DejaVuSans-Oblique.ttf"     "$metrics" "$root/ttfs/Dejardana-Italic.ttf"     --italic        --accents "$accents"
$make "$src/DejaVuSans-BoldOblique.ttf" "$metrics" "$root/ttfs/Dejardana-BoldItalic.ttf" --bold --italic --accents "$accents"
