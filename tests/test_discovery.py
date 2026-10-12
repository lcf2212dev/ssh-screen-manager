import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ssh_screen_manager.discovery import Device, discover, effective_target, probe, ssh_aliases


class DiscoveryTests(unittest.TestCase):
    def test_config_includes_aliases_without_wildcards_or_recursion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, included = root / "config", root / "peers"
            config.write_text(f'Include "{included}"\nHost notebook notebook.home *.example !excluded\n')
            included.write_text(f'Include {config}\nHost=desktop\nHost = lab\n')
            self.assertEqual(ssh_aliases(config), ["desktop", "lab", "notebook", "notebook.home"])

    @patch("ssh_screen_manager.discovery.command_output")
    def test_public_git_hosts_are_not_automatically_probed(self, output):
        output.side_effect = ["hostname github.com\nuser git\nport 22\n", "140.82.114.3 STREAM github.com"]
        self.assertIsNone(effective_target("github.com"))
        output.side_effect = ["hostname desktop.home\nuser ada\nport 2222\n", "192.0.2.50 STREAM desktop.home"]
        self.assertEqual(effective_target("desktop"), ("desktop.home", "ada", "2222"))

    @patch("ssh_screen_manager.discovery.subprocess.run")
    def test_readiness_never_starts_screen_capture_and_preserves_strict_ssh(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(dict(
            app="ssh-screen-manager", protocol=1, hostname="notebook", ready=True, screens=1, identity="remote:1000")), "")
        device = probe("notebook")
        self.assertTrue(device.ready)
        self.assertEqual(device.detail, "Available · 1 screen")
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-1], "exec ~/.local/bin/ssh-screen-manager --check")
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(dict(
            app="ssh-screen-manager", protocol=1, ready=False, screens=1, reason="Unlock the computer")), "")
        self.assertFalse(probe("notebook").ready)
        run.return_value = subprocess.CompletedProcess([], 0, "not an SSH Screen Manager response", "")
        self.assertFalse(probe("notebook").ready)

    def test_aliases_are_collapsed_self_is_hidden_and_saved_offline_device_is_retained(self):
        targets = {"notebook": ("notebook.home", "me", "22"),
                   "notebook.home": ("notebook.home", "me", "22"),
                   "192.0.2.5": ("192.0.2.5", "me", "22"),
                   "offline": ("offline.home", "me", "22"),
                   "self": ("192.0.2.4", "me", "22")}
        def check(host):
            return Device(host, host, host != "offline", identity="local" if host == "self" else
                          "laptop" if host != "offline" else "")
        with patch("ssh_screen_manager.discovery.ssh_aliases", return_value=["notebook.home", "self"]), \
             patch("ssh_screen_manager.discovery.known_hosts", return_value=["192.0.2.5"]), \
             patch("ssh_screen_manager.discovery.advertised_hosts", return_value=[]), \
             patch("ssh_screen_manager.discovery.effective_target", side_effect=lambda h, explicit: targets[h]), \
             patch("ssh_screen_manager.discovery.local_identity", return_value="local"), \
             patch("ssh_screen_manager.discovery.probe", side_effect=check) as probes:
            devices = discover(["notebook", "offline"])
        self.assertEqual([d.host for d in devices], ["notebook", "offline"])
        self.assertEqual(probes.call_count, 4)


if __name__ == "__main__":
    unittest.main()
