"""Tests für die Einteilung in Bauabschnitte."""

from lego_mosaik.domain.sections import section_index, split_sections


def test_split_sections_row_major_with_short_edges() -> None:
    sections = split_sections(width=40, height=20, size=16)
    assert [(s.number, s.row, s.col, s.rows, s.cols) for s in sections] == [
        (1, 0, 0, 16, 16),
        (2, 0, 16, 16, 16),
        (3, 0, 32, 16, 8),
        (4, 16, 0, 4, 16),
        (5, 16, 16, 4, 16),
        (6, 16, 32, 4, 8),
    ]


def test_section_index_matches_numbering() -> None:
    sections = split_sections(40, 20, 16)
    for section in sections:
        assert section_index(section.row, section.col, 40, 16) == section.number - 1
    assert sections[2].contains(5, 39) and not sections[2].contains(16, 39)
