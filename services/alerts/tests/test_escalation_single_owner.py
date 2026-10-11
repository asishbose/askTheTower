"""06 §5 "single owner of escalation state" (build-log/22-ops.md, "test_c23 — single owner of escalation state").

Every write to an `esc#<line_id>#<watcher>` row is conditional on the `(chain_id, version)` its writer read; the
winner owns the step (it alone texts, audits, parks or deletes), the loser does nothing.

- The primitives: `save_escalation` (create when absent, advance from the version read), `clear_escalation`
  (delete only the version read), a row deleted and re-created never matching a stale reader, legacy rows.
- The race, for real: the scheduler's tick, a poll (evaluate + its own tick) and the webhook's evaluate run in
  three threads, each with its own event loop, 50 times over the transplant chain of 06 §11.4 (partner with ack,
  then the neighbour). A rendezvous holds the first conditional writes of a phase until two consumers reach it,
  so they hit DynamoDB together. One lock around the moto client stands in for DynamoDB's per-item atomicity,
  which moto does not give threads; the race is in Alerts, above it.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import pytest
from alerts import escalation
from alerts import state as S
from alerts.clock import FixedClock
from alerts.runner import poll, process_line
from alerts.send_backends import LogSender
from alerts.testing import (
    ASISH,
    LINE_KEY,
    MSISDN_KEY,
    T0,
    TEST_WALL,
    FakeCarrier,
    FakeLine,
    World,
    audit_rows,
    make_service,
)
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store
from tower_consent import tables as T
from tower_policy import Facts, ReasonCode

pytestmark = pytest.mark.integration

M = timedelta(minutes=1)
OWNER, P, N = "user-asish", "user-partner", "user-neighbour"
CHAIN = [(P, True), (N, False)]
RUNS = 50
GATE_TIMEOUT_S = 0.5
LINE = "ln_" + "a" * 64


def _esc(step: int = 0, **kw: Any) -> S.Escalation:
    return S.Escalation(
        line_id=LINE,
        watcher_user_id=OWNER,
        codes=[ReasonCode.UNREACHABLE],
        facts=Facts(fetched_at=T0),
        tz="America/Toronto",
        step=step,
        sent_at=T0 + step * 15 * M,
        **kw,
    )


def _wall() -> datetime:
    return TEST_WALL


# --- the conditional primitives ----------------------------------------------------------------------------


def test_a_new_row_is_created_only_where_none_exists(store: Store) -> None:
    first = S.save_escalation(store, _esc(), wall=_wall())
    assert first is not None and first.version == 1 and first.chain_id
    assert S.save_escalation(store, _esc(), wall=_wall()) is None  # a second "new chain" without a read loses
    stored = S.get_escalation(store, LINE, OWNER)
    assert stored == first


def test_one_read_advances_the_step_once(store: Store) -> None:
    held = S.save_escalation(store, _esc(0), wall=_wall())
    assert held is not None
    won = S.save_escalation(store, held.model_copy(update={"step": 1}), wall=_wall(), held=held)
    lost = S.save_escalation(store, held.model_copy(update={"step": 1}), wall=_wall(), held=held)
    assert won is not None and won.step == 1 and won.version == 2 and won.chain_id == held.chain_id
    assert lost is None
    assert S.get_escalation(store, LINE, OWNER) == won


def test_a_stale_reader_cannot_delete_and_the_owner_deletes_once(store: Store) -> None:
    held = S.save_escalation(store, _esc(0), wall=_wall())
    assert held is not None
    moved = S.save_escalation(store, held.model_copy(update={"step": 1}), wall=_wall(), held=held)
    assert moved is not None
    assert not S.clear_escalation(store, held)  # read step 0; the row is at step 1 now
    assert S.get_escalation(store, LINE, OWNER) == moved
    assert S.clear_escalation(store, moved)
    assert S.get_escalation(store, LINE, OWNER) is None
    assert not S.clear_escalation(store, moved)  # nothing left to delete


def test_a_row_deleted_and_recreated_does_not_match_its_old_reader(store: Store) -> None:
    old = S.save_escalation(store, _esc(0), wall=_wall())
    assert old is not None and S.clear_escalation(store, old)
    new = S.save_escalation(store, _esc(0), wall=_wall())
    assert new is not None and new.version == old.version and new.chain_id != old.chain_id
    assert S.save_escalation(store, old.model_copy(update={"step": 1}), wall=_wall(), held=old) is None
    assert not S.clear_escalation(store, old)
    assert S.get_escalation(store, LINE, OWNER) == new


def test_a_row_parked_before_versions_is_taken_by_the_first_writer_only(store: Store) -> None:
    legacy = _esc(0).model_dump(mode="json", exclude={"chain_id", "version"})
    store.put(S.ALERTS_STATE, {**legacy, "pk": f"esc#{LINE}#{OWNER}", "expires_at": 1})
    held = S.get_escalation(store, LINE, OWNER)
    assert held is not None and held.version == 0 and held.chain_id == ""
    won = S.save_escalation(store, held.model_copy(update={"step": 1}), wall=_wall(), held=held)
    assert won is not None and won.version == 1 and won.chain_id
    assert S.save_escalation(store, held.model_copy(update={"step": 1}), wall=_wall(), held=held) is None
    assert not S.clear_escalation(store, held)


# --- the race ----------------------------------------------------------------------------------------------


class Rendezvous:
    """One-shot: the first `parties` callers leave together (or after the timeout); later callers pass."""

    def __init__(self, parties: int = 2, timeout: float = GATE_TIMEOUT_S) -> None:
        self.parties = parties
        self.timeout = timeout
        self.armed = False
        self.met = 0  # how many times `parties` callers really arrived together
        self._arrived = 0
        self._cond = threading.Condition()

    def arm(self) -> None:
        with self._cond:
            self.armed, self._arrived = True, 0

    def wait(self) -> None:
        with self._cond:
            if not self.armed:
                return
            self._arrived += 1
            if self._arrived >= self.parties:
                self.armed = False
                self.met += 1
                self._cond.notify_all()
                return
            self._cond.wait_for(lambda: not self.armed, self.timeout)
            self.armed = False  # alone at the gate: let everyone through from now on


class LockedClient:
    """The boto3 client with one lock per call: DynamoDB's per-item atomicity, which moto lacks under threads."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self.exceptions = client.exceptions

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return target(*args, **kwargs)

        return call


