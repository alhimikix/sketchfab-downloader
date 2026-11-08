"""Portable HTTPS trust for builds that run without a system Python install."""
from functools import lru_cache
import ssl

import certifi


@lru_cache(maxsize=1)
def ssl_context():
    context = ssl.create_default_context()
    # Retain OS/custom trust and add the bundled Mozilla CA roots.
    context.load_verify_locations(cafile=certifi.where())
    return context
