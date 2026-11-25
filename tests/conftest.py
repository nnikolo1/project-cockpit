def pytest_configure(config):
    config.addinivalue_line("markers", "ui_action(*ids): the UI actions a test exercises (server:x, page:x, form:x; /refused: its refusal); "
                                       "tools/ui_actions.py reads them, tests/test_ui_actions.py fails for an action no test names")
