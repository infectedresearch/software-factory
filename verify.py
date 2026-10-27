"""Recompute research arithmetic without access to the private application."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / 'research'))
import build

n = build.numbers()
ledger = build.load('ledger.json')
commits = [c for lane in build.load('data.json')['lanes'] for c in lane['commits']]
assert len(ledger) == len(commits) == 147
assert len({r['sha'] for r in ledger}) == 147
assert {r['sha'] for r in ledger} == {r['sha'] for r in commits}
assert n['factory_commits'] == 132
assert n['on_branch'] == 110
assert n['real'] == 77
assert round(n['real_hours']) == 98
assert round(n['usd']) == 493
assert round(n['tokens_total'] / 1e6) == 848
assert n['lane0']['keep_pct'] == 30 and n['lanes']['keep_pct'] == 89
assert n['suite']['new'] == []
print(f"147 ledger rows; {n['on_branch']} integrated factory commits; {n['real']} AI-labeled fixes")
print(f"{n['tokens_total']:,} tokens; ${n['usd']:.2f} snapshot list-price cost")
print('Arithmetic verified. Private application outcomes are not independently reproduced.')
