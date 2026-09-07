"""Cryptocurrency spot prices.

Major assets: CoinGecko public API (aggregated exchange spot, no key for light use).
Long-tail / on-chain: DexScreener free API (rate limit ~300/min).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import aiohttp

from ngram.presence.tools.registry import tool

_DEX_BASE = "https://api.dexscreener.com"
_COINGECKO = "https://api.coingecko.com/api/v3"
_TIMEOUT = aiohttp.ClientTimeout(total=12)
_HEADERS = {"User-Agent": "NgramBot/0.1", "Accept": "application/json"}

# CoinGecko coin ids for common tickers / names (lowercase lookup)
_COINGECKO_IDS: dict[str, str] = {}
for _sym, _id in (
    ("BTC", "bitcoin"),
    ("BITCOIN", "bitcoin"),
    ("ETH", "ethereum"),
    ("ETHEREUM", "ethereum"),
    ("SOL", "solana"),
    ("SOLANA", "solana"),
    ("ZEC", "zcash"),
    ("ZCASH", "zcash"),
    ("XRP", "ripple"),
    ("DOGE", "dogecoin"),
    ("DOGECOIN", "dogecoin"),
    ("ADA", "cardano"),
    ("AVAX", "avalanche-2"),
    ("DOT", "polkadot"),
    ("MATIC", "matic-network"),
    ("POL", "polygon-ecosystem-token"),
    ("LINK", "chainlink"),
    ("ATOM", "cosmos"),
    ("LTC", "litecoin"),
    ("BCH", "bitcoin-cash"),
    ("XLM", "stellar"),
    ("ALGO", "algorand"),
    ("NEAR", "near"),
    ("APT", "aptos"),
    ("ARB", "arbitrum"),
    ("OP", "optimism"),
    ("SUI", "sui"),
    ("TON", "the-open-network"),
    ("SHIB", "shiba-inu"),
    ("PEPE", "pepe"),
    ("TRX", "tron"),
    ("BNB", "binancecoin"),
    ("UNI", "uniswap"),
    ("AAVE", "aave"),
    ("CRV", "curve-dao-token"),
    ("MKR", "maker"),
    ("SNX", "havven"),
):
    _COINGECKO_IDS[_sym] = _id

# Prefer DEX pairs on these chains first (closer to canonical spot for wrapped majors)
_CHAIN_PRIORITY: dict[str, int] = {
    "ethereum": 100,
    "bsc": 90,
    "arbitrum": 88,
    "base": 86,
    "optimism": 84,
    "polygon-pos": 82,
    "avalanche": 80,
    "fantom": 70,
    "cronos": 68,
    "solana": 50,  # good for SOL; wrapped BTC/ETH on Solana can misprice vs spot
    "bitcoin": 95,
}

_STABLECOIN_SYMBOLS = frozenset({
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX",
    "LUSD", "SUSD", "USDD", "PYUSD", "EUSD", "CUSD",
})


def _coingecko_id_for_query(q: str) -> str | None:
    key = q.strip().upper().replace(" ", "")
    if not key:
        return None
    return _COINGECKO_IDS.get(key)


async def _coingecko_price(cg_id: str) -> dict[str, Any] | None:
    url = f"{_COINGECKO}/simple/price?ids={quote(cg_id)}&vs_currencies=usd&include_24hr_change=true"
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
        async with session.get(url) as resp:
            if resp.status == 429:
                return None
            if resp.status >= 400:
                return None
            data = await resp.json()
    row = data.get(cg_id)
    if not row or "usd" not in row:
        return None
    out: dict[str, Any] = {
        "source": "coingecko",
        "price_usd": str(row["usd"]),
    }
    if row.get("usd_24h_change") is not None:
        out["change_24h"] = f"{row['usd_24h_change']:.2f}%"
    return out


def _pick_best_pair(pairs: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Pick best DEX pair; prefer major L1/L2 chains over exotic wrapped pools."""
    query_upper = query.strip().upper()

    scored: list[tuple[float, dict[str, Any]]] = []
    for p in pairs:
        base = (p.get("baseToken") or {})
        quote_tok = (p.get("quoteToken") or {})
        price_usd = p.get("priceUsd")
        if not price_usd:
            continue

        base_sym = (base.get("symbol") or "").upper()
        quote_sym = (quote_tok.get("symbol") or "").upper()
        chain = str(p.get("chainId") or "").lower()

        if base_sym in _STABLECOIN_SYMBOLS and query_upper not in _STABLECOIN_SYMBOLS:
            continue

        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)

        chain_boost = float(_CHAIN_PRIORITY.get(chain, 0))
        score = chain_boost * 1e6 + liq + vol * 0.1

        if base_sym == query_upper:
            score += 1e12
        elif query_upper in base_sym or query_upper in (base.get("name") or "").upper():
            score += 5e11

        if quote_sym in _STABLECOIN_SYMBOLS or quote_sym in ("USD", "WETH", "WBTC", "ETH", "BTC"):
            score += 1e11

        scored.append((score, p))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _format_pair(pair: dict[str, Any]) -> dict[str, Any]:
    base = pair.get("baseToken") or {}
    quote_tok = pair.get("quoteToken") or {}
    price_change = pair.get("priceChange") or {}
    volume = pair.get("volume") or {}
    liquidity = pair.get("liquidity") or {}
    fdv = pair.get("fdv")

    out: dict[str, Any] = {
        "source": "dexscreener",
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "price_usd": pair.get("priceUsd"),
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "pair": f"{base.get('symbol')}/{quote_tok.get('symbol')}",
    }

    if price_change.get("h1") is not None:
        out["change_1h"] = f"{price_change['h1']}%"
    if price_change.get("h24") is not None:
        out["change_24h"] = f"{price_change['h24']}%"
    if volume.get("h24"):
        out["volume_24h_usd"] = volume["h24"]
    if liquidity.get("usd"):
        out["liquidity_usd"] = liquidity["usd"]
    if fdv:
        out["fully_diluted_valuation"] = fdv
    if pair.get("url"):
        out["dexscreener_url"] = pair["url"]

    return out


