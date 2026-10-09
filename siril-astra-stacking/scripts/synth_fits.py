"""Synthetic FITS sequence generator and transform-calibration harness.

The generated frames carry known ground truth (translation, rotation, vignetting,
noise) so that both the FITS parser and the registration-transform derivation can
be verified without any real telescope data.

Usage:
    # CFA calibration sequence with known per-frame translations
    python3 synth_fits.py cfa /tmp/out 200 150 4

    # RGB sequence with known rotation between frames
    python3 synth_fits.py rgb /tmp/out 200 150 4 2.0

    # Print the expected ground truth for a generated set
    python3 synth_fits.py cfa /tmp/out 200 150 4 --truth
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fits_probe import build_card, pad_to_block

# Star catalogue: (x, y, peak brightness). Eight stars so that Siril's default
# -minpairs=8 is satisfied; selftest passes -minpairs=4 explicitly for robustness.
STARS = [
    (50, 50, 45000), (120, 60, 32000), (70, 110, 28000), (150, 120, 18000),
    (35, 120, 24000), (95, 30, 20000), (160, 40, 15000), (30, 80, 12000),
    # Far-corner stars: the PWS engine's catalogue demands >= 5 isolated stars
    # per frame (26 px border margin, 25 px isolation), which the original
    # corner-clustered eight cannot supply on its own.
    (260, 210, 26000), (330, 260, 22000), (240, 60, 18000), (320, 140, 16000),
]

# Header fields copied verbatim from a real ZWO Seestar S30 Pro capture of M42.
SMART_HEADER_FIELDS = [
    ("TELESCOP", "'S30 Pro_10d57b1d'", "Telescope used to acquire this image"),
    ("INSTRUME", "'imx585  '", "Instrument name"),
    ("FILTER", "'IRCUT   '", "Active filter name"),
    ("BAYERPAT", "'GRBG    '", "Bayer color pattern"),
    ("OBJECT", "'M 42    '", "Name of the object of interest"),
    ("EXPTIME", "60.", "[s] Exposure time duration"),
    ("FOCALLEN", "162.539022546324", "[mm] Focal length"),
    ("XPIXSZ", "2.90000009536743", "[um] Pixel X axis size"),
    ("YPIXSZ", "2.90000009536743", "[um] Pixel Y axis size"),
    ("GAIN", "80", "Gain value"),
    ("CCD-TEMP", "9.9375", "sensor temperature in C"),
    ("STACKCNT", "1", "Stack frame index"),
    ("CREATOR", "'ZWO Seestar S50'", "Capture software"),
    ("PRODUCER", "'ZWO '", "Powered by ZWO"),
    # Siril itself writes this out-of-range value; the parser must tolerate it.
    ("EQUINOX", "9.87654321E+107", "Equatorial equinox"),
]

TRADITIONAL_HEADER_FIELDS = [
    ("TELESCOP", "'TS-Optics 8in f/4'", "Telescope used to acquire this image"),
    ("INSTRUME", "'ASI533MM Pro'", "Instrument name"),
    ("FILTER", "'Luminance     '", "Active filter name"),
    ("OBJECT", "'M 31    '", "Name of the object of interest"),
    ("EXPTIME", "180.", "[s] Exposure time duration"),
    ("FOCALLEN", "1625.0", "[mm] Focal length"),
    ("XPIXSZ", "3.76", "[um] Pixel X axis size"),
    ("YPIXSZ", "3.76", "[um] Pixel Y axis size"),
    ("GAIN", "100", "Gain value"),
    ("CCD-TEMP", "-10.0", "sensor temperature in C"),
    ("IMAGETYP", "'Light '", "Type of image"),
]


def _header(cards, width, height, bitpix, naxis, naxis3=None):
    entries = [
        build_card("SIMPLE", "T", "file does conform to FITS standard"),
        build_card("BITPIX", str(bitpix)),
        build_card("NAXIS", str(naxis)),
        build_card("NAXIS1", str(width)),
        build_card("NAXIS2", str(height)),
    ]
    if naxis3 is not None:
        entries.append(build_card("NAXIS3", str(naxis3)))
    entries.extend(build_card(key, value, comment) for key, value, comment in cards)
    entries.append(build_card("END", ""))
    return pad_to_block("".join(entries).encode("ascii"))


def _lcg_noise(seed):
    """Deterministic linear congruential noise in [-30, 30]."""
    seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
    return ((seed >> 16) & 0xFF) / 255.0 * 60.0 - 30.0


def _sample(x, y, width, height, star_positions, seed, base, vignetting,
            psf_sigma2=7.0):
    """Evaluate the synthetic scene at pixel ``(x, y)``.

    ``psf_sigma2`` is the Gaussian star profile's sigma-squared in px^2, so a
    larger value writes blurrier stars. Varying it across frames gives the
    sequence genuine frame-quality dispersion, which the frame-quality report
    and the PWS gate's end-to-end test need.
    """
    value = base
    if vignetting:
        value += 400.0 * (1.0 - x / width) + 300.0 * (1.0 - y / height)
    value += _lcg_noise(seed)
    for star_x, star_y, brightness in star_positions:
        d2 = (x - star_x) ** 2 + (y - star_y) ** 2
        if d2 < 36 * (psf_sigma2 / 7.0):
            value += brightness * math.exp(-d2 / psf_sigma2)
    return value


def _transformed_stars(width, height, index, dx_step, dy_step, rotation_deg):
    """Star positions for frame ``index`` under the requested ground truth.

    The scene is rotated about the frame centre and then translated by
    ``(-dx, -dy)``, so that after registration against frame 0 the recovered
    displacement equals ``(dx_step * index, dy_step * index)``. This mirrors the
    convention calibrated in ``fits_probe.derive_transform``.
    """
    angle = math.radians(rotation_deg * index)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    dx, dy = dx_step * index, dy_step * index
    moved = []
    for star_x, star_y, brightness in STARS:
        cx, cy = star_x - width / 2.0, star_y - height / 2.0
        rx = cx * cos_a - cy * sin_a
        ry = cx * sin_a + cy * cos_a
        moved.append((rx + width / 2.0 - dx, ry + height / 2.0 - dy, brightness))
    return moved


def write_cfa_sequence(out_dir, width, height, frames, dx_step=3, dy_step=-2,
                       rotation_deg=0.0, smart=True, prefix="sub",
                       blur_per_frame=0.0, vignetting=True):
    """Write a CFA (``NAXIS=2``) FITS sequence with known transforms.

    ``blur_per_frame`` adds that much Gaussian sigma-squared per frame index
    (frame 0 stays at the base 7.0), producing real frame-quality dispersion:
    with 20 frames and ``blur_per_frame=1.4`` the last frame's stars are twice
    as wide as the first's, which is what the PWS gate's dispersion test needs.

    Returns:
        List of ``(path, truth_dict)`` where ``truth_dict`` holds the intended
        ``dx``/``dy``/``rotation_deg`` for that frame.
    """
    os.makedirs(out_dir, exist_ok=True)
    cards = SMART_HEADER_FIELDS if smart else TRADITIONAL_HEADER_FIELDS
    header = _header(cards, width, height, 16, 2)

    written = []
    for index in range(frames):
        star_positions = _transformed_stars(
            width, height, index, dx_step, dy_step, rotation_deg
        )
        psf_sigma2 = 7.0 + blur_per_frame * index
        pixels = bytearray()
        seed = 12345 + index * 7
        for y in range(height):
            for x in range(width):
                value = _sample(x, y, width, height, star_positions, seed,
                                2000.0, vignetting, psf_sigma2=psf_sigma2)
                pixels += struct.pack(">H", max(0, min(65535, int(value))))
        path = os.path.join(out_dir, "%s%03d.fit" % (prefix, index))
        with open(path, "wb") as handle:
            handle.write(header)
            handle.write(pad_to_block(bytes(pixels)))
        written.append((path, {
            "dx": dx_step * index,
            "dy": dy_step * index,
            "rotation_deg": rotation_deg * index,
        }))
    return written


def write_rgb_sequence(out_dir, width, height, frames, dx_step=2, dy_step=-1,
                       rotation_deg=0.0, prefix="sub"):
    """Write an RGB (``NAXIS=3``) 16-bit FITS sequence with known transforms."""
    os.makedirs(out_dir, exist_ok=True)
    header = _header(TRADITIONAL_HEADER_FIELDS, width, height, 16, 3, naxis3=3)

    written = []
    for index in range(frames):
        star_positions = _transformed_stars(
            width, height, index, dx_step, dy_step, rotation_deg
        )
        planes = [bytearray() for _ in range(3)]
        seed = 999 + index * 13
        for y in range(height):
            for x in range(width):
                base = _sample(x, y, width, height, star_positions, seed, 2000.0, True)
                for plane, gain in zip(planes, (1.00, 0.93, 0.86)):
                    plane += struct.pack(">H", max(0, min(65535, int(base * gain))))
        pixels = b"".join(planes)
        path = os.path.join(out_dir, "%s%03d.fit" % (prefix, index))
        with open(path, "wb") as handle:
            handle.write(header)
            handle.write(pad_to_block(bytes(pixels)))
        written.append((path, {
            "dx": dx_step * index,
            "dy": dy_step * index,
            "rotation_deg": rotation_deg * index,
        }))
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cfa", "rgb"])
    parser.add_argument("out_dir")
    parser.add_argument("width", type=int, nargs="?", default=200)
    parser.add_argument("height", type=int, nargs="?", default=150)
    parser.add_argument("frames", type=int, nargs="?", default=4)
    parser.add_argument("--rotation", type=float, default=0.0,
                        help="per-frame rotation in degrees")
    parser.add_argument("--dx", type=int, default=None)
    parser.add_argument("--dy", type=int, default=None)
    parser.add_argument("--traditional", action="store_true",
                        help="use traditional-equipment header fields")
    parser.add_argument("--truth", action="store_true",
                        help="print ground truth after generating")
    args = parser.parse_args()

    dx = args.dx if args.dx is not None else (3 if args.mode == "cfa" else 2)
    dy = args.dy if args.dy is not None else (-2 if args.mode == "cfa" else -1)

    if args.mode == "cfa":
        written = write_cfa_sequence(
            args.out_dir, args.width, args.height, args.frames,
            dx_step=dx, dy_step=dy, rotation_deg=args.rotation,
            smart=not args.traditional,
        )
    else:
        written = write_rgb_sequence(
            args.out_dir, args.width, args.height, args.frames,
            dx_step=dx, dy_step=dy, rotation_deg=args.rotation,
        )

    print("wrote %d %s frames to %s" % (len(written), args.mode, args.out_dir))
    if args.truth:
        for path, truth in written:
            print("  %s dx=%+d dy=%+d rot=%+.2f"
                  % (os.path.basename(path), truth["dx"], truth["dy"],
                     truth["rotation_deg"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())