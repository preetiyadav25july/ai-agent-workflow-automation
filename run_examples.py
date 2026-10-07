"""Runs every example request and writes the transcripts to outputs/.
   python run_examples.py            -> examples/requests.json  (the 10 required workflows)
   python run_examples.py --edge     -> examples/edge_cases.json (conditions & error handling)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from agent.agent import Agent, format_report  # noqa: E402


def main():
    edge = "--edge" in sys.argv
    src = ROOT / "examples" / ("edge_cases.json" if edge else "requests.json")
    out = ROOT / "outputs" / ("edge_case_results.txt" if edge else "example_results.txt")
    agent = Agent()
    cases = json.loads(src.read_text())
    blocks, summary = [], []
    for c in cases:
        decision, run = agent.handle(c["request"], c.get("params"), log=False)
        blocks.append(f"### {c['id']}" + (f"   (expected: {c['expect']})" if c.get("expect") else "") + "\n"
                      + format_report(decision, run, agent.workflows, c["request"], agent.llm.mode))
        summary.append(f"{c['id']:<34} -> {(decision.workflow_id or 'NONE'):<6} "
                       f"{run.status if run else 'no workflow selected'}")
    text = "\n\n".join(blocks) + "\n\nSUMMARY\n" + "\n".join(summary) + "\n"
    out.write_text(text, encoding="utf-8")
    print("\n".join(summary))
    print(f"\nFull transcripts: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