class RacingStore(Store):
    """A conditional write on `esc#` / `rl#` waits at that prefix's rendezvous first; esc deletes are recorded."""

    def __init__(self, client: Any, prefix: str) -> None:
        super().__init__(LockedClient(client), prefix)  # type: ignore[arg-type]
        self.gates = {"esc#": Rendezvous(), "rl#": Rendezvous()}
        self.esc_deleted: list[dict[str, Any]] = []

    def _gate(self, table: T.Table, pk: object, condition: str | None) -> None:
        if table is S.ALERTS_STATE and condition and isinstance(pk, str):
            for prefix, gate in self.gates.items():
                if pk.startswith(prefix):
                    gate.wait()

    def put(self, table: T.Table, item: Mapping[str, Any], **kw: Any) -> None:
        self._gate(table, item.get("pk"), kw.get("condition"))
        super().put(table, item, **kw)

    def update(
        self, table: T.Table, key: Mapping[str, Any], update_expression: str, **kw: Any
    ) -> dict[str, Any]:
        self._gate(table, key.get("pk"), kw.get("condition"))
        return super().update(table, key, update_expression, **kw)

    def delete(self, table: T.Table, key: Mapping[str, Any], **kw: Any) -> dict[str, Any] | None:
        self._gate(table, key.get("pk"), kw.get("condition"))
        old = super().delete(table, key, **kw)
        if old is not None and str(key.get("pk", "")).startswith("esc#"):
            self.esc_deleted.append(old)
        return old


def race(*consumers: Callable[[], Awaitable[object]]) -> None:
    """Each consumer in its own thread and event loop, started together; any exception fails the run."""
    errors: list[BaseException] = []
    start = threading.Barrier(len(consumers))

    def run(consumer: Callable[[], Awaitable[object]]) -> None:
        async def go() -> None:
            await consumer()

        try:
            start.wait(timeout=10)
            asyncio.run(go())
        except BaseException as e:  # noqa: BLE001 - re-raised in the test thread below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(c,)) for c in consumers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "a consumer hung"
    if errors:
        raise errors[0]


def _world(store: RacingStore) -> tuple[World, FakeCarrier, FixedClock, LogSender, str]:
    store.ensure_tables()
    S.ensure_alerts_table(store)
    clock = FixedClock(T0)
    carrier = FakeCarrier(clock)
    sender = LogSender()
    svc = make_service(store, carrier, clock, sender=sender)
    world = World(
        svc=svc, store=store, hasher=LocalLineIdHasher(LINE_KEY), cipher=LocalMsisdnCipher(MSISDN_KEY), now=T0
    )
    world.standard()
    world.grant_watch(ASISH, OWNER, P, "asish")
    world.grant_watch(ASISH, OWNER, N, "asish")
    world.watch(ASISH, OWNER, "transplant", CHAIN)
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    return world, carrier, clock, sender, world.lines[ASISH]


