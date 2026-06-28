"""npm registry client."""

from typing import Optional


def get_latest_version(package: str) -> Optional[str]:
    """Return the latest version of an npm package, or None on failure."""
    import json
    encoded = package.replace("/", "%2F")
    url = f"https://registry.npmjs.org/{encoded}/latest"
    try:
        try:
            import httpx
            r = httpx.get(url, timeout=8, follow_redirects=True)
            if r.status_code == 200:
                return r.json().get("version")
        except ImportError:
            import urllib.request
            with urllib.request.urlopen(url, timeout=8) as r:
                return json.loads(r.read()).get("version")
    except Exception:
        pass
    return None
