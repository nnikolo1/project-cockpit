"""The Worker's good-bye note (spec docs/specs/dispatcher-and-workers.md, 4o). Standard library only: the
Worker's Stop hook imports this module inside the container."""

OUTCOMES = ("done", "gave_up")
TEXT_FIELDS = ("any_assumptions", "any_limitations_or_caveats", "any_open_questions")
MAX_WORDS = 100
MAX_CHARS = 400


def _text_problem(name, value):
    if not isinstance(value, str):
        return f"{name} must be text"
    if len(value.split()) > MAX_WORDS:
        return f"{name} is longer than {MAX_WORDS} words"
    if len(value) > MAX_CHARS:
        return f"{name} is longer than {MAX_CHARS} characters"
    return None


def validate(note):
    """The problems found in a parsed good-bye note; an empty list means a usable note."""
    if not isinstance(note, dict):
        return ["the note must be a JSON object"]
    problems = []
    if note.get("outcome") not in OUTCOMES:
        problems.append('outcome must be "done" or "gave_up"')
    summary = note.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        problems.append("summary must be non-empty text")
    else:
        problems.append(_text_problem("summary", summary))
    for name in TEXT_FIELDS:
        if name in note:
            problems.append(_text_problem(name, note[name]))
    if "tokens" in note:
        tokens = note["tokens"]
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            problems.append("tokens must be a whole number, not negative")
    return [p for p in problems if p]