def _drop(store: Store, client: Any) -> None:
    for t in (*T.TABLES, S.ALERTS_STATE):
        client.delete_table(TableName=store.name(t))


def _labels(sender: LogSender, to: str) -> list[str]:
    return [s.label for s in sender.sent if s.label == to]


async def _one_run(client: Any, n: int) -> tuple[int, int]:
    store = RacingStore(client, prefix=f"race{n}-{uuid.uuid4().hex[:6]}-")
    world, carrier, clock, sender, line = _world(store)
    svc = world.svc
    try:
        await process_line(svc, line, "poll", T0)  # baseline
        clock.set(T0 + M)
        carrier.set_reachable(ASISH, False)
        await process_line(svc, line, "event", clock.at)

        # +21: 20 min dark. The poll and the event both decide to alert; the tick races beside them.
        clock.set(T0 + 21 * M)
        store.gates["rl#"].arm()
        now = clock.at
        race(
            lambda: poll(svc, "transplant", now),
            lambda: process_line(svc, line, "event", now),
            lambda: escalation.tick(svc, now),
        )
        assert _labels(sender, f"chain:{P}") == [f"chain:{P}"], f"run {n}: {[s.label for s in sender.sent]}"
        assert _labels(sender, f"chain:{N}") == [], f"run {n}"
        assert _labels(sender, f"line_holder:{OWNER}") == [f"line_holder:{OWNER}"], f"run {n}"
        parked = S.get_escalation(store, line, OWNER)
        assert parked is not None and parked.step == 0, f"run {n}: parked {parked}"
        assert store.esc_deleted == [], f"run {n}: a row was deleted before the last step"

        # +36: 15 min with no ack. Two ticks (the scheduler's and the poll's) and an event race on the row.
        clock.set(T0 + 36 * M)
        store.gates["esc#"].arm()
        now = clock.at
        race(
            lambda: escalation.tick(svc, now),
            lambda: poll(svc, "transplant", now),
            lambda: process_line(svc, line, "event", now),
        )
        assert _labels(sender, f"chain:{P}") == [f"chain:{P}"], f"run {n}"
        assert _labels(sender, f"chain:{N}") == [f"chain:{N}"], f"run {n}: {[s.label for s in sender.sent]}"
        assert S.get_escalation(store, line, OWNER) is None, f"run {n}: the chain outlived its last step"
        assert [d["step"] for d in store.esc_deleted] == [1], f"run {n}: deletes {store.esc_deleted}"

        rows = [
            (r.trigger, r.outcome, tuple(c.value for c in r.reason_codes)) for r in audit_rows(store, line)
        ]
        released = [r for r in rows if r[1] == "changed"]
        assert released == [
            ("event", "changed", ("UNREACHABLE",)),
            ("poll", "changed", ("UNREACHABLE",)),
        ] or (released == [("poll", "changed", ("UNREACHABLE",))] * 2), (
            f"run {n}: one release at +21 (event or poll), one tick release at +36: {rows}"
        )
        # besides the releases: rate-limited repeats (06 §5) and the poll watchdog's first subscribe, nothing
        others = [r for r in rows if r[1] != "changed"]
        assert set(others) <= {
            ("event", "suppressed", ("UNREACHABLE",)),
            ("poll", "suppressed", ("UNREACHABLE",)),
            ("poll", "ok", ("OK",)),
        }, f"run {n}: {rows}"
        assert others.count(("poll", "ok", ("OK",))) <= 1, f"run {n}: {rows}"
        return store.gates["esc#"].met, svc.metrics["escalation_lost"]
    finally:
        _drop(store, client)


async def test_ticks_and_evaluate_racing_on_one_chain_text_each_step_once(
    request: pytest.FixtureRequest,
) -> None:
    client = request.getfixturevalue("moto_dynamodb")
    met = lost = 0
    for n in range(RUNS):
        m, lo = await _one_run(client, n)
        met += m
        lost += lo
    # the race was real: in most runs two ticks reached the conditional write together, and one of them lost
    assert met >= RUNS // 2, f"two ticks met at the conditional write in only {met}/{RUNS} runs"
    assert lost >= met, f"{lost} lost claims for {met} meetings"