async def _dex_search(query: str) -> list[dict[str, Any]]:
    url = f"{_DEX_BASE}/latest/dex/search?q={quote(query)}"
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
        async with session.get(url) as resp:
            if resp.status >= 400:
                return []
            data = await resp.json()
            return data.get("pairs") or []


@tool(
    name="get_crypto_price",
    description=(
        "Get the current spot USD price of a cryptocurrency. "
        "Uses aggregated market data for major coins (BTC, ETH, SOL, ZEC, etc.). "
        "For obscure tokens, uses on-chain DEX data. "
        "Call once per token — do NOT batch multiple tokens into one call."
    ),
)
async def get_crypto_price(token: str) -> str:
    q = (token or "").strip()
    if not q:
        return json.dumps({"error": "empty token query"})

    try:
        cg_id = _coingecko_id_for_query(q)
        if cg_id:
            cg = await _coingecko_price(cg_id)
            if cg:
                cg["symbol"] = q.strip().upper()
                cg["name"] = cg_id.replace("-", " ").title()
                return json.dumps(cg, ensure_ascii=False)

        pairs = await _dex_search(q)
        if not pairs:
            return json.dumps({"error": f"no results for '{q}'", "token": q})

        best = _pick_best_pair(pairs, q)
        if not best:
            return json.dumps({"error": f"no priced pair found for '{q}'", "token": q})

        return json.dumps(_format_pair(best), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "token": q})


@tool(
    name="search_crypto_token",
    description=(
        "Search for a cryptocurrency token or trading pair on DexScreener. "
        "Returns top on-chain results with price, volume, liquidity, and chain. "
        "For major coins' spot price, prefer get_crypto_price instead."
    ),
)
async def search_crypto_token(query: str, max_results: int = 5) -> str:
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "empty query"})

    try:
        pairs = await _dex_search(q)
        if not pairs:
            return json.dumps({"query": q, "results": []})

        ranked = sorted(
            (p for p in pairs if p.get("priceUsd")),
            key=lambda p: (
                _CHAIN_PRIORITY.get(str(p.get("chainId") or "").lower(), 0),
                float((p.get("liquidity") or {}).get("usd") or 0),
            ),
            reverse=True,
        )

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in ranked:
            base = (p.get("baseToken") or {})
            sym = (base.get("symbol") or "").upper()
            if sym in _STABLECOIN_SYMBOLS:
                continue
            key = f"{sym}_{p.get('chainId')}"
            if key in seen:
                continue
            seen.add(key)
            results.append(_format_pair(p))
            if len(results) >= max_results:
                break

        return json.dumps({"query": q, "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "query": q})
