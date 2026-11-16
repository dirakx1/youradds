"""Currency conversion between the currency youradds shows (DEFAULT_CURRENCY, e.g. COP) and the currency an ad
account bills in (e.g. USD). Rates come from a free public source and are cached; they can be pinned with
FX_RATE_<FROM>_<TO> (e.g. FX_RATE_COP_USD=0.00026). If no rate can be had we raise instead of guessing, because a
wrong rate means a wrong budget on a real ad account."""
import time

import httpx

from .config import env

TTL = 6 * 3600              # a fresh rate is reused for 6 hours
STALE_OK = 7 * 24 * 3600    # if the source is down, a rate up to a week old beats refusing every budget change
_cache: dict[str, tuple[float, dict]] = {}


class FxError(RuntimeError):
    pass


def _table(base: str) -> dict:
    hit = _cache.get(base)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        r = httpx.get(f"https://open.er-api.com/v6/latest/{base}", timeout=8)
        r.raise_for_status()
        d = r.json()
        if d.get("result") != "success" or not isinstance(d.get("rates"), dict):
            raise ValueError("unexpected response")
        _cache[base] = (time.time(), d["rates"])
        return d["rates"]
    except (httpx.HTTPError, ValueError):
        if hit and time.time() - hit[0] < STALE_OK:
            return hit[1]
        raise FxError(f"Could not get the {base} exchange rate right now. Nothing was changed; try again shortly.")


def rate(frm: str, to: str) -> float:
    frm, to = frm.upper(), to.upper()
    if frm == to:
        return 1.0
    pinned = env(f"FX_RATE_{frm}_{to}")
    if pinned:
        try:
            v = float(pinned)
        except ValueError:
            raise FxError(f"FX_RATE_{frm}_{to} is not a number")
        if v > 0:
            return v
    r = _table(frm).get(to)
    if not r or r <= 0:
        raise FxError(f"No exchange rate from {frm} to {to}")
    return float(r)


def convert(amount: float, frm: str, to: str) -> float:
    return amount * rate(frm, to)
