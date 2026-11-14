"""Turns model outputs into one clear instruction for the parent."""
import config as C


def recommend(state, sat, p_leak60, ttl_min, still_frac):
    asleep = still_frac > 0.8
    if state == "soiled":
        return "CHANGE NOW", "Soiled diaper detected", "red"
    if sat >= C.LEAK_SAT or p_leak60 > 0.7:
        return "CHANGE NOW", "Leak risk: diaper is nearly full", "red"
    if state == "wet":
        if p_leak60 > 0.4:
            return "CHANGE SOON", f"Wet: {p_leak60:.0%} chance it's full within the hour", "orange"
        if asleep:
            return "CAN WAIT", f"Wet, but baby is asleep: roughly {ttl_min:.0f} min until full", "orange"
        return "CHANGE WHEN CONVENIENT", f"Wet: roughly {ttl_min:.0f} min until full", "orange"
    return "ALL GOOD", "Diaper is dry", "green"
