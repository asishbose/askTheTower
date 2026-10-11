# Contributing

Thanks for looking. This is a hackathon entry, so the bar for a change is "keeps the seven rules and the tests".

- **Set up:** `uv sync --all-packages && uv run pre-commit install`. Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker for the integration and end-to-end layers.
- **Before a PR:** run `make lint typecheck test-unit test-integration`. If you touch anything a demo transcript depends on, also run `make up && make test-e2e`. Every test carries exactly one of the `unit` / `integration` / `e2e` / `nightly` markers.
- **The rules** in [`docs/architecture/README.md`](docs/architecture/README.md#design-rules-restated-from-the-deck-and-review-responsemd) are not up for "improvement". There is no model in the request path. Policy is code. Results are booleans and timestamps. Consent is per line and per tool. If a shortcut breaks a rule, the shortcut is wrong.
- **Docs stay true:** if your code deviates from `docs/architecture/`, change the doc in the same PR and say so in the PR body.
- **Never commit** a phone number, a key or a token. `.env` files are gitignored, and `.env.example` holds placeholders only. Run `make privacy-grep` and `make secrets-check` before you push.
- **Licence:** contributions are accepted under [Apache-2.0](LICENSE).
