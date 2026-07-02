# Refs: daemon-skew-self-heal

- Linear `DIS-28`: Detect and explain Dispatch daemon/client version skew after upgrades.
- `src/outfitter/dispatch/surfaces/cli.py`: CLI control-socket invocation and daemon lifecycle commands.
- `src/outfitter/dispatch/daemon/control.py`: control socket dispatch and method-not-found errors.
- `src/outfitter/dispatch/daemon/lifecycle.py`: detached daemon start/stop helpers.
- `src/outfitter/dispatch/core/ops.py`: current CLI/daemon op registry.
- `src/outfitter/dispatch/core/models.py`: status output contracts.
- `src/outfitter/dispatch/core/handlers.py`: status handler and lane counts.
- `tests/daemon/test_control.py`: control socket behavior tests.
- `tests/daemon/test_lifecycle.py`: lifecycle tests.
- `tests/surfaces/test_derive_cli.py`: CLI route and command behavior tests.
- `tests/test_doctor.py`: lifecycle command tests.
- `docs/adrs/0008-control-socket-protocol.md`: control protocol version/capability intent.
- `docs/adrs/0009-mcp-daemon-lifecycle.md`: daemon singleton lifecycle.
- `docs/usage/README.md`: operator lifecycle docs.
- `skills/dispatch/SKILL.md`: first-party agent guidance.
