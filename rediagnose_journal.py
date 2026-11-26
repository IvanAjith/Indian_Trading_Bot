"""Re-compute the risk/good factor codes of journal rows with the CURRENT journal.diagnose rules.

Used after the diagnosis rules change (e.g. the 26 Nov 2026 additions: oi_against, stretched, continuation) so old rows
carry the new codes and the weekly review can compare them. Idempotent. Only the codes/lesson/outcome fields are rewritten.

  python rediagnose_journal.py journal/trades.jsonl
"""
import json
import sys

import journal as jr


def rediagnose(path: str) -> int:
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    for r in rows:
        d = jr.diagnose(r)
        r.update({k: d[k] for k in ("outcome", "outcome_label", "win", "risk_codes", "good_codes", "lesson")})
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


if __name__ == "__main__":
    print(f"re-diagnosed {rediagnose(sys.argv[1] if len(sys.argv) > 1 else 'journal/trades.jsonl')} rows")
