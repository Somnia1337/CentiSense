import unittest

import main


class TestParseStockfishLine(unittest.TestCase):
    def _state(self):
        return {"cp": 0, "mate": None, "bestmove": None, "pv": []}

    def test_bestmove_terminates_search(self):
        state = self._state()
        self.assertTrue(main.parse_stockfish_line("bestmove e2e4 ponder e7e5", state))
        self.assertEqual(state["bestmove"], "e2e4")

    def test_score_cp_and_pv(self):
        state = self._state()
        done = main.parse_stockfish_line(
            "info depth 20 seldepth 25 multipv 1 score cp 42 nodes 1000 "
            "nps 50000 hashfull 10 tbhits 0 time 20 pv e2e4 e7e5 g1f3",
            state,
        )
        self.assertFalse(done)
        self.assertEqual(state["cp"], 42)
        self.assertEqual(state["pv"], ["e2e4", "e7e5", "g1f3"])

    def test_score_mate(self):
        state = self._state()
        main.parse_stockfish_line("info depth 20 score mate 3 pv d1h5", state)
        self.assertEqual(state["mate"], 3)

    def test_negative_score(self):
        state = self._state()
        main.parse_stockfish_line("info depth 20 score cp -130 pv g8f6", state)
        self.assertEqual(state["cp"], -130)

    def test_lower_bound_score(self):
        state = self._state()
        # Stockfish can report lower/upper bounds; the regex still extracts cp.
        main.parse_stockfish_line("info depth 20 score cp 5 lowerbound", state)
        self.assertEqual(state["cp"], 5)

    def test_last_pv_wins(self):
        state = self._state()
        main.parse_stockfish_line("info depth 18 score cp 30 pv d2d4 d7d5", state)
        main.parse_stockfish_line("info depth 19 score cp 30 pv d2d4 d7d5 c2c4", state)
        self.assertEqual(state["pv"], ["d2d4", "d7d5", "c2c4"])


if __name__ == "__main__":
    unittest.main()
