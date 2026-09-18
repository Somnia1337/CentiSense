import unittest

import chess

import main


class TestPositionDatabase(unittest.TestCase):
    def test_cached_positions_are_valid(self):
        path = main.POSITIONS_FILE
        if not path.exists():
            self.skipTest(f"{path.name} not found")
        lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        self.assertGreater(len(lines), 0)
        for fen in lines:
            with self.subTest(fen=fen[:40]):
                board = chess.Board(fen)
                self.assertFalse(board.is_game_over())
                piece_count = bin(board.occupied).count("1")
                self.assertGreaterEqual(piece_count, main.MIN_PIECES)


if __name__ == "__main__":
    unittest.main()
