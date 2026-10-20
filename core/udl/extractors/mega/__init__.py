"""Built-in MEGA client (MegaBasterd-inspired) with optional external CLI fallback."""

from udl.extractors.mega.extractor import mega_extractor
from udl.extractors.mega.links import link_info, matches, parse_link

__all__ = [
    "mega_extractor",
    "matches",
    "parse_link",
    "link_info",
]
