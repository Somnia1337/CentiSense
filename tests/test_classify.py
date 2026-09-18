import unittest

import main


class TestClassifyEval(unittest.TestCase):
    def test_category_boundaries(self):
        cases = [
            (300, "white_winning"),
            (10_000, "white_winning"),
            (299, "white_big"),
            (150, "white_big"),
            (149, "white_slight"),
            (50, "white_slight"),
            (49, "equal"),
            (0, "equal"),
            (-49, "equal"),
            (-50, "black_slight"),
            (-149, "black_slight"),
            (-150, "black_big"),
            (-299, "black_big"),
            (-300, "black_winning"),
            (-10_000, "black_winning"),
        ]
        for cp, expected in cases:
            with self.subTest(cp=cp):
                self.assertEqual(main.classify_eval(cp), expected)

    def test_evaluation_category_mates_are_decisive(self):
        self.assertEqual(main.evaluation_category(0, 1), "white_winning")
        self.assertEqual(main.evaluation_category(0, -1), "black_winning")
        # Mate sign wins over the raw centipawn value.
        self.assertEqual(main.evaluation_category(-50, 1), "white_winning")
        self.assertEqual(main.evaluation_category(50, -1), "black_winning")

    def test_evaluation_category_without_mate(self):
        self.assertEqual(main.evaluation_category(42, None), "equal")
        self.assertEqual(main.evaluation_category(250, None), "white_big")


class TestCategoriesShape(unittest.TestCase):
    def test_keys_are_ordered_and_unique(self):
        keys = [cat[0] for cat in main.CATEGORIES]
        self.assertEqual(
            keys,
            [
                "white_winning",
                "white_big",
                "white_slight",
                "equal",
                "black_slight",
                "black_big",
                "black_winning",
            ],
        )
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_key_has_matching_classification(self):
        for cat in main.CATEGORIES:
            key, _label, _desc, lo, hi = cat
            self.assertEqual(main.classify_eval(lo), key)
            self.assertEqual(main.classify_eval(hi), key)


if __name__ == "__main__":
    unittest.main()
