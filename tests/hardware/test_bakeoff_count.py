"""hardware/breakout/bakeoff/count_drc.py's pure parts (task R1c write-up, fix round 1).

The counting itself needs KiCad's Python and a board; what is tested here is the split at
y = 226 (R1d's In2.Cu metric against J45's exempt residue) and the places the clearance
entries are counted in, so the numbers in bakeoff-report.md §3 mean what it says."""
import pytest

from hardware.breakout.bakeoff import count_drc as cd


def test_the_line_is_j45s():
    assert cd.J45_Y == 226.0 and cd.OPTO_X == 55.0


@pytest.mark.parametrize("y1,y2,length,want", [
    (200.0, 220.0, 20.0, (20.0, 0.0)),     # wholly outside J45
    (226.0, 230.0, 4.0, (0.0, 4.0)),       # starts on the line: J45's
    (230.0, 235.0, 5.0, (0.0, 5.0)),       # wholly at J45
    (224.0, 228.0, 4.0, (2.0, 2.0)),       # crosses: clipped at the line
    (228.0, 224.0, 8.0, (4.0, 4.0)),       # either direction; length is the track's own
    (210.0, 226.0, 16.0, (16.0, 0.0)),     # ends on the line: outside
])
def test_split_at_clips_each_track_at_y_226(y1, y2, length, want):
    assert cd.split_at(y1, y2, length) == pytest.approx(want)


@pytest.mark.parametrize("x,y,want", [
    (30.0, 230.0, "j45"), (60.0, 230.0, "j45"),        # J45 first, whatever x
    (60.0, 150.0, "opto_field"), (55.0, 225.9, "opto_field"),
    (54.9, 150.0, "elsewhere"), (10.0, 120.0, "elsewhere"),
])
def test_places_do_not_overlap(x, y, want):
    assert cd.place(x, y) == want
