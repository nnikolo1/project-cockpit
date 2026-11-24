import pytest

from dispatcher.note import validate

OK = {"outcome": "done", "summary": "did it"}


def test_minimal_note_is_usable():
    assert validate(OK) == []


def test_all_fields_usable():
    note = {**OK, "any_assumptions": "a", "any_limitations_or_caveats": "b", "any_open_questions": "c", "tokens": 0}
    assert validate(note) == []


@pytest.mark.parametrize("note", ["x", [], None, 3])
def test_not_an_object(note):
    assert validate(note)


@pytest.mark.parametrize("note", [
    {"summary": "s"}, {"outcome": "maybe", "summary": "s"}, {"outcome": "done"},
    {"outcome": "done", "summary": ""}, {"outcome": "done", "summary": "  "}, {"outcome": "done", "summary": 3},
])
def test_mandatory_fields(note):
    assert validate(note)


@pytest.mark.parametrize("field", ["summary", "any_assumptions", "any_limitations_or_caveats", "any_open_questions"])
def test_size_limits_at_the_boundary(field):
    words = {**OK, field: " ".join(["a"] * 100)}
    assert validate(words) == []
    assert validate({**OK, field: " ".join(["a"] * 101)})
    assert validate({**OK, field: "a" * 400}) == []
    assert validate({**OK, field: "a" * 401})


def test_optional_text_must_be_text():
    assert validate({**OK, "any_assumptions": 5})


@pytest.mark.parametrize("tokens", [True, False, -1, 1.5, "3", {"in": 1}])
def test_tokens_must_be_a_whole_number(tokens):
    assert validate({**OK, "tokens": tokens})


def test_extra_field_is_not_an_error():
    assert validate({**OK, "branch": "b", "other": 1}) == []
