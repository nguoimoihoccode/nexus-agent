"""Symbol list for the read-only market display page.

HOSE reviews its large-cap basket semi-annually. This is a frozen transcription
serving one display-only page: it carries no revision, no lineage, and no
production-eligibility record, so it must never be treated as a research
universe. Anything that feeds a dataset or a backtest belongs in the governed
`quant-data-worker` path instead.

The tuple is deliberately *not* named after the index. HOSE's review effective
2026-08-03 appoints 30 symbols, but every source reachable when this list was
written -- a Vietnambiz summary citing HOSE's 2026-07-15 announcement, and the
HOSE announcement page itself -- enumerated only the 27 below (MCH and TCX
added, PLX and TPB removed). The other three were not invented. Pasting HOSE's
published basket into this tuple is the only change needed to make it exact.

Every symbol here was verified to return HOSE/VND data from the display provider
on 2026-10-16. HOSE only: the provider has no HNX or UPCOM listing, so adding a
non-HOSE ticker produces a permanent "unavailable" row rather than an error.
"""

HOSE_LARGE_CAP_SYMBOLS: tuple[str, ...] = (
    "ACB",
    "BID",
    "BSR",
    "CTG",
    "FPT",
    "GAS",
    "HDB",
    "HPG",
    "LPB",
    "MBB",
    "MCH",
    "MSN",
    "MWG",
    "SHB",
    "SSB",
    "STB",
    "TCB",
    "TCX",
    "VCB",
    "VHM",
    "VIB",
    "VIC",
    "VJC",
    "VNM",
    "VPB",
    "VPL",
    "VRE",
)
