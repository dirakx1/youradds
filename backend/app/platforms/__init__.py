from .meta import Meta
from .tiktok import TikTok
from .google import Google

CLASSES = {c.name: c for c in (Meta, TikTok, Google)}


def make(platform: str, creds: dict):
    """A connector bound to one user's credentials."""
    return CLASSES[platform](creds)
