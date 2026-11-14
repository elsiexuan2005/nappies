"""Physiology and diaper assumptions that shape the SYNTHETIC data.

Every value marked ASSUMPTION should be checked against a published source
and cited on your slide. These numbers only decide *how often / how much*
things happen; the sensor scale comes from calibration.json (your real board).
"""

SAMPLE_HZ = 1            # firmware sends 1 line per second
FEATURE_STEP_S = 10      # one training row every 10 s of simulated time
LEAK_SAT = 0.85          # diaper saturation treated as "leak risk"
TTL_CAP_MIN = 240        # time-to-leak is capped at 4 h ("4 h or more")

STATES = ["dry", "wet", "soiled"]

# ASSUMPTION - verify & cite. Per age group:
#   void_interval_min : mean minutes between wees while awake
#   void_ml           : (mean, sd) volume per wee in mL
#   poo_per_day       : average poos per day
#   sizes             : diaper sizes typically worn (sim picks one at random,
#                       which also creates some badly-fitting diapers -> leaks)
# Sanity check: infant urine output is roughly 1-2 mL/kg/h (verify & cite).
AGE_GROUPS = {
    "0-3m":  dict(void_interval_min=60,  void_ml=(8, 3),   poo_per_day=4.0, sizes=[1, 2]),
    "3-6m":  dict(void_interval_min=75,  void_ml=(15, 5),  poo_per_day=2.5, sizes=[2, 3]),
    "6-12m": dict(void_interval_min=100, void_ml=(25, 8),  poo_per_day=1.5, sizes=[3, 4, 5]),
}

# ASSUMPTION - wees are less frequent during sleep
SLEEP_VOID_INTERVAL_FACTOR = 1.5

# ASSUMPTION - practical capacity (mL) of the wet zone under the sensor before
# leak risk. Total lab absorbency is much higher; tune so that a typical
# daytime diaper (~3 h) is part-full and an overnight one (~10-12 h) is near full.
DIAPER_CAPACITY_ML = {1: 60, 2: 80, 3: 105, 4: 130, 5: 155}

# Session mix used for training: (probability, hours)
SESSION_TYPES = {"day": (0.6, 3.0), "night": (0.4, 10.0)}

# ASSUMPTION - mean duration (minutes) of each activity bout
ACTIVITY_MEAN_MIN = {"asleep": 70, "calm": 25, "active": 12}
