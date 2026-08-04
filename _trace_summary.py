# -*- coding: utf-8 -*-
"""Dump a compact summary of .sun/traces/*.jsonl for resuming work.

Usage: python _trace_summary.py [trace_file] [tail_n]
"""
import json
import sys

target = sys.argv[1] if len(sys.argv) > 1 else None
tail_n = int(sys.argv[2]) if len(sys.argv) > 2 else 0

fs = sorted(__import__('glob').glob('.sun/traces/*.jsonl'))
out = []
for f in fs:
    if target and target not in f:
        continue
    lines = open(f, encoding='utf-8', errors='replace').read().splitlines()
    if tail_n:
        lines = lines[-tail_n:]
    out.append('=== ' + f)
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = r['kind']
        if k == 'tool_call':
            out.append('CALL ' + r['data']['name'] + ' ' + str(r['data'].get('args', ''))[:200])
        elif k == 'tool_result':
            res = r['data'].get('result')
            if isinstance(res, dict):
                s = str(res.get('stdout') or res.get('summary') or '')[:120]
                out.append('  RES ok=' + str(res.get('success')) + ' ' + s)
        elif k == 'think':
            out.append('THINK ' + str(r['data'].get('text', ''))[:160])
        elif k == 'finish':
            out.append('FINISH ' + str(r['data'].get('summary', ''))[:400])
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
print('\n'.join(out))
