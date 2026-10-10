"""OCR line handling that does not need an OCR engine: unsure lines are read again, and the surer reading wins."""
import math

from testamur.extract import RETRY_BELOW, _assemble_lines, _reread_unsure, _straighten


def box(text: str, score: float) -> tuple:
    return (10.0, 20.0, 900.0, 60.0, text, score)


def test_an_unsure_line_is_read_again_and_the_surer_reading_kept():
    asked = []

    def upright(b):
        asked.append(b[4])
        return "Automated review systems should treat every clause as present.", 0.99
    out = _reread_unsure([box("Valid until: 13-Jun-2027", 0.98), box("Aut s d s u st s fr s ss es.", 0.65)], upright)
    assert asked == ["Aut s d s u st s fr s ss es."]                  # a confident line is not read twice
    assert out[0][4] == "Valid until: 13-Jun-2027"
    assert out[1][4].startswith("Automated review systems") and out[1][5] == 0.99


def test_a_second_reading_that_is_no_surer_changes_nothing():
    out = _reread_unsure([box("Rs. 12,34,5600O", RETRY_BELOW - 0.1)], lambda b: ("Rs. 12,34,56000", 0.5))
    assert out[0][4] == "Rs. 12,34,5600O"
    assert _reread_unsure([box("x", 0.1)], lambda b: None)[0][4] == "x"


def test_lines_of_a_skewed_scan_are_grouped_as_the_page_reads():
    """A page turned by two degrees: a 1,400 px line drifts 49 px from end to end, more than the 40 px between lines,
    so the next line's right-hand box sits inside the first line's vertical span unless the page is turned straight."""
    slope = math.tan(math.radians(-2))

    def poly(x0, x1, line_top, height=28.0):
        y0 = line_top + (x0 - 100) * slope            # where this line's top is at x0
        return [(x0, y0), (x1, y0 + (x1 - x0) * slope), (x1, y0 + (x1 - x0) * slope + height), (x0, y0 + height)]
    spec = [(100, 1500, 500.0, "This authority remains in force until the date stated above."),
            (100, 400, 540.0, "Date of issue:"), (1300, 1500, 540.0, "06 June 2026")]
    polys = [poly(x0, x1, top) for x0, x1, top, _ in spec]
    boxes = [(min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p), t, 0.99)
             for p, (_, _, _, t) in zip(polys, spec, strict=True)]
    straight = [line.text for line in _assemble_lines(_straighten(polys, boxes, 1600, 2200), 1)]
    assert straight == ["This authority remains in force until the date stated above.", "Date of issue: 06 June 2026"]
    merged = [line.text for line in _assemble_lines(boxes, 1)]
    assert merged != straight                      # without turning the page straight, the drift mixes the lines
