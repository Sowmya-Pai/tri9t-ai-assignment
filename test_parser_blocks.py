import unittest

from parser import HeadingDetector


class HeadingDetectorBlockCompatibilityTest(unittest.TestCase):
    def test_detect_from_blocks_accepts_pymupdf_tuples(self):
        detector = HeadingDetector()
        blocks = [
            (72.0, 60.0, 182.0, 75.0, "Section One\n", 0, 0),
            (72.0, 90.0, 182.0, 120.0, "Body text\n", 0, 0),
        ]

        headings = detector.detect_from_blocks(blocks)

        self.assertEqual(len(headings), 1)
        self.assertEqual(headings[0].text, "Section One")
        self.assertEqual(headings[0].level, 1)


if __name__ == "__main__":
    unittest.main()
