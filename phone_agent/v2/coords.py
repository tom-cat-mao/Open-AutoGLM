"""Coordinate conversion utility for relative-to-absolute pixel mapping.

Model outputs 0-1000 relative coordinates. Tools MUST convert to absolute
pixels via convert_relative_to_absolute() before passing to device commands.
Ported from the v1 ``graph/tools/coords.py`` (P0 #1 semantics preserved).
"""

from __future__ import annotations


def convert_relative_to_absolute(
    element: list[int], screen_width: int, screen_height: int
) -> tuple[int, int]:
    """Convert relative coordinates (0-1000) to absolute pixels.

    Args:
        element: Relative coordinates [x, y] in 0-1000 range.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.

    Returns:
        Tuple of (absolute_x, absolute_y) in pixels.
    """
    x = int(element[0] / 1000 * screen_width)
    y = int(element[1] / 1000 * screen_height)
    return x, y
