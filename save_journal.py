"""
save_journal.py
---------------
Merges one run's journal folder into the long-term copy (the `journal-data` branch checkout) WITHOUT
overwriting what is already there - a day can have more than one run (for example after a restart).

    python save_journal.py journal /tmp/jdata/journal

  trades.jsonl   -> new lines are appended
  YYYY-MM-DD.md  -> new trade sections are appended (the header is only written once)
Anything else (e.g. weekly/, learnings.json) is left alone.
"""

import os
import sys


def merge(src: str, dst: str) -> int:
    if not os.path.isdir(src):
        print(f"[save_journal] no journal folder '{src}' - nothing to save")
        return 0
    os.makedirs(dst, exist_ok=True)
    saved = 0
    jsonl = os.path.join(src, "trades.jsonl")
    if os.path.exists(jsonl):
        with open(jsonl) as fh:
            lines = [l for l in fh if l.strip()]
        with open(os.path.join(dst, "trades.jsonl"), "a") as out:
            out.writelines(l if l.endswith("\n") else l + "\n" for l in lines)
        saved += len(lines)
    for name in sorted(os.listdir(src)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(src, name)) as fh:
            text = fh.read()
        target = os.path.join(dst, name)
        if os.path.exists(target):
            cut = text.find("###")                      # keep only the trade sections
            text = text[cut:] if cut >= 0 else ""
        if text:
            with open(target, "a") as out:
                if os.path.getsize(target) > 0:
                    out.write("\n")                      # blank line between runs' sections
                out.write(text if text.endswith("\n") else text + "\n")
    for name in sorted(os.listdir(src)):          # the day's scanned-symbol list: simply copied (one file per day, overwritten)
        if (name.startswith("universe-") and name.endswith(".txt")) or (name.startswith("regime-") and name.endswith(".jsonl")):
            with open(os.path.join(src, name)) as fh, open(os.path.join(dst, name), "w") as out:
                out.write(fh.read())
    print(f"[save_journal] merged {saved} trade record(s) into {dst}")
    return saved


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    merge(sys.argv[1], sys.argv[2])
