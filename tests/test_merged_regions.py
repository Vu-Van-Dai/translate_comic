"""A region holding two balloons' lettering is split, one balloon is not."""
import numpy as np

from comic.vision.regions import text_blocks

CAP = 20          # letter height in px
ADV = 16          # letter advance
PITCH = 28        # line pitch


def _line(mask, cx, top, letters):
    """Draw a centred line of `letters` blob-letters (2 px apart) at `top`."""
    width = letters * ADV
    x = cx - width // 2
    for i in range(letters):
        mask[top:top + CAP, x + i * ADV:x + i * ADV + ADV - 3] = 255


def _balloon(mask, cx, top, lengths):
    for k, n in enumerate(lengths):
        _line(mask, cx, top + k * PITCH, n)


def test_one_centred_balloon_is_one_block():
    m = np.zeros((300, 400), np.uint8)
    _balloon(m, 200, 20, [6, 11, 13, 9, 4])      # an egg of lines
    assert len(text_blocks(m)) <= 1


def test_side_by_side_lobes_split():
    # Dare mo ch.1 p.20: a block top right, another lower left, gutter between.
    m = np.zeros((320, 520), np.uint8)
    _balloon(m, 380, 10, [8, 8, 9, 9, 9])
    _balloon(m, 90, 160, [3, 4, 6])
    blocks = text_blocks(m)
    assert len(blocks) == 2


def test_stacked_offset_lobes_split():
    # Dare mo ch.1 p.23: the second block starts one line below the first,
    # overlapping it a little but centred far to the left.
    m = np.zeros((420, 480), np.uint8)
    _balloon(m, 330, 10, [9, 9, 10])
    _balloon(m, 150, 10 + 3 * PITCH + 12, [11, 12, 9, 9, 8, 7])
    assert len(text_blocks(m)) == 2


def test_lone_punctuation_beside_a_balloon_is_not_a_block():
    m = np.zeros((300, 400), np.uint8)
    _balloon(m, 150, 20, [8, 10, 7])
    m[200:212, 330:334] = 255                     # a "!" off to the side
    m[200:212, 340:344] = 255
    assert len(text_blocks(m)) <= 1


def test_vertical_columns_split_by_transposing():
    m = np.zeros((320, 520), np.uint8)             # drawn as rows, read as columns
    _balloon(m, 380, 10, [8, 8, 9, 9, 9])
    _balloon(m, 90, 160, [3, 4, 6])
    blocks = text_blocks(np.ascontiguousarray(m.T), vertical=True)
    assert len(blocks) == 2
    # returned in the vertical crop's own coordinates
    assert all(b.y2 <= 520 and b.x2 <= 320 for b in blocks)
