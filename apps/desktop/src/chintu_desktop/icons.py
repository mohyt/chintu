"""Procedurally drawn purple orb icons, one per presence state.

Colour variant (default): the orb from the Android home screen.
- connected: orb with a lit lavender ring
- away: dim orb
- down: orb with a small slash

Monochrome variant (`mono=True`): black shapes on transparency, the shape language
Apple uses for template menu bar images. pystray does not flag images as templates,
so this variant suits light menu bars; the colour variant works on both.
"""

from PIL import Image, ImageDraw

from .status import Presence

# Palette shared with the pairing page and the Android orb.
ORB_CORE = (199, 169, 255)       # #C7A9FF
ORB_EDGE = (92, 48, 168)         # deep violet
ORB_DIM_CORE = (120, 104, 160)
ORB_DIM_EDGE = (54, 40, 88)
RING = (244, 240, 255)           # #F4F0FF
SLASH = (255, 96, 96)
BLACK = (0, 0, 0)

SUPERSAMPLE = 4


def _gradient_orb(draw, box, core, edge, steps=24):
    """Approximate a radial gradient with concentric ellipses (no numpy needed)."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = (x1 - x0) / 2
    for i in range(steps):
        t = i / (steps - 1)
        r = radius * (1 - t)
        colour = tuple(round(edge[k] + (core[k] - edge[k]) * t) for k in range(3))
        # Shift the highlight up-left so the orb reads as lit from above.
        ox, oy = -radius * 0.18 * t, -radius * 0.22 * t
        draw.ellipse((cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy), fill=colour + (255,))


def _slash(draw, size, width, colour):
    """A short diagonal bar across the lower-right of the orb."""
    margin = size * 0.26
    draw.line((margin, size - margin, size - margin, margin), fill=colour, width=width)


def render_icon(presence, size=64, mono=False):
    """Return an RGBA `PIL.Image` of `size` x `size` pixels for the given presence."""
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = big * 0.14
    orb = (pad, pad, big - pad, big - pad)

    if mono:
        _render_mono(image, draw, presence, big, orb)
    else:
        _render_colour(image, draw, presence, big, orb)

    return image.resize((size, size), Image.LANCZOS)


def _render_colour(image, draw, presence, big, orb):
    if presence is Presence.CONNECTED:
        ring_width = max(2, big // 16)
        draw.ellipse((big * 0.04, big * 0.04, big * 0.96, big * 0.96), outline=RING + (255,), width=ring_width)
        _gradient_orb(draw, orb, ORB_CORE, ORB_EDGE)
    elif presence is Presence.AWAY:
        _gradient_orb(draw, orb, ORB_DIM_CORE, ORB_DIM_EDGE)
    else:
        _gradient_orb(draw, orb, ORB_DIM_CORE, ORB_DIM_EDGE)
        # Light outline first so the slash stays visible over the dark orb edge.
        _slash(draw, big, big // 7, (6, 3, 22, 255))
        _slash(draw, big, big // 12, SLASH + (255,))


def _render_mono(image, draw, presence, big, orb):
    if presence is Presence.CONNECTED:
        ring_width = max(2, big // 16)
        draw.ellipse((big * 0.04, big * 0.04, big * 0.96, big * 0.96), outline=BLACK + (255,), width=ring_width)
        inner = tuple(v + (big * 0.04 if i < 2 else -big * 0.04) for i, v in enumerate(orb))
        draw.ellipse(inner, fill=BLACK + (255,))
    elif presence is Presence.AWAY:
        # A hollow orb reads as "dim" in a single colour.
        draw.ellipse(orb, outline=BLACK + (255,), width=max(2, big // 14))
    else:
        draw.ellipse(orb, fill=BLACK + (255,))
        # Cut a transparent gap through the orb, then draw the slash inside the gap.
        gap = Image.new("L", image.size, 0)
        _slash(ImageDraw.Draw(gap), big, big // 6, 255)
        image.paste((0, 0, 0, 0), mask=gap)
        _slash(draw, big, big // 16, BLACK + (255,))


def icon_set(size=64, mono=False):
    """All three icons, keyed by presence, rendered once at startup."""
    return {presence: render_icon(presence, size=size, mono=mono) for presence in Presence}
