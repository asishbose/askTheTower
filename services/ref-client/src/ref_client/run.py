"""The `ref-client` CLI.

    ref-client say "is my line ok" [--user asish]      one utterance through the Bedrock agent
    ref-client demo [--env local] [--agent auto]        the three moments + transplant story; writes transcripts
    ref-client corpus [--env local]                     the phrasing corpus → artifacts/corpus.md
    ref-client serve                                    the HTTP app behind the web chat page (09 §6; the image's CMD)

`--agent auto` (default) uses Bedrock when AWS credentials are configured and the scripted agent otherwise
(demo only; it says so on the first line). `--agent bedrock` never falls back: no Bedrock → exit 3 with the
reason. Exit codes: 0 ok · 1 demo mismatch / corpus below the gate · 2 Tower or the demo controls failed ·
3 Bedrock unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from ref_client import voice
from ref_client.agent import Agent, BedrockAgent, BedrockUnavailable, ScriptedAgent, has_aws_credentials
from ref_client.cognito import CognitoUnavailable, CognitoUsers
from ref_client.corpus_runner import run_corpus
from ref_client.demo import (
    AWS_STORIES,
    STORY_NAMES,
    Control,
    DemoError,
    Story,
    alerts_sent_list,
    alerts_settle_s,
    aws_control,
    http_control,
    run_demo,
)
from ref_client.mcp_client import TowerClient, TowerConfig, TowerError, user_id_for
from ref_client.transcript import redact

EXIT_OK, EXIT_FAIL, EXIT_TOWER, EXIT_BEDROCK = 0, 1, 2, 3
# testing-and-showcase.md §4: the stories are showcase steps 4–7.
SHOWCASE_STEP = {"moment-1": 4, "moment-2": 5, "moment-3": 6, "transplant": 7}
ARTIFACTS = Path(os.environ.get("REF_ARTIFACTS_DIR", "artifacts"))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ref-client", description="Reference client for Ask the Tower.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--env", default=os.environ.get("ENV", "local"), choices=["local", "eks", "aws"])
        sp.add_argument(
            "--agent", default=os.environ.get("REF_AGENT", "auto"), choices=["auto", "bedrock", "scripted"]
        )

    s = sub.add_parser("say", help="one utterance through the agent")
    s.add_argument("utterance")
    s.add_argument("--user", default="asish", help="asish | mom | a user id (local X-Tower-User)")
    common(s)

    d = sub.add_parser("demo", help="three moments + transplant story; writes artifacts/transcripts/*.json")
    d.add_argument("--story", action="append", choices=STORY_NAMES, help="run only these stories")
    d.add_argument("--out", type=Path, default=ARTIFACTS / "transcripts")
    d.add_argument("--no-write", action="store_true")
    d.add_argument(
        "--pause", action="store_true", help="wait for enter before each story (make showcase, §4 steps 4–7)"
    )
    common(d)

    c = sub.add_parser("corpus", help="tool-selection corpus → artifacts/corpus.md")
    c.add_argument("--out", type=Path, default=ARTIFACTS / "corpus.md")
    c.add_argument("--user", default="asish")
    common(c)

    sub.add_parser("serve", help="the HTTP app behind the web chat page (POST /invocations; env, not flags)")
    return p


def _err(msg: str) -> None:
    print(f"ref-client: {msg}", file=sys.stderr)


def _use_bedrock(choice: str) -> bool:
    return choice == "bedrock" or (choice == "auto" and has_aws_credentials())


async def _say(args: argparse.Namespace) -> int:
    if args.agent == "scripted":
        _err("`say` needs a model to choose the tool; the scripted agent only runs the demo script")
        return EXIT_BEDROCK
    if not _use_bedrock(args.agent):
        _err(
            "no AWS credentials found: `say` needs Bedrock. Configure AWS credentials (and BEDROCK_MODEL_ID, "
            "AWS_REGION), or run `ref-client demo --agent scripted`."
        )
        return EXIT_BEDROCK
    utterance = voice.listen() if args.utterance == "-" and voice.enabled() else args.utterance
    async with TowerClient(TowerConfig.from_env(user_id=user_id_for(args.user))) as tower:
        agent = BedrockAgent(tower)
        turn = await agent.ask(utterance)
    for c in turn.tool_calls:
        print(f"→ {c.name}({c.args})  {turn.reason_codes}")
    print(turn.spoken)
    voice.maybe_speak(turn.spoken)
    return EXIT_OK


async def _demo(args: argparse.Namespace) -> int:
    bedrock = _use_bedrock(args.agent)
    if not bedrock and args.agent == "scripted":
        # Chosen, not a fallback: ENV=aws runs scripted by default (mk/demo.mk; DEMO_AGENT=bedrock for the model).
        print(
            "ref-client demo: scripted agent (tool calls come from the demo script, `summary` is read verbatim; "
            "the model-driven run is the web chat page, or DEMO_AGENT=bedrock)."
        )
    elif not bedrock:
        print(
            "ref-client demo: scripted agent (no Bedrock: tool calls come from the demo script, `summary` is "
            "read verbatim). Set AWS credentials for the model-driven run."
        )
    # ENV=aws: Tower takes a Cognito access token per user (the JWT authorizer; the static bearer is local only)
    # and the admin calls go through the demo admin path with DEMO_ADMIN_BEARER and the users' subs.
    users = CognitoUsers.from_artifacts(ARTIFACTS) if args.env == "aws" else None
    async with AsyncExitStack() as stack:

        def tower_config(user_id: str) -> TowerConfig:
            if users is None:
                return TowerConfig.from_env(user_id=user_id)
            token = users.access_token(user_id.removeprefix("user-"))
            return TowerConfig(
                url=os.environ.get("TOWER_URL") or TowerConfig.url,
                authorization=f"Bearer {token}",
                timeout_s=float(os.environ.get("TOWER_TIMEOUT_S", "10")),
            )

        async def agent_for(user_id: str) -> Agent:
            tower = await stack.enter_async_context(TowerClient(tower_config(user_id)))
            return BedrockAgent(tower) if bedrock else ScriptedAgent(tower)

        sent = alerts_sent_list(args.env, os.environ)
        settle = alerts_settle_s(args.env, os.environ)
        control: Control
        if users is not None:
            control = aws_control(
                os.environ, asish=users.sub("asish"), mom=users.sub("mom"), alerts_settle_s=settle, sent=sent
            )
        else:
            control = http_control(alerts_settle_s=settle, sent=sent)
        stack.push_async_callback(control.mock.aclose)
        if sent is not None:
            stack.push_async_callback(sent.aclose)
        for admin in (control.grants, control.settings):
            close = getattr(admin, "aclose", None)
            if close is not None:
                stack.push_async_callback(close)

        def echo(line: str) -> None:
            print(line, flush=True)
            if line.startswith("  ref> "):
                voice.maybe_speak(line[7:])

        def pause(story: Story) -> None:
            step = SHOWCASE_STEP.get(story.name)
            label = f"step {step}: {story.title}" if step else story.title
            input(f"\npress enter for {label} ")

        run = await run_demo(
            agent_for,
            control,
            env=args.env,
            stories=tuple(args.story or (AWS_STORIES if users is not None else STORY_NAMES)),
            out_dir=None if args.no_write else args.out,
            echo=echo,
            pause=pause if args.pause else None,
        )
    if run.mismatches:
        print("\nDEMO MISMATCH:\n  " + "\n  ".join(run.mismatches))
        return EXIT_FAIL
    print(f"\ndemo ok: {len(run.transcripts)} stories, reason codes as expected")
    return EXIT_OK


async def _corpus(args: argparse.Namespace) -> int:
    if args.agent == "scripted":
        _err("the corpus measures a model's tool choice; it cannot run with the scripted agent")
        return EXIT_BEDROCK
    if not _use_bedrock(args.agent):
        print(
            "ref-client corpus: SKIPPED — no AWS credentials (the corpus needs Bedrock). "
            f"{args.out} not written. RUN-ALL Decisions: skipped, not failed."
        )
        return EXIT_OK
    async with TowerClient(TowerConfig.from_env(user_id=user_id_for(args.user))) as tower:
        agent = BedrockAgent(tower)
        print("| # | say | expected | chosen | pass |\n|---:|---|---|---|---|")
        report = await run_corpus(agent, echo=print)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report.markdown(), encoding="utf-8")
    print(
        f"\ncorpus: {report.passed}/{len(report.rows)} = {report.rate:.0%} (gate {report.threshold:.0%}) "
        f"→ {args.out}; tokens in/out {agent.usage['inputTokens']}/{agent.usage['outputTokens']}"
    )
    return EXIT_OK if report.ok else EXIT_FAIL


COMMANDS: dict[str, Any] = {"say": _say, "demo": _demo, "corpus": _corpus}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "serve":
        from ref_client.http import serve

        serve()
        return EXIT_OK
    try:
        return int(asyncio.run(COMMANDS[args.cmd](args)))
    except BedrockUnavailable as e:
        _err(str(e))
        return EXIT_BEDROCK
    except (TowerError, DemoError, CognitoUnavailable) as e:
        _err(str(e))
        return EXIT_TOWER
    except Exception as e:  # noqa: BLE001 - httpx errors from the demo controls
        import httpx

        if isinstance(e, httpx.HTTPError):
            _err(f"demo control call failed: {type(e).__name__}: {redact(str(e))}")
            return EXIT_TOWER
        raise


if __name__ == "__main__":
    sys.exit(main())
