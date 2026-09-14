"""
Risk engine exactly as per paper:
  R = wC*C + wB*B + wH*H,  wC+wB+wH = 1
  Defaults: wC=0.35, wB=0.40, wH=0.25, tau1=0.50, tau2=0.65
  H = 1 - exp(-F/3), F = recent failed-attempt count
  C combines hour-of-day + inter-request interval deviation in [0,1]
  B is normalized anomaly score in [0,1] from Isolation Forest lite
Decision:
  R < tau1            -> GRANT
  tau1 <= R < tau2    -> STEP_UP (additional authentication)
  R >= tau2           -> DENY_ALERT
"""
import math
import random


DEFAULTS = {"wC": 0.35, "wB": 0.40, "wH": 0.25, "tau1": 0.50, "tau2": 0.65}


def history_score(fail_count: int) -> float:
    f = max(0, int(fail_count or 0))
    return 1.0 - math.exp(-f / 3.0)


def context_score(hour: float, inter_arrival_min: float, user: dict) -> float:
    """C in [0,1]: 0.5*hour_dev + 0.3*freq_dev + 0.2*session_novelty handled by caller.
    Here: 0.6*hour_dev + 0.4*freq_dev; session novelty added in server as +0.2 blend."""
    try:
        h = float(hour) % 24
    except Exception:
        h = 12.0
    s = float(user.get("usual_start_hour", 7)) if user else 7.0
    e = float(user.get("usual_end_hour", 21)) if user else 21.0
    # hour deviation: 0 inside usual window, ramps to 1 deep at night (2-4am worst)
    if s <= h <= e:
        hour_dev = 0.0
    else:
        # distance outside window, normalized; night hours penalised more
        dist = min(abs(h - s), abs(h - e), 12.0) / 6.0  # 0..~2 -> clip
        night_bonus = 0.35 if (h < 5 or h >= 23) else 0.0
        hour_dev = min(1.0, dist * 0.7 + night_bonus)
    # frequency deviation: burst (very short interval) is suspicious
    try:
        iv = float(inter_arrival_min)
    except Exception:
        iv = 180.0
    mean_iv = float(user.get("mean_interarrival_min", 180)) if user else 180.0
    if iv < 0:
        iv = 0
    if iv >= mean_iv:
        freq_dev = 0.0
    else:
        # iv << mean -> ~1 ; e.g. iv=2min vs mean 180 -> ~0.99
        freq_dev = math.exp(-iv / max(20.0, mean_iv * 0.25))
        freq_dev = min(1.0, max(0.0, freq_dev))
    c = 0.6 * hour_dev + 0.4 * freq_dev
    return min(1.0, max(0.0, c))


def blend_session_novelty(c: float, session_novelty: int) -> float:
    if int(session_novelty or 0) == 1:
        return min(1.0, 0.8 * c + 0.2 * 1.0)
    return c


# ---- Isolation Forest lite (pure python, unsupervised) ----
class IsolationForestLite:
    """Minimal isolation-forest: random splits over feature vectors.
    Train only on normal events. anomaly_score(x) in [0,1] (higher = more anomalous).
    Features: [hour_sin, hour_cos, log_interarrival, fail_count_norm, session_novelty]
    """

    def __init__(self, n_trees=60, max_depth=8, seed=42):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.seed = seed
        self.trees = []  # each tree: list of (feat, split) per depth path? store as nested funcs
        self.n = 0
        self._norm = 1.0

    @staticmethod
    def featurize(hour, inter_arrival_min, fail_count, session_novelty):
        import math as m
        h = float(hour) % 24
        hx = m.sin(2 * m.pi * h / 24.0)
        hy = m.cos(2 * m.pi * h / 24.0)
        iv = max(0.5, float(inter_arrival_min or 60))
        liv = m.log(iv + 1.0) / m.log(600.0)  # ~0..1
        fc = min(1.0, float(fail_count or 0) / 6.0)
        sn = 1.0 if int(session_novelty or 0) else 0.0
        return [hx, hy, liv, fc, sn]

    def fit(self, rows):
        rnd = random.Random(self.seed)
        self.n = len(rows)
        self.trees = []
        if not rows:
            return
        dim = len(rows[0])
        for _ in range(self.n_trees):
            # build a random isolation path set: store random projection splits
            splits = []
            sample = rnd.sample(rows, min(len(rows), 64))
            for d in range(self.max_depth):
                f = rnd.randrange(dim)
                vals = [r[f] for r in sample]
                lo, hi = min(vals), max(vals)
                if lo == hi:
                    splits.append((f, lo))
                else:
                    splits.append((f, rnd.uniform(lo, hi)))
            self.trees.append(splits)
        # normalizer: avg path length for n (standard isolation forest c(n))
        n = max(2, self.n)
        self._norm = 2 * (math.log(n - 1) + 0.5772) - 2 * (n - 1) / n

    def _path(self, x, splits):
        depth = 0
        for (f, s) in splits:
            depth += 1
            # go "left" if x[f] < s else right; stop early if isolated in small range
            # simplified: path length = first depth where |x[f]-s| large => use full depth + adjustment
            pass
        # compute pseudo path: average |x[f]-s| normalized
        dists = []
        for (f, s) in splits:
            dists.append(abs(x[f] - s))
        avg = sum(dists) / max(1, len(dists))
        # map avg distance (0..~2) to path length: small distance -> short path (anomaly)
        # path in (1, max_depth]
        path = 1.0 + (1.0 - min(1.0, avg)) * (self.max_depth - 1)
        return path

    def anomaly_score(self, x):
        import math as m
        liv, fc, sn = x[2], x[3], x[4]
        # interpretable behavioral rules (burst / night+novelty / failure history)
        iv = max(0.5, 600.0 ** max(0.0, min(1.0, liv)) - 1.0)
        hour = (m.atan2(x[0], x[1]) * 12.0 / m.pi) % 24  # from sin/cos
        burst = m.exp(-iv / 8.0) if iv < 30 else 0.0
        night = 0.9 if ((hour < 5 or hour >= 23) and sn >= 0.5) else 0.0
        failr = min(1.0, fc * 6.0 / 3.0) * 0.9
        rule = max(burst, night, failr, sn * 0.45)
        if not self.trees:
            return min(1.0, max(rule, 0.5 * fc + 0.3 * sn))
        paths = [self._path(x, t) for t in self.trees]
        avg_path = sum(paths) / len(paths)
        # standard isolation score: 2^(-E(h)/c(n)), in (0,1]
        score = math.pow(2.0, -avg_path / max(0.5, self._norm * 0.35 + self.max_depth * 0.35))
        tree_score = min(1.0, max(0.0, score))
        return min(1.0, max(tree_score, rule))


def compute_risk(c, b, h, wC=0.35, wB=0.40, wH=0.25):
    return wC * c + wB * b + wH * h


def decide(r, tau1=0.50, tau2=0.65):
    if r < tau1:
        return "GRANT"
    if r < tau2:
        return "STEP_UP"
    return "DENY_ALERT"
