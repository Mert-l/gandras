"""Run real geographic discovery with configured search, geocoding and Ollama services."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.location.resolver import resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('claim')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = resolve(args.claim, notify=lambda action, summary, details: print(f'[{action}] {summary}', file=sys.stderr))
    output = json.dumps(result, indent=2, ensure_ascii=True)
    if args.output:
        args.output.write_text(output, encoding='utf-8')
    else:
        print(output)
    return 0 if result['status'] == 'resolved' else 2


if __name__ == '__main__':
    raise SystemExit(main())
