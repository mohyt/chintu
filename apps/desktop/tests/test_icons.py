import unittest

from chintu_desktop.icons import icon_set, render_icon
from chintu_desktop.status import Presence


def _pixels(image):
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if flattened else image.getdata())


def _opaque_pixels(image):
    return sum(1 for pixel in _pixels(image) if pixel[3] > 0)


class IconTests(unittest.TestCase):
    def test_each_state_renders_requested_size(self):
        for mono in (False, True):
            for presence in Presence:
                for size in (22, 64):
                    image = render_icon(presence, size=size, mono=mono)
                    self.assertEqual(image.size, (size, size), (presence, mono, size))
                    self.assertEqual(image.mode, "RGBA")
                    self.assertGreater(_opaque_pixels(image), size * size // 10)

    def test_states_look_different(self):
        images = icon_set(size=32)
        self.assertEqual(set(images), set(Presence))
        data = {presence: images[presence].tobytes() for presence in Presence}
        self.assertEqual(len(set(data.values())), 3)

    def test_connected_has_a_ring_outside_the_orb(self):
        connected = render_icon(Presence.CONNECTED, size=64)
        away = render_icon(Presence.AWAY, size=64)
        # The ring sits near the edge, where the dim orb is transparent.
        self.assertGreater(connected.getpixel((32, 3))[3], 0)
        self.assertEqual(away.getpixel((32, 3))[3], 0)

    def test_mono_uses_only_black(self):
        for presence in Presence:
            image = render_icon(presence, size=48, mono=True)
            colours = {pixel[:3] for pixel in _pixels(image) if pixel[3] > 200}
            self.assertEqual(colours, {(0, 0, 0)}, presence)

    def test_connected_is_brighter_than_away(self):
        def brightness(image):
            pixels = [sum(p[:3]) for p in _pixels(image) if p[3] > 0]
            return sum(pixels) / len(pixels)
        self.assertGreater(brightness(render_icon(Presence.CONNECTED)), brightness(render_icon(Presence.AWAY)))


if __name__ == "__main__":
    unittest.main()
