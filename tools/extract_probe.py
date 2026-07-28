"""
Show what the current extraction prompt returns for every review in the DB.

No code changes -- runs the live prompt against the stored text and prints the
raw JSON. Used to decide whether the prompt needs a rewrite before changing
anything downstream.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import SessionLocal, Review, RcaDraft
from server.prompts import match_indicator_prompt
from server.services import claude


async def main():
    db = SessionLocal()
    for r in db.query(Review).order_by(Review.received_at.desc()).all():
        _orig = (r.body_original or "").strip()
        _eng  = (r.body_english or "").strip()
        match_text = _eng if not _orig else (
            _eng if _orig in _eng else (f"{_eng}\n{_orig}".strip() if _eng else _orig))
        pub = r.received_at.date().isoformat() if r.received_at else ""
        raw = await claude._call(
            match_indicator_prompt(match_text, pub, reviewer_name=r.author or ""),
            max_tokens=400)
        ind = claude._extract_json_object(raw) or {}
        print("=" * 76)
        print(f"{r.author}  ({r.id})")
        print("STORED BODY (first 300 chars):")
        print("  " + (_orig or _eng or "")[:300].replace("\n", " "))
        print(f"EXTRACTED: {ind}")


if __name__ == "__main__":
    asyncio.run(main())
