#!/usr/bin/env sh
# Install and build ngram AR (from repo root). Requires Node.js >= 22.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/ngramAR"
npm install
npm run build
echo "ngram AR ready. From ngramAR: npm run ngramar  (or see ngramAR/docs/NGRAM.md)"
