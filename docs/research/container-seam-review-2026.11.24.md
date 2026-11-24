# The container seam: review of the architecture for replacing the containerisation solution

Status: research report, persisted as received (read-only run by a background agent, 2026-11-24; written into the docs afterwards because the later analysis of agent and OS dependence could not find it)
Date: 2026-11-24
Modules: taskqueue

Owner decisions on this report (2026-11-24): the spec texts 4c, 4e, 4f and Approach 1 were applied tool-neutral; the code change (alive, kill, relay start and the data folder into the runtime adapter) was made; the proxy sits in the Dispatcher sub-module and the relay in the Worker sub-module. Open: the remaining seam fixes are in `docs/memos/2026.11.24-refactoring-for-agent-and-os-independence.md`.

**Short answer.** The code already has most of the seam: almost every call to Apple's `container` tool goes through `dispatcher/runtime.py` (`Runtime.call()` with an injectable `run`). Three things leaked out of it (they were moved in by the change above). The spec was worse: 4c, 4e and 4f wrote Apple's tool into the requirements, and the only evidence for the design came from one unsupported OS version.

## 1. Where it is tied to macOS, and to macOS before 26

**Facts established on macOS 15.3.1.** Source: `docs/research/feasibility-container-networking-2026.11.23.md` on the branch `feasibility-networking`.
- Apple's tool 1.7.1 is supported only on macOS 26. On 15 it "can run", and upstream does not fix issues it cannot reproduce on 26. Homebrew refuses to install it on 15 (`depends_on macos: :tahoe`), so it was installed from the signed `.pkg` with the admin password.
- The upstream doc says that on macOS 15 "the `container network` commands are not available" and "`--network` ... will result in an error". Even so, the test run with `--network none` on 1.7.1 / 15.3.1 worked: loopback only, 1.1.1.1 unreachable. **So the egress guarantee (4c, 4s, Approach 1) rests on an observation that contradicts the macOS 15 documentation, and it has not been checked on 26.**
- Getting a host Unix socket in through `-v` (relayed over vsock, 4f) was verified from source (RuntimeService.swift, 1.7.1) and observed working on 15.3.1.
- That killing the host-side `container exec` client ends the guest process (relied on by 3i and 3j) was observed only on 15.3.1.
- The first run hung: every `exec`, `stats`, `kill` and `stop` timed out. That hang is why 3p requires a time limit on every call. It happened on the unsupported OS.
- Bind mounts appear root-owned in the guest. That is why `prepare_task` sets `chmod 0777` on the outbox.

**Code that assumed Apple's tool.**
- `runtime.py`: `run_args()` (Apple flags, socket passed as `-v`); `state()` parses Apple's `list --format json` shape (`configuration.id`); `stats()` and `build_image()` use Apple's syntax; `launch()` returns the host `Popen` of `container exec`.
- Leak 1 (core.py): `ensure_relay()` built the Apple `exec -d` command line inside core instead of inside runtime.
- Leak 2 (core.py): `pid_alive` and `os.kill(pid, SIGKILL)` on the host PID of the exec client. This only works because Apple's tool ends the guest process when that client is killed.
- Leak 3 (core.py): the default `--container-data ~/Library/Application Support/com.apple.container`.
- `image/Containerfile` comment: "Claude Code binary for Alpine arm64" and `container build`.

**macOS ties that are not about containers** (named only; no change proposed): launchd every 2 minutes (3c); LaunchAgent plus `caffeinate -i` (4d); `sysctl kern.memorystatus_vm_pressure_level` and `pmset` (for 3g); the 104-byte socket path limit (4f); "on the owner's MacBook" (5c). None of these depends on macOS 15 versus 26.

## 2. The seam

**Behind the seam** (one runtime class; `runtime.Runtime` is the current Apple adapter). Each replacement has to provide:
1. **`ensure_container()`**: one long-lived container from the image, with an init process, a fixed memory and CPU limit, the worker area at `/area`, and **no way out except the proxy** (3k, 4b, 4c, 4s).
2. **The proxy channel**: make the host proxy reachable at `127.0.0.1:<port>` inside the container, plus `ensure_relay()` (3k, 4f). How this is done differs most between runtimes, so it must sit behind the seam.
3. **`exec(user, cmd, env, workdir, timeout)`** as an unprivileged `workerN` (4e, 4j). The egress probe (4s), the clone (4k, 4l) and the transcript time (3o) are built on it.
4. **`launch(user, task_id)`, `alive(handle)` and `kill(handle)`** (3e, 3i, 3j, 4e). The kill must really end the guest process.
5. **`stats()` and the data-folder size** for the heartbeat, or "not reported" (3g).
6. **`build_image(context)`** (4m).
7. **A time limit on every call**, raising `ContainerTimeout` (3p).

