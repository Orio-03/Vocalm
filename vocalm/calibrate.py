"""Setting your line from your own voice: one pass talking normally, one pass at "too loud".

The line goes 45% of the way from normal to loud, but never closer than 6 dB above normal: ordinary speech swings
that much between words, so a tighter line would go red all the time.
"""
import numpy as np

MIN_GAP_DB = 6.0
SHARE = 0.45
MIN_SAMPLES = {"normal": 20, "loud": 10}  # blocks of actual speech (about 11 ms each)


def level_of(samples, gate_dbfs):
    """Your level in a pass: the 75th percentile of the blocks where you were actually talking, which reflects how
    loud your words are rather than the gaps between them. None if there wasn't enough talking."""
    speech = [s for s in samples if s > gate_dbfs]
    return float(np.percentile(speech, 75)) if speech else None, len(speech)


def compute(normal_samples, loud_samples, gate_dbfs):
    """{"normal", "loud", "threshold", "note"} from the two passes. Raises ValueError with a friendly reason."""
    normal, n_normal = level_of(normal_samples, gate_dbfs)
    if normal is None or n_normal < MIN_SAMPLES["normal"]:
        raise ValueError("Vocalm barely heard you in the normal pass. Check your mic is unmuted and try again.")
    loud, n_loud = level_of(loud_samples, gate_dbfs)
    if loud is None or n_loud < MIN_SAMPLES["loud"]:
        raise ValueError("Vocalm barely heard you in the loud pass. Try again, a little louder.")

    note = ""
    if loud - normal < 3.0:
        threshold = normal + MIN_GAP_DB
        note = "Your two passes sounded about the same, so the line is 6 dB above your normal voice."
    else:
        threshold = normal + (loud - normal) * SHARE
        if threshold - normal < MIN_GAP_DB:
            threshold = normal + MIN_GAP_DB
            note = "Widened to 6 dB above your normal voice, so ordinary speech doesn't trip it."
    return {"normal": round(normal, 1), "loud": round(loud, 1), "threshold": round(threshold, 1), "note": note}
