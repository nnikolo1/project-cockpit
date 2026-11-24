# Failure-path test: the stub image

`stub_claude.py` replaces the `claude` program in a derived image, so that each failure case of the Dispatcher is reproducible
(driver: `tools/e2e_failure_paths.py`). Build the normal image first (`dispatcher/image/Containerfile`), then, from the repository root:

    container build --dns 1.1.1.1 -t cockpit-worker:stub -f tools/e2e/Containerfile.stub tools/e2e

The stub needs `python3` on the image's path (the base image has it). Run the Dispatcher with `--image cockpit-worker:stub`.
