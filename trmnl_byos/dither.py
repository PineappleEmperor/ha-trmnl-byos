"""Ordered (Bayer) dithering to a 4-level grey palette.

Reproduces the `dither_method=ordered&palette=gray-4` output the previous screenshot
service produced. Ordered dithering is used instead of Floyd-Steinberg because its pattern
is temporally stable: it does not shift between successive frames, which avoids ghosting on
repeated e-ink refreshes. Pillow only offers Floyd-Steinberg, so this is implemented directly
with numpy — pure, deterministic, no external binary.
"""

import numpy as np

# 8x8 Bayer threshold matrix, values 0..63.
_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float64)

_LEVELS = 4
_STEP = 255.0 / (_LEVELS - 1)  # 85.0 -> output values {0, 85, 170, 255}


def _threshold_map(h: int, w: int) -> np.ndarray:
    """Bayer matrix normalised to (0,1), tiled to cover an h x w image."""
    tile = (_BAYER8 + 0.5) / 64.0
    reps_y = (h + 7) // 8
    reps_x = (w + 7) // 8
    return np.tile(tile, (reps_y, reps_x))[:h, :w]


def ordered_gray4(gray: np.ndarray, dither: bool = True) -> np.ndarray:
    """Quantise an 8-bit grayscale image to 4 grey levels {0,85,170,255}.

    gray: HxW uint8 array (a Pillow "L" image as an array).
    dither: True -> ordered Bayer dithering; False -> plain nearest-level quantisation.
    Returns an HxW uint8 array containing only the four palette values.
    """
    scaled = gray.astype(np.float64) / 255.0 * (_LEVELS - 1)
    floor = np.floor(scaled)
    if dither:
        frac = scaled - floor
        out_level = floor + (frac > _threshold_map(*gray.shape)).astype(np.float64)
    else:
        out_level = np.round(scaled)
    out_level = np.clip(out_level, 0, _LEVELS - 1)
    return (out_level * _STEP).round().astype(np.uint8)
