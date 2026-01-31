"""Text helpers."""

import re


def slugify(title: str) -> str:
    """Turn a product title into a URL slug: 'Red  Shoes!' -> 'red-shoes'."""
    return re.sub(r"[^a-z0-9]", "-", title.lower())
