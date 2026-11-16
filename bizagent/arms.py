"""The two model arms.

agent    the model has the analysis tools and decides what to look at, turn by turn.
oneshot  one prompt: the model is handed a fixed pack of query results and writes the report.

Both end in the same JSON answer, both go through the same number check, and both get one chance
to revise a report the check did not release.

A run keeps its conversation in state.json next to its evidence folder and names every model call
(folder name + call number), so a run that was interrupted carries on from the last finished step.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import llm, prompts
from . import metrics as M
from .evidence import Evidence, check_blocks, clean_segment, feedback, save_json
from .tools import Toolbox

MAX_CALLS = 16      # model calls per case for the agent, revisions included
MAX_REVISIONS = 1   # times a report that was not released may be rewritten
PACK_METRICS = 3    # one-prompt pack: how many alerted metrics get a full breakdown
PACK_FUNNELS = 6    # one-prompt pack: how many alerted slices get a health check
FORMAT = {"format": {"type": "json_schema", "schema": prompts.ANSWER_SCHEMA}}
CACHE = {"type": "ephemeral"}  # prompt caching: the system prompt, and each call reuses the conversation so far


def _system(with_tools: bool) -> list[dict]:
    return [{"type": "text", "text": prompts.system_prompt(with_tools), "cache_control": CACHE}]


def _filters(items) -> dict:
    return {i["dimension"]: i["value"] for i in (items or []) if isinstance(i, dict) and i.get("value") not in (None, "")}


def _blocks(resp) -> list[dict]:
    """The content of a response as plain dicts, the form it is stored in and sent back in."""
    return [b.model_dump(mode="json", exclude_none=True) for b in resp.content]


def _parse(content: list[dict]) -> dict | None:
    text = next((b["text"] for b in content if b["type"] == "text"), None)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    for c in data.get("causes", []):
        c["segment"] = clean_segment(_filters(c.get("segment")))
    return data


def _call_tool(tb: Toolbox, name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "overview":
            return tb.overview(), False
        if name == "breakdown":
            return tb.breakdown(args["metric"], args["dimension"], _filters(args.get("filters")) or None, args.get("compare", "baseline")), False
        if name == "funnel":
            return tb.funnel(_filters(args.get("filters")) or None, args.get("compare", "baseline")), False
        if name == "run_sql":
            return tb.run_sql(args["sql"], args.get("purpose", "")), False
        return f"没有这个工具：{name}", True
    except Exception as e:  # shown to the model so it can correct the call
        return f"出错：{type(e).__name__}: {str(e)[:600]}", True


class _Run:
    """What both arms keep track of: the conversation, the answer, its checks, revisions. Saved after every step."""

    KEEP = ("messages", "data", "check", "first_check", "first_answer", "revisions", "error", "trace", "finished")

    def __init__(self, week: str, folder: Path):
        self.week = week
        self.state_path = Path(folder).parent / "state.json"
        self.name = Path(folder).parent.name
        saved = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.ev = Evidence(folder, keep=saved.get("evidence"))
        self.usage = llm.Usage(**saved["usage"]) if saved else llm.Usage()
        self.messages: list[dict] = []
        self.data = self.check = self.first_check = self.first_answer = self.error = None
        self.revisions, self.trace, self.finished = 0, [], False
        for k in self.KEEP:
            if k in saved:
                setattr(self, k, saved[k])
        self.resumed = bool(saved)

    def save(self) -> None:
        u = self.usage
        save_json(self.state_path, {**{k: getattr(self, k) for k in self.KEEP}, "evidence": self.ev.items,
                                    "usage": {"calls": u.calls, "input_tokens": u.input_tokens, "cache_write_tokens": u.cache_write_tokens,
                                              "cache_read_tokens": u.cache_read_tokens, "output_tokens": u.output_tokens,
                                              "seconds": u.seconds, "batch": u.batch, "stop_reasons": u.stop_reasons}})

    def ask(self, **kwargs):
        return llm.create(self.usage, key=f"{self.name}#{self.usage.calls + 1}", messages=self.messages, output_config=FORMAT,
                          cache_control=CACHE, **kwargs)

    def accept(self, resp, content: list[dict]) -> str | None:
        """Take a final answer. Returns the text to send back if it must be revised, else None."""
        if resp.stop_reason != "end_turn":
            self.error = f"stopped: {resp.stop_reason}"
            return None
        data = _parse(content)
        if data is None:
            self.error = "answer is not valid JSON"
            return None
        self.data = data
        self.check = check_blocks(data.get("blocks", []), self.ev.dir, bool(data.get("has_anomaly")))
        if self.first_check is None:
            self.first_check, self.first_answer = self.check, data
        if self.check["released"] or self.revisions >= MAX_REVISIONS:
            return None
        self.revisions += 1
        return prompts.RETRY.format(feedback=feedback(self.check, data["blocks"]))

    def result(self) -> dict:
        self.finished = True
        self.save()
        d = self.data or {"has_anomaly": False, "causes": [], "blocks": []}
        return {"week": self.week, "has_anomaly": bool(d.get("has_anomaly")), "causes": d.get("causes", []), "blocks": d.get("blocks", []),
                "evidence": self.ev.items, "check": self.check, "first_check": self.first_check, "first_answer": self.first_answer,
                "revisions": self.revisions, "error": self.error, "usage": self.usage.as_dict(),
                "stop_reasons": self.usage.stop_reasons, "trace": self.trace, "prompt_version": prompts.PROMPT_VERSION}


def run_agent(con, week: str, folder: Path) -> dict:
    run = _Run(week, folder)
    tb = Toolbox(con, week, run.ev)
    if not run.messages:
        run.messages = [{"role": "user", "content": prompts.USER_AGENT.format(week=week)}]
    while not run.finished:
        if run.usage.calls >= MAX_CALLS:
            run.error = f"no answer within {MAX_CALLS} model calls"
            break
        resp = run.ask(system=_system(True), tools=prompts.tools())
        content = _blocks(resp)
        run.messages.append({"role": "assistant", "content": content})
        if resp.stop_reason == "tool_use":
            results = []
            for b in content:
                if b["type"] == "tool_use":
                    text, is_error = _call_tool(tb, b["name"], b["input"])
                    run.trace.append({"tool": b["name"], "input": b["input"], "evidence": None if is_error else run.ev.last_id, "error": is_error})
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": text, **({"is_error": True} if is_error else {})})
            run.messages.append({"role": "user", "content": results})
            run.save()
            continue
        again = run.accept(resp, content)
        if again is None:
            break
        run.messages.append({"role": "user", "content": again})
        run.save()
    return run.result()


def build_pack(tb: Toolbox) -> str:
    """The fixed material of the one-prompt arm: overview, alerted slices, breakdowns of the strongest
    alerted metrics along every dimension they allow, and a health check of each alerted slice."""
    parts = [tb.overview()]
    scan = tb.scan
    al = scan[scan["is_alert"]]
    if al.empty:
        return "\n\n".join(parts)
    ranked = al.reindex(al["robust_z"].abs().sort_values(ascending=False).index)
    seen: list[str] = []
    for name in ranked["metric"]:
        if name not in seen and M.metric(name)["type"] != "distinct":
            seen.append(name)
    for name in seen[:PACK_METRICS]:
        for dim in M.dims_of(M.metric(name)):
            parts.append(tb.breakdown(name, dim))
    slices = ranked[ranked["dim"] != "total"][["dim", "slice"]].drop_duplicates().head(PACK_FUNNELS)
    for r in slices.itertuples():
        parts.append(tb.funnel({r.dim: r.slice}))
    return "\n\n".join(parts)


def run_oneshot(con, week: str, folder: Path) -> dict:
    run = _Run(week, folder)
    if not run.messages:
        tb = Toolbox(con, week, run.ev)
        pack = build_pack(tb)
        run.trace.append({"pack_evidence": len(run.ev.items)})
        run.messages = [{"role": "user", "content": prompts.USER_ONESHOT.format(week=week, pack=pack)}]
        run.save()
    while not run.finished:
        resp = run.ask(system=_system(False))
        content = _blocks(resp)
        again = run.accept(resp, content)
        if again is None:
            break
        run.messages += [{"role": "assistant", "content": content}, {"role": "user", "content": again}]
        run.save()
    return run.result()


ARMS = {"agent": run_agent, "oneshot": run_oneshot}