**Stays in the Dispatcher:** choosing tasks (3a), settings (6a, 6b) and the state file; the start, runaway and activity decisions (3i, 3j, 3o); the outbox files and the note check (4n, 4o, 5b); `git bundle` in and out (3d, 3e) and the GitHub labels; the proxy and usage counting (4g, 4h) and the processing log (5); the host readings (3g), the disk check (3n) and launchd (3c). `proxy.py`, `plog.py` and `tasks.py` contain nothing specific to the runtime (checked).

## 3. The seam checked against other runtimes

**Apple `container` on macOS 26.**
- Verified: same CLI; `container network create [--internal]` exists only on macOS 26; `container machine` exists; `stats --format json --no-stream` and `list --format json` exist.
- Not documented on any version: `--network none` as a value (the command reference gives only `<name>[,mac=...]`), and which direction `--publish-socket` works in.
- Inference: the adapter carries over unchanged, but the 15.3.1 checks (Approach 1) have to be repeated. If `--network none` were refused, `--internal` would be the alternative on 26, and the doc does not say what it blocks.

**Colima (Docker engine inside a Lima VM).**
- Verified: Docker has `--network none`, `--init`, `--memory`, `--cpus` and `exec --user`. Lima's template documents `portForwards` with `hostSocket`/`guestSocket` and `reverse` ("can only be used for unix sockets"). Bind-mounting a host socket over virtiofs fails ("Operation not supported", Colima #997). Killing the `docker exec` client does not stop the process it started (moby #9098).
- Inference: the proxy channel would be a Lima reverse socket forward into the VM, bind-mounted from the VM path into the container, with the same relay. The kill would have to run inside the guest (for example `exec pkill -u workerN`). `list` would be parsed differently (`docker ps -a --format json` gives one object per line). The data folder would be `~/.colima`.

**Docker Desktop.** Mounting a host Unix socket is not reliably supported: the evidence is indirect (osxfs docs: "no transmission across the hypervisor"; forum errors), with no official statement. Inference: the proxy channel would need another route, for example a TCP proxy on an internal Docker network, which would replace `--network none`. This confirms that item 2 belongs behind the seam.

## 4. Where the specification was tied to one solution

- **4c**: named Apple's tool and its flags. The only sub-point tied entirely to one solution.
- **4e**: the `container exec --user workerN` syntax.
- **4f**: "the runtime relays it over vsock" (Apple's mechanism; the 104-byte limit is a macOS limit).
- **3k**: "starts the relay inside it": generic enough as long as 4f says how the socket gets in.
- **Approach 1**: the evidence is "on macOS 15.3.1" and did not name the tool version or say that this OS is unsupported.
- macOS rather than the container tool: 3c (launchd), 4d (LaunchAgent, `caffeinate`), 5c (MacBook).

Everything else (3g, 3p, 4a, 4b, 4l, 4s) was already worded neutrally.

## 5. Gaps noted

- The PID of a `Popen` started in an earlier firing is checked with `os.kill(pid, 0)`, which risks PID reuse.
- `chmod 0777` on the outbox is an Apple-specific workaround. It is harmless elsewhere.

## Sources

- [apple/container technical-overview (macOS 15 limitations, one VM per container)](https://github.com/apple/container/blob/main/docs/technical-overview.md)
- [apple/container command-reference](https://github.com/apple/container/blob/main/docs/command-reference.md)
- [apple/container networking (isolated networks on macOS 26)](https://github.com/apple/container/blob/main/docs/networking.md)
- [apple/container host-integration (`--ssh` is a socket volume)](https://github.com/apple/container/blob/main/docs/host-integration.md)
- [Lima default template (portForwards, hostSocket, reverse)](https://raw.githubusercontent.com/lima-vm/lima/master/templates/default.yaml)
- [Lima port forwarding docs](https://lima-vm.io/docs/config/port/)
- [Colima #997 (socket bind mount under virtiofs)](https://github.com/abiosoft/colima/issues/997)
- [Docker for Mac osxfs docs (sockets not across the hypervisor)](https://github.com/docker/docker.github.io/blob/a17a06c6a15028011250900c49a1b59620cf1c65/docker-for-mac/osxfs.md)
- [Docker forum: socket mount "operation not supported"](https://forums.docker.com/t/error-while-creating-mount-source-path-listeners-operation-not-supported/122632)
- [moby #9098 (killing the docker exec client leaves the process running)](https://www.github.com/moby/moby/issues/9098)
