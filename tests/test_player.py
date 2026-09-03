import subprocess
import tempfile
import threading
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.player import PlaybackError, WindowsAudioPlayer


class PlayerTests(TestCase):
    def test_speed_uses_pitch_preserved_audio_without_mci_pitch_shift(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio_path = root / "episode.mp3"
            audio_path.write_bytes(b"test")
            player = WindowsAudioPlayer(cache_dir=root / "cache")
            player.set_speed(1.5)
            player.set_volume(65)

            def mci_response(command: str, **_kwargs) -> str:
                return "60000" if command.endswith("status daily_nexus_episode length") else ""

            with (
                patch.object(
                    player,
                    "_prepare_playback_path",
                    return_value=audio_path,
                ),
                patch.object(player, "_mci", side_effect=mci_response) as mci,
            ):
                player.play(audio_path)

            commands = [call.args[0] for call in mci.call_args_list]
            self.assertNotIn("set daily_nexus_episode speed 1500", commands)
            self.assertIn("setaudio daily_nexus_episode volume to 650", commands)
            self.assertEqual("play daily_nexus_episode", commands[-1])
            self.assertEqual(90_000, player.length_ms())

    def test_ffmpeg_atempo_cache_is_used_for_pitch_preservation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio_path = root / "episode.mp3"
            audio_path.write_bytes(b"source")
            player = WindowsAudioPlayer(cache_dir=root / "cache")

            def complete(command, **_kwargs):
                Path(command[-1]).write_bytes(b"x" * 1_100)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("audiodigest.player.shutil.which", return_value="ffmpeg.exe"),
                patch("audiodigest.player.subprocess.run", side_effect=complete) as run,
            ):
                prepared = player._prepare_playback_path(audio_path, 1.5)

            command = run.call_args.args[0]
            self.assertTrue(prepared.is_file())
            self.assertIn("atempo=1.500", command)
            self.assertNotEqual(audio_path, prepared)

    def test_seek_uses_logical_episode_time_at_changed_tempo(self):
        player = WindowsAudioPlayer()
        player._opened = True
        player._active_speed = 1.5
        player._logical_length_ms = 90_000
        with patch.object(player, "_mci", return_value="playing") as mci:
            player.seek_ms(45_000)
        commands = [call.args[0] for call in mci.call_args_list]
        self.assertIn("seek daily_nexus_episode to 30000", commands)
        self.assertEqual("play daily_nexus_episode", commands[-1])

    def test_worker_preparation_does_not_touch_mci(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio_path = root / "episode.mp3"
            audio_path.write_bytes(b"test")
            player = WindowsAudioPlayer(cache_dir=root / "cache")
            prepared_values = []

            with (
                patch.object(
                    player,
                    "_prepare_playback_path",
                    return_value=audio_path,
                ),
                patch.object(player, "_mci") as mci,
            ):
                worker = threading.Thread(
                    target=lambda: prepared_values.append(
                        player.prepare(audio_path, 1.25)
                    )
                )
                worker.start()
                worker.join()
                self.assertFalse(mci.called)

                def response(command: str, **_kwargs) -> str:
                    return "60000" if command.endswith(" length") else ""

                mci.side_effect = response
                player.play_prepared(prepared_values[0])

            commands = [call.args[0] for call in mci.call_args_list]
            self.assertTrue(commands[0].startswith('open "'))
            self.assertEqual("play daily_nexus_episode", commands[-1])

    def test_speed_and_volume_ranges_are_guarded(self):
        player = WindowsAudioPlayer()
        with self.assertRaises(PlaybackError):
            player.set_speed(2.5)
        with self.assertRaises(PlaybackError):
            player.set_volume(101)
