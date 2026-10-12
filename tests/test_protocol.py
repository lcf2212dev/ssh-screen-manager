import io
import math
import struct
import unittest

from ssh_screen_manager.protocol import normalized_point, read_packet, ssh_command, unit, validate_host, write_packet


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_binary_and_sequential(self):
        stream = io.BytesIO()
        write_packet(stream, {"type": "frame", "monitor": "DP-2"}, b"\xff\xd8\x00\n\x80")
        write_packet(stream, {"type": "hello", "monitors": []})
        stream.seek(0)
        self.assertEqual(read_packet(stream)[1], b"\xff\xd8\x00\n\x80")
        self.assertEqual(read_packet(stream)[0]["type"], "hello")
        with self.assertRaises(EOFError):
            read_packet(stream)

    def test_truncated_and_oversized(self):
        for data in [b"\x00", struct.pack("!I", 9999999), struct.pack("!I", 2) + b"{", struct.pack("!I", 2) + b"[]"]:
            with self.assertRaises((ValueError, EOFError)):
                read_packet(io.BytesIO(data))
        for header in [b'{"bytes":-1}', b'{"bytes":true}', b'{"bytes":999999999}']:
            with self.assertRaises(ValueError):
                read_packet(io.BytesIO(struct.pack("!I", len(header)) + header))

    def test_letterboxing_and_fractional_coordinates(self):
        point = normalized_point(500, 500, 1000, 1000, 3440, 1440)
        self.assertAlmostEqual(point[0], 0.5)
        self.assertAlmostEqual(point[1], 0.5)
        self.assertIsNone(normalized_point(500, 10, 1000, 1000, 3440, 1440))
        self.assertEqual(normalized_point(500, 10, 1000, 1000, 3440, 1440, True), (0.5, 0))
        self.assertIsNone(normalized_point(1, 1, 0, 0, 1920, 1080))
        self.assertEqual(normalized_point(100, 50, 200, 100, 2000, 1000), (0.5, 0.5))

    def test_host_cannot_inject_shell_or_ssh_options(self):
        for host in ["-oProxyCommand=bad", "a;id", "a$(id)", "a`id`", "host\ncommand", "a b", ""]:
            with self.assertRaises(ValueError):
                validate_host(host)
        for host in ["desktop.home", "user@desktop", "192.0.2.10", "[::1]"]:
            if host.startswith("["):
                continue  # IPv6 can use an SSH alias.
            self.assertEqual(ssh_command(host, "low-bandwidth")[-2], host)
        with self.assertRaises(ValueError):
            ssh_command("desktop", "low-bandwidth; id")

    def test_invalid_coordinates(self):
        for value in [math.nan, math.inf, -0.1, 1.1, True, "0.5"]:
            with self.assertRaises(ValueError):
                unit(value)

    def test_fullscreen_fill_maps_entire_wide_screen(self):
        for x, y, expected in [(0, 0, (0, 0)), (800, 450, (0.5, 0.5)),
                               (1600, 900, (1, 1)), (160, 810, (0.1, 0.9))]:
            self.assertEqual(normalized_point(x, y, 1600, 900, 3440, 1440, fill=True), expected)
        self.assertIsNone(normalized_point(-1, 50, 1600, 900, 3440, 1440, fill=True))
        self.assertEqual(normalized_point(-1, 450, 1600, 900, 3440, 1440, clamp=True, fill=True), (0, 0.5))


if __name__ == "__main__":
    unittest.main()
