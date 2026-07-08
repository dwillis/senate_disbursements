import json
from pathlib import Path

import pytest

from senate_parser.extract import Word

FIXTURES = Path(__file__).parent / "fixtures"


def load_words(name: str) -> list[Word]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [Word(**d) for d in data]


@pytest.fixture
def page_1000_words():
    return load_words("vol1_page_1000")


@pytest.fixture
def page_1001_words():
    return load_words("vol1_page_1001")


@pytest.fixture
def page_1004_words():
    return load_words("vol1_page_1004")


@pytest.fixture
def page_125_words():
    return load_words("vol1_page_125")
