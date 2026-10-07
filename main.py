"""CLI for the workflow agent.

  python main.py "Which products need restocking?"
  python main.py "Where is order ORD-1001?"
  python main.py "Generate SEO content for this product" --param product_name="Trail Backpack" --param material=Nylon
  python main.py --interactive          # chat; the agent asks follow-up questions when it needs info
  python main.py --list                 # show the workflows loaded from Excel
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent.agent import Agent, format_report  # noqa: E402
from agent.router import heuristic_params  # noqa: E402


def parse_params(pairs, file=None):
    p = {}
    for item in pairs or []:
        if "=" not in item:
            sys.exit(f"--param expects key=value, got: {item}")
        k, v = item.split("=", 1)
        p[k.strip()] = v.strip()
    if file:
        p["file"] = file
    return p


def show(agent, request, params, workflow=None, log=True):
    decision, run = agent.handle(request, params, workflow_id=workflow, log=log)
    print(format_report(decision, run, agent.workflows, request, agent.llm.mode))
    return decision, run


def interactive(agent, log=True):
    print(f"Workflow agent ready ({agent.llm.mode}). Type 'quit' to exit.\n")
    while True:
        try:
            req = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if req.lower() in ("quit", "exit", ""):
            break
        params, wf = {}, None
        while True:
            decision, run = show(agent, req, params, wf, log=log)
            if run is None or run.status != "needs_input":
                break
            ans = input("\nYou (answer, or blank to cancel): ").strip()
            if not ans:
                break
            wf = run.workflow_id
            new = heuristic_params(ans)
            if not new and len(run.missing) == 1:       # a bare answer fills the one missing field
                new = {run.missing[0]: ans}
            params.update(new)
            if not new:
                print("Sorry, I could not read that. Use key=value, e.g. campaign_goal=...; dates=...")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?")
    ap.add_argument("--param", "-p", action="append", help="extra input as key=value (repeatable)")
    ap.add_argument("--file", "-f", help="input file for workflows that take one")
    ap.add_argument("--workflow", "-w", help="skip routing and force a workflow id, e.g. WF003")
    ap.add_argument("--interactive", "-i", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-log", action="store_true", help="do not append to execution_logs.csv")
    a = ap.parse_args()
    agent = Agent()
    if a.list:
        for wf in agent.workflows.values():
            print(f"{wf.id}  {wf.name}\n      steps: {' -> '.join(wf.steps)}")
        return
    if a.interactive:
        return interactive(agent, log=not a.no_log)
    if not a.request:
        ap.error("give a request, or use --interactive / --list")
    show(agent, a.request, parse_params(a.param, a.file), a.workflow, log=not a.no_log)


if __name__ == "__main__":
    main()
