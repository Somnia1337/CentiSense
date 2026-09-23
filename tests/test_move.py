import unittest

import chess
from fastapi import HTTPException

import main


class TestApplyMove(unittest.TestCase):
    def test_legal_move_alternates_turn(self):
        result = main.apply_move(chess.STARTING_FEN, "e2e4")
        self.assertEqual(result["side_to_move"], "black")
        self.assertEqual(result["san"], "e4")
        self.assertIn("e7e5", result["legal_moves"])

    def test_illegal_move_raises(self):
        with self.assertRaises(HTTPException) as ctx:
            main.apply_move(chess.STARTING_FEN, "e2e5")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_wrong_side_to_move_raises(self):
        # White to move; a black pawn move is not among white's legal moves.
        with self.assertRaises(HTTPException) as ctx:
            main.apply_move(chess.STARTING_FEN, "e7e5")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_move_notation_raises(self):
        with self.assertRaises(HTTPException):
            main.apply_move(chess.STARTING_FEN, "x9x9")

    def test_invalid_fen_raises(self):
        with self.assertRaises(HTTPException):
            main.apply_move("not a real fen", "e2e4")

    def test_castling_is_legal(self):
        fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
        result = main.apply_move(fen, "e1g1")
        self.assertEqual(result["side_to_move"], "black")
        self.assertEqual(result["san"], "O-O")

    def test_promotion_requires_promotion_piece(self):
        fen = "8/P7/8/8/8/8/8/k6K w - - 0 1"
        with self.assertRaises(HTTPException):
            main.apply_move(fen, "a7a8")
        result = main.apply_move(fen, "a7a8q")
        self.assertEqual(result["side_to_move"], "black")

    def test_move_does_not_flip_when_orientation_fixed(self):
        # White to move; after e2e4 black is to move. With a fixed (non-flipped)
        # orientation the board must stay white-at-the-bottom instead of flipping.
        result = main.apply_move(chess.STARTING_FEN, "e2e4", flipped=False)
        _, fixed_svg = main.render_board(result["fen"], flipped=False)
        self.assertEqual(result["svg"], fixed_svg)
        # Sanity: the default rendering of the new position flips for black.
        _, auto_svg = main.render_board(result["fen"])
        self.assertNotEqual(result["svg"], auto_svg)


class TestRenderBoard(unittest.TestCase):
    def test_svg_contains_square_labels(self):
        board, svg = main.render_board(chess.STARTING_FEN)
        self.assertEqual(board.fen(), chess.STARTING_FEN)
        self.assertIn('class="square', svg)
        self.assertIn("a1", svg)
        self.assertIn("h8", svg)


class TestBuildPvStates(unittest.TestCase):
    def test_replays_san_moves(self):
        states = main.build_pv_states(chess.STARTING_FEN, ["e4", "e5", "Nf3", "Nc6"])
        self.assertEqual(len(states), 4)
        self.assertEqual(states[0]["side_to_move"], "black")
        self.assertEqual(states[1]["side_to_move"], "white")
        self.assertEqual(states[2]["side_to_move"], "black")
        self.assertEqual(states[3]["side_to_move"], "white")
        self.assertIn('class="square', states[0]["svg"])
        board = chess.Board(states[2]["fen"])
        self.assertEqual(
            board.fen().split(" ")[0],
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R",
        )

    def test_stops_on_invalid_move(self):
        states = main.build_pv_states(chess.STARTING_FEN, ["e4", "not-a-move"])
        self.assertEqual(len(states), 1)


if __name__ == "__main__":
    unittest.main()
