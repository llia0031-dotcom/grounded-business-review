# evidence-check (vendored)

`check_evidence.py` is copied unchanged from [evidence-check](https://github.com/Jason-d-e/evidence-check), version 0.4.0 (MIT).

- sha256 of the file: `01a60c2d94c6ce02c81f6e2f3777f18605d028167815170263419d99982fbfde`
- It checks two things for every claim: each quote is in the cited source file word for word, and every number in the claim is in its quotes.
- This project calls `load_sources`, `check_claim` and `numbers_in` from it (see `bizagent/evidence.py`) and does not modify it.
