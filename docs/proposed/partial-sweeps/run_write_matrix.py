"""WRITE 2x2 sweep: {sonnet-4-6, sonnet-5} x {disabled, adaptive}.

Measurement only. Reuses production's own invocation path (orchestrate.parse_agent_spec
+ render_body + claude_cli.run_agent with the same tools/permission_mode) so the only
things that vary between arms are `model` and `thinking`. Nothing under newsroom/src or
.claude/agents is modified: the fixture path is substituted into a COPY of the body.

Runs INSIDE the digest-newsroom container (CLAUDECODE blocks nested SDK calls on the host).
Appends one JSON object per rep to --out, carrying the full request config, the SDK usage
block (incl. output_tokens_details.thinking_tokens -- the proof the manipulation took
effect), cost, latency, and the generated draft.
"""
from __future__ import annotations

import argparse, asyncio, datetime, json, shutil, time, traceback
from pathlib import Path
import sys

sys.path.insert(0, "/app/src")

import claude_cli
import orchestrate

PROD_INPUT_DIR = "/app/data/claude_input"
OUT_NAME = "draft_selections.json"


def build_body(agent_path: Path, fixture_dir: Path, run_date: str) -> tuple[str, list[str], str]:
    spec = orchestrate.parse_agent_spec(agent_path)
    body = spec.body.replace(PROD_INPUT_DIR, str(fixture_dir))
    if PROD_INPUT_DIR in body:
        raise SystemExit("fixture path substitution failed")
    if str(fixture_dir) not in body:
        raise SystemExit("fixture path never appeared in body")
    y, m, d = (int(x) for x in run_date.split("-"))
    body = orchestrate.render_body(body, today=datetime.date(y, m, d))
    if "{{CURRENT_DATE}}" in body:
        raise SystemExit("CURRENT_DATE token not rendered")
    return body, orchestrate._tool_list(spec), spec.model


async def one_run(model: str, body: str, thinking: dict, tools: list[str], out_path: Path):
    out_path.unlink(missing_ok=True)
    t0 = time.time()
    res = await claude_cli.run_agent(
        "Begin.",
        model=model,
        system_prompt=body,
        permission_mode="acceptEdits",
        allowed_tools=" ".join(tools),
        tools=tools,
        cwd="/app",
        idle_timeout=300.0,
        thinking=thinking,
    )
    return res, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="/app/.claude/agents/write.md")
    ap.add_argument("--fixture-root", required=True, help="dir holding input/ and meta.json")
    ap.add_argument("--model", required=True)
    ap.add_argument("--thinking", required=True, choices=["disabled", "adaptive"])
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--start-rep", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--drafts-dir", required=True)
    # Each concurrently-running arm needs its OWN copy of the inputs: the agent writes
    # draft_selections.json INTO the directory it reads from, so a shared fixture dir
    # would have four arms clobbering one file. Slots are fixed-width (A1..A4) so the
    # substituted path is the same LENGTH in every arm's prompt -- the prompt must not
    # differ between arms by anything but model/thinking.
    ap.add_argument("--slot", required=True, help="two-char work slot, e.g. A1")
    args = ap.parse_args()

    root = Path(args.fixture_root)
    meta = json.loads((root / "meta.json").read_text())
    if len(args.slot) != 2:
        raise SystemExit("--slot must be exactly 2 chars so prompt length is arm-invariant")
    fixture = Path("/app/write-sweep/out/w") / args.slot / "input"
    if fixture.exists():
        shutil.rmtree(fixture)
    fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "input", fixture)
    body, tools, spec_model = build_body(Path(args.agent), fixture, meta["date"])
    thinking = {"type": args.thinking}
    arm = f"{args.model}/{args.thinking}"
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    drafts = Path(args.drafts_dir); drafts.mkdir(parents=True, exist_ok=True)
    out_path = fixture / OUT_NAME

    for i in range(args.start_rep, args.start_rep + args.reps):
        rec = {"run_id": meta["run_id"], "run_date": meta["date"], "arm": arm,
               "model": args.model, "thinking": args.thinking, "rep": i,
               "spec_model": spec_model, "tools": tools,
               "prompt_chars": len(body)}
        try:
            res, wall = asyncio.run(one_run(args.model, body, thinking, tools, out_path))
            rec.update(ok=bool(res.ok), wall_s=round(wall, 1), duration_ms=res.duration_ms,
                       total_cost_usd=res.total_cost_usd, usage=res.usage, subtype=res.subtype,
                       api_error_status=res.api_error_status,
                       files_read=[Path(p).name for p in (res.files_read or ())])
            if not res.ok:
                rec["error"] = res.error_summary()[:400]
            elif not out_path.exists():
                rec["ok"] = False; rec["error"] = "no draft_selections.json written"
            else:
                payload = json.loads(out_path.read_text())
                dest = drafts / f"r{meta['run_id']}_{args.model}_{args.thinking}_rep{i}.json"
                dest.write_text(json.dumps(payload, indent=1))
                rec["draft_path"] = dest.name
                rec["n_stories"] = len(payload.get("must_know") or []) + len(payload.get("should_know") or [])
        except Exception as e:  # noqa: BLE001
            rec["ok"] = False; rec["error"] = repr(e)[:400]
            rec["traceback"] = traceback.format_exc()[-1500:]
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        u = rec.get("usage") or {}
        think = ((u.get("output_tokens_details") or {}).get("thinking_tokens"))
        print(f"[{arm} r{meta['run_id']} rep{i}] ok={rec.get('ok')} stories={rec.get('n_stories')} "
              f"${rec.get('total_cost_usd')} wall={rec.get('wall_s')}s think_tok={think} "
              f"{rec.get('error','')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
