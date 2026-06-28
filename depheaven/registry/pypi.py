"""PyPI JSON API client."""

from typing import Optional

try:
    import httpx
    _CLIENT = "httpx"
except ImportError:
    import urllib.request as _urllib
    _CLIENT = "urllib"


def get_latest_version(package: str) -> Optional[str]:
    """Return the latest stable version of a PyPI package, or None on failure."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        if _CLIENT == "httpx":
            import httpx
            r = httpx.get(url, timeout=8, follow_redirects=True)
            if r.status_code == 200:
                return r.json()["info"]["version"]
        else:
            import json, urllib.request
            with urllib.request.urlopen(url, timeout=8) as r:
                return json.loads(r.read())["info"]["version"]
    except Exception:
        pass
    return None
