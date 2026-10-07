#!/bin/bash
# Download Binance USDT-M perp aggTrades daily zips from data.binance.vision (public archive, not the API).
# usage: dl_aggtrades.sh START END SYM...
D=/root/bitana/research_cache/aggtrades
s=$1; e=$2; shift 2
for sym in "$@"; do mkdir -p $D/$sym
  d=$s; while [[ "$d" < "$e" || "$d" == "$e" ]]; do
    f=$D/$sym/$sym-aggTrades-$d.zip
    [ -s $f ] || curl -sf -o $f https://data.binance.vision/data/futures/um/daily/aggTrades/$sym/$sym-aggTrades-$d.zip || { echo "miss $sym $d"; rm -f $f; }
    d=$(date -I -d "$d + 1 day"); done; done
echo done
