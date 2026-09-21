import shutil
import subprocess
import sys
import time
import unittest

import chess

import main


class TestReadLineTimeout(unittest.TestCase):
    def test_read_line_does_not_wait_full_timeout(self):
        """Regression: buffered read-ahead must not make _read_line block for
        the full timeout when further lines are already available."""
        eng = main.StockfishEngine()
        child = (
            "import sys, time; "
            "sys.stdout.write('line1\\nline2\\nline3\\n'); "
            "sys.stdout.flush(); time.sleep(5)"
        )
        eng.process = subprocess.Popen(
            [sys.executable, "-c", child],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        eng._read_buf = b""
        try:
            start = time.monotonic()
            lines = []
            for _ in range(3):
                line = eng._read_line(timeout=2.0)
                if line is None:
                    break
                lines.append(line)
            elapsed = time.monotonic() - start
            self.assertEqual(lines, ["line1", "line2", "line3"])
            self.assertLess(elapsed, 1.0)
        finally:
            eng.process.kill()
            eng.process.wait()


@unittest.skipUnless(shutil.which("stockfish"), "stockfish is not installed")
class TestEngineIntegration(unittest.TestCase):
    def test_start_and_evaluate_return_quickly(self):
        """The engine should start and evaluate well within the timeouts,
        rather than waiting out each timeout in full."""
        eng = main.StockfishEngine()
        try:
            start = time.monotonic()
            eng.start()
            start_elapsed = time.monotonic() - start
            self.assertIsNotNone(eng.process)
            self.assertIsNone(eng.process.poll())
            # UCI handshake is near-instant; it must not take ~5s per keyword.
            self.assertLess(start_elapsed, 3.0)

            eval_start = time.monotonic()
            result = eng.evaluate(chess.STARTING_FEN, depth=6, timeout=5.0)
            eval_elapsed = time.monotonic() - eval_start
            self.assertNotIn("error", result)
            self.assertIsNotNone(result["bestmove"])
            self.assertLess(eval_elapsed, 5.0)
        finally:
            eng.stop()


if __name__ == "__main__":
    unittest.main()
