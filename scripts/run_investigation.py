"""Run one investigation synchronously from the command line (same code path as the API worker).

Usage: python scripts/run_investigation.py CASE_ID ["claim text"] [--no-llm]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database  # noqa: E402
from app.agent.orchestrator import Runner  # noqa: E402
from app.agent.state import InvestigationState, load_demo_cases, new_id  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
case = next(c for c in load_demo_cases() if c["id"] == args[0])
claim = args[1] if len(args) > 1 else case["default_claim"]
database.init_db()
st = InvestigationState(investigation_id=new_id(), mode=case["mode"], claim=claim, case_id=case["id"],
                        location={k: case[k] for k in ("name", "latitude", "longitude", "width_km")},
                        temporal_scope={"before_target": case["before_date"], "after_target": case["after_date"]})
st = Runner(st, use_llm="--no-llm" not in sys.argv).run()
for s in st.steps:
    print(f"[{s.status:9s}] {s.action:24s} ({s.decided_by}) {s.summary}\n{'':14}why: {s.reason}")
print("STATUS", st.status, st.error)
if st.verdict:
    print(json.dumps({k: st.verdict[k] for k in ("status", "confidence", "summary", "rationale")}, indent=1))
print("from_cache", st.from_cache, "id", st.investigation_id)
