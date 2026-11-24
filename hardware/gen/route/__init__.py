"""The breakout board's routing engine (task R1a, 2026-11-24).

A grid maze router grown from the NI-island trial router (git show trial-ni-opus:
hardware/breakout/trial_route_ni.py), with its rasterisation and search moved to numpy.

    rules.py   Rules.load(breakout_dir): widths and clearances from the rule files (no numpy)
    stage.py   the obstacle-file and route-file schemas, Pad, RouteResult (no numpy)
    grid.py    Grid: obstacles as distance fields, thresholded per clearance case (numpy)
    search.py  route_connection(): A* over the grid (numpy)

rules and stage import without numpy, so KiCad's Python (task R1b's board_io) can use them;
the names below that need numpy are imported only when numpy is there.
"""
from .rules import Rules, RulesError  # noqa: F401
from .stage import (OBSTACLES_SCHEMA, Pad, RouteResult, assert_stayed_local,  # noqa: F401
                    net_order, pads_of, prune_dangling)

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover - KiCad's Python
    pass
else:
    from .grid import Grid  # noqa: F401
    from .search import Area, Copper, Path, ViaSite, rect_area, route_connection  # noqa: F401
