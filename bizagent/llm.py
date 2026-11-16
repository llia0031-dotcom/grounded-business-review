"""Calling the model: one client, token and cost accounting, and a pool that sends requests as batches.

The API key is read from a file (BIZ_ENV_FILE, default .env in the project folder), never from the
shell environment, and is never printed or stored anywhere else.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config as C

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
# Claude Haiku 5.5 list price in USD per token, prompts up to 100K tokens
PRICE = {"input": 0.10 / 1e6, "output": 0.50 / 1e6}
CACHE_WRITE, CACHE_READ = 1.25, 0.10   # multiples of the input price (5-minute cache)
BATCH_DISCOUNT = 0.5                    # Message Batches API: half price on all tokens
POOL = None                             # a BatchPool while a batch run is going


def _read_key() -> str:
    env_file = Path(os.environ.get("BIZ_ENV_FILE") or (C.ROOT / ".env")).resolve()
    if not env_file.exists():
        raise SystemExit(f"API key file not found: {env_file}. Set BIZ_ENV_FILE to a file with a line ANTHROPIC_API_KEY=...")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("ANTHROPIC_API_KEY missing in the key file")


@lru_cache(maxsize=1)
def client():
    for k in [k for k in os.environ if k.startswith("ANTHROPIC_")]:
        del os.environ[k]
    import anthropic

    return anthropic.Anthropic(api_key=_read_key(), base_url="https://api.anthropic.com", max_retries=4)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    batch: bool = False
    stop_reasons: list = field(default_factory=list)

    def add(self, resp, secs: float) -> None:
        u = resp.usage
        self.calls += 1
        self.input_tokens += u.input_tokens or 0
        self.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.output_tokens += u.output_tokens or 0
        self.seconds += secs
        self.stop_reasons.append(resp.stop_reason)

    @property
    def cost(self) -> float:
        full = ((self.input_tokens + CACHE_WRITE * self.cache_write_tokens + CACHE_READ * self.cache_read_tokens) * PRICE["input"]
                + self.output_tokens * PRICE["output"])
        return full * BATCH_DISCOUNT if self.batch else full

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "cache_write_tokens": self.cache_write_tokens,
                "cache_read_tokens": self.cache_read_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost, 6), "model_seconds": round(self.seconds, 2),
                "pricing": "batch" if self.batch else "standard"}


def create(usage: Usage, key: str | None = None, **kwargs):
    """One Messages request with the project's model and effort; goes through the batch pool when one is set.

    key names the request (case and call number). A batch pool with a journal uses it after an
    interrupted run to pick the answer up from the batch already submitted instead of sending the request again.
    """
    t0 = time.perf_counter()
    params = dict(model=MODEL, max_tokens=MAX_TOKENS, output_config={"effort": EFFORT, **kwargs.pop("output_config", {})}, **kwargs)
    if POOL is not None:
        resp = POOL.request(params, key)
        usage.batch = True
    else:
        resp = client().messages.create(**params)
    usage.add(resp, time.perf_counter() - t0)
    return resp


class BatchPool:
    """Sends the requests of many concurrent workers as Message Batches.

    Each worker (one case) runs in its own thread and calls create() as usual. While a pool is set as
    POOL, create() hands the request to the pool and waits. When every running worker is waiting, the
    waiting requests go out as one batch; when it has ended each worker gets its message back and
    carries on (runs its tools, asks again). So each round of the tool loops, across all workers, is
    one batch, and prompts, tools and parameters are those of a normal run. Results are matched by
    custom_id. An errored or expired request is sent again in the next batch, three tries in all;
    an invalid request is not retried.

    With a journal file, every submitted request is written down as (key, batch id, custom id). After an
    interrupted run, a request whose key is in the journal is answered from that batch, not sent again.
    """

    def __init__(self, poll_seconds: float = 15.0, log=print, journal: Path | None = None):
        self.cv = threading.Condition()
        self.waiting: dict[str, dict] = {}
        self.running = 0
        self.n = 0
        self.closing = False
        self.poll = poll_seconds
        self.log = log
        self.batches: list[dict] = []
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.journal_path = journal
        self.journal: dict[str, tuple[str, str]] = {}
        self.fetched: dict[str, dict] = {}
        self.reused = 0
        self.io = threading.Lock()
        if journal and journal.exists():
            for line in journal.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.journal[e["key"]] = (e["batch"], e["custom_id"])

    def _from_journal(self, key: str):
        """The message a previous process already asked for under this key, or None."""
        batch_id, custom_id = self.journal[key]
        c = client()
        with self.io:
            if batch_id not in self.fetched:
                while c.messages.batches.retrieve(batch_id).processing_status != "ended":
                    time.sleep(self.poll)
                self.fetched[batch_id] = {r.custom_id: r.result for r in c.messages.batches.results(batch_id)}
        res = self.fetched[batch_id].get(custom_id)
        return res.message if res is not None and res.type == "succeeded" else None

    def start(self):
        self.thread.start()

    def enter(self):
        with self.cv:
            self.running += 1

    def leave(self):
        with self.cv:
            self.running -= 1
            self.cv.notify_all()

    def close(self):
        with self.cv:
            self.closing = True
            self.cv.notify_all()
        self.thread.join()

    def request(self, params: dict, key: str | None = None):
        if key and key in self.journal:
            message = self._from_journal(key)
            if message is not None:
                with self.cv:
                    self.reused += 1
                return message
        with self.cv:
            self.n += 1
            slot = {"params": params, "tries": 0, "done": False, "message": None, "error": None, "key": key}
            self.waiting[f"req-{self.n}"] = slot
            self.cv.notify_all()
            while not slot["done"]:
                self.cv.wait()
        if slot["error"]:
            raise RuntimeError(slot["error"])
        return slot["message"]

    def _loop(self):
        while True:
            with self.cv:
                while not (self.waiting and len(self.waiting) >= self.running) and not (self.closing and not self.waiting):
                    self.cv.wait(timeout=5)
                if self.closing and not self.waiting:
                    return
                send, self.waiting = self.waiting, {}
            try:
                results = self._send(send)
            except Exception as e:  # the batch itself failed: its requests are tried again, then stored as failures
                results = {}
                self.log(f"batch failed: {type(e).__name__}: {e}")
            with self.cv:
                for cid, slot in send.items():
                    res = results.get(cid)
                    if res is not None and res.type == "succeeded":
                        slot["message"], slot["done"] = res.message, True
                        continue
                    slot["tries"] += 1
                    why = "missing" if res is None else res.type
                    if res is not None and res.type == "errored":
                        why += f" ({getattr(getattr(res.error, 'error', None), 'type', '')}: {getattr(getattr(res.error, 'error', None), 'message', '')})"
                    if slot["tries"] < 3 and "invalid_request" not in why:
                        self.waiting[cid] = slot
                    else:
                        slot["error"], slot["done"] = f"batch request {why}", True
                self.cv.notify_all()

    def _send(self, send: dict) -> dict:
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        c = client()
        batch = c.messages.batches.create(requests=[
            Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**slot["params"])) for cid, slot in send.items()])
        if self.journal_path:
            with self.journal_path.open("a", encoding="utf-8", newline="\n") as f:
                for cid, slot in send.items():
                    if slot.get("key"):
                        f.write(json.dumps({"key": slot["key"], "batch": batch.id, "custom_id": cid}) + "\n")
                        self.journal[slot["key"]] = (batch.id, cid)
        t0 = time.time()
        while True:
            b = c.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(self.poll)
        results = {r.custom_id: r.result for r in c.messages.batches.results(batch.id)}
        counts = b.request_counts
        self.batches.append({"requests": len(send), "minutes": round((time.time() - t0) / 60, 1),
                             "succeeded": counts.succeeded, "errored": counts.errored, "expired": counts.expired})
        self.log(f"batch {len(self.batches)}: {len(send)} requests, ended after {(time.time() - t0) / 60:.1f} min "
                 f"({counts.succeeded} succeeded, {counts.errored} errored, {counts.expired} expired)")
        return results
