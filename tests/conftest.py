"""Suite-wide safety: no test run may make a live model call (it spends money and uses the
owner's credentials). The sandbox runner refuses to place a real credential file into a job home
while ``ZKQEC_FORBID_LIVE=1``; only the explicitly opted-in live test (``ZKQEC_LIVE=1``) lifts it.
Set before any test module imports zkqec; inherited by every subprocess a test starts."""
import os

if os.environ.get("ZKQEC_LIVE") != "1":
    os.environ["ZKQEC_FORBID_LIVE"] = "1"
