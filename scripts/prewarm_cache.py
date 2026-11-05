"""Run every demo case once so STAC searches, raster subsets and VLM outputs are cached.

Afterwards the demo cases replay from cache (marked from_cache) without network access to
Earth Search. Cached data are the same real scenes; dates and scene IDs are unchanged.

Usage: python scripts/prewarm_cache.py [CASE_ID ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database  # noqa: E402
from app.agent.orchestrator import Runner  # noqa: E402
from app.agent.state import InvestigationState, load_demo_cases, new_id  # noqa: E402

database.init_db()
wanted = set(sys.argv[1:])
ok = True
for case in load_demo_cases():
    if wanted and case["id"] not in wanted:
        continue
    t0 = time.time()
    st = InvestigationState(investigation_id=new_id(), mode=case["mode"], claim=case["default_claim"], case_id=case["id"],
                            location={k: case[k] for k in ("name", "latitude", "longitude", "width_km")},
                            temporal_scope={"before_target": case["before_date"], "after_target": case["after_date"]})
    st = Runner(st).run()
    v = st.verdict or {}
    print(f"{case['id']:28s} {st.status:9s} {v.get('status', '-'):13s} {v.get('confidence', '-'):6s} "
          f"vlm={(st.visual_analysis or {}).get('status', '-'):11s} cache={st.from_cache} {time.time() - t0:5.0f}s  {st.investigation_id}")
    ok &= st.status == "completed"
sys.exit(0 if ok else 1)
