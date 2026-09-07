# Install and build ngram AR (from repo root). Requires Node.js >= 22.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "ngramAR")
npm install
npm run build
Write-Host "ngram AR ready. From ngramAR: npm run ngramar  (or see ngramAR/docs/NGRAM.md)"
