"""Synthetic dataset generator matching the paper:
20 users x 300 normal train = 6000 train
test: 150 normal + 75 anomalous per user = 3000 normal + 1500 anomalous.
Anomaly types: unusual-hour, burst-rate, failure-burst, compromised-but-valid.
"""
import random


def gen_users(n=20, seed=7):
    rnd = random.Random(seed)
    users = []
    for i in range(n):
        start = rnd.choice([6, 7, 8])
        end = rnd.choice([20, 21, 22])
        users.append({
            "id": f"user_{i+1:02d}",
            "name": f"Member {i+1}",
            "pin": f"{1000 + i}",
            "usual_start_hour": float(start),
            "usual_end_hour": float(end),
            "mean_interarrival_min": float(rnd.choice([120, 180, 240])),
        })
    return users


def gen_normal(user, rnd):
    h = rnd.gauss((user["usual_start_hour"] + user["usual_end_hour"]) / 2, 2.5)
    h = max(0, min(23, h))
    # occasional edge but still normal
    iv = max(5, rnd.gauss(user["mean_interarrival_min"], 60))
    return {"hour": h, "inter_arrival_min": iv, "fail_count": 0 if rnd.random() < 0.95 else 1,
            "session_novelty": 0 if rnd.random() < 0.97 else 1, "label": 0}


def gen_anomaly(user, rnd):
    kind = rnd.choice(["hour", "burst", "failburst", "compromised"])
    if kind == "hour":
        h = rnd.choice([1, 2, 3, 4, 23.5])
        iv = max(5, rnd.gauss(user["mean_interarrival_min"], 60))
        return {"hour": h, "inter_arrival_min": iv, "fail_count": 0, "session_novelty": 1, "label": 1, "kind": kind}
    if kind == "burst":
        return {"hour": rnd.uniform(9, 18), "inter_arrival_min": rnd.uniform(0.5, 3),
                "fail_count": 0, "session_novelty": 0, "label": 1, "kind": kind}
    if kind == "failburst":
        return {"hour": rnd.uniform(9, 20), "inter_arrival_min": rnd.uniform(1, 10),
                "fail_count": rnd.randint(3, 6), "session_novelty": 0, "label": 1, "kind": kind}
    # compromised-but-valid: valid credential, off-pattern hour + novelty
    return {"hour": rnd.choice([2, 3, 4]), "inter_arrival_min": rnd.uniform(2, 20),
            "fail_count": rnd.choice([0, 1]), "session_novelty": 1, "label": 1, "kind": kind}


def build_dataset(seed=7):
    rnd = random.Random(seed)
    users = gen_users(seed=seed)
    train, test = [], []
    for u in users:
        for _ in range(300):
            e = gen_normal(u, rnd)
            e["user_id"] = u["id"]
            train.append(e)
        for _ in range(150):
            e = gen_normal(u, rnd)
            e["user_id"] = u["id"]
            test.append(e)
        for _ in range(75):
            e = gen_anomaly(u, rnd)
            e["user_id"] = u["id"]
            test.append(e)
    rnd.shuffle(test)
    return users, train, test
