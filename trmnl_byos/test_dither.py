import hashlib

import numpy as np

from dither import ordered_gray4


def _gradient(h=64, w=256):
    row = np.arange(w, dtype=np.uint8)
    return np.tile(row, (h, 1))


def test_output_only_palette_values():
    out = ordered_gray4(_gradient())
    assert set(np.unique(out).tolist()) <= {0, 85, 170, 255}


def test_no_dither_is_nearest_level():
    # Solid mid-grey -> nearest of {0,85,170,255} is 170 (128/255*3=1.506 -> round 2 -> 170).
    flat = np.full((8, 8), 128, np.uint8)
    out = ordered_gray4(flat, dither=False)
    assert np.all(out == 170)


def test_pure_black_and_white_preserved():
    assert np.all(ordered_gray4(np.zeros((8, 8), np.uint8)) == 0)
    assert np.all(ordered_gray4(np.full((8, 8), 255, np.uint8)) == 255)


def test_deterministic_hash():
    out = ordered_gray4(_gradient())
    digest = hashlib.md5(out.tobytes()).hexdigest()
    assert digest == EXPECTED_HASH, digest


# Filled in from a first run, then locked to catch accidental algorithm drift.
EXPECTED_HASH = "7935c9315109dbef398be9b70fabe796"


if __name__ == "__main__":
    # Print the gradient hash so EXPECTED_HASH can be locked in.
    out = ordered_gray4(_gradient())
    print("gradient hash:", hashlib.md5(out.tobytes()).hexdigest())
    for name, fn in list(globals().items()):
        if name.startswith("test_") and name != "test_deterministic_hash":
            fn()
            print("PASS", name)
    print("done")
