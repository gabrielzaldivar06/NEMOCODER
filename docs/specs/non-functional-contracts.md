# Non-Functional Contracts

This spec defines executable non-functional contracts for Mission Control and headless execution.

## Performance SLO Contracts

- Provider timeout bounds are mandatory and validated by contract tests.
- `subprocess` provider timeout bounds: 5s to 1800s.
- `fake` provider timeout bounds: 1s to 300s.

## Secret-Handling Contracts

- LM Studio/API secret values must never be leaked in user-facing error messages.
- Mission Control must redact known API key values before surfacing network error details.
- Contract tests must prove that a failing LM Studio response cannot echo `LMSTUDIO_API_KEY`.
