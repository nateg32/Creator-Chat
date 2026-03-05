import pytest
from utils.name_formatter import normalize_creator_name

@pytest.mark.parametrize("raw,expected", [
    (" alex   hormozi ", "Alex Hormozi"),
    ("jean-pierre dupont", "Jean-Pierre Dupont"),
    ("o'connor", "O'Connor"),
    ("ludwig van beethoven", "Ludwig van Beethoven"),
    ("john smith jr", "John Smith Jr."),
    ("John Smith III", "John Smith III"),
    ("MrBeast", "MrBeast"),
    ("MKBHD", "MKBHD"),
    ("madonna", "Madonna"),
    ("van helsing", "Van Helsing"),
])
def test_normalization(raw, expected):
    r = normalize_creator_name(raw)
    assert r.is_valid is True
    assert r.normalized == expected

def test_single_lowercase_acronym_suggested():
    r = normalize_creator_name("mkbhd")
    assert r.is_valid is True
    assert r.normalized == "Mkbhd"
    assert r.suggested == "MKBHD"
    assert r.flags.get("likely_acronym") is True

@pytest.mark.parametrize("raw", ["", "   ", "1", "<script>", "a"*81])
def test_invalid(raw):
    r = normalize_creator_name(raw)
    assert r.is_valid is False
    assert r.normalized is None
    assert r.error is not None
