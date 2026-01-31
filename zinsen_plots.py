import io
import urllib.error
import urllib.request

import pandas as pd
import matplotlib.pyplot as plt

SERIES = {
    "SME (<=0.25m), variabel <=3M": "MIR.M.U2.B.A2A.D.R.2.2240.EUR.N",
    "Large (>1m), variabel <=3M":   "MIR.M.U2.B.A2A.D.R.1.2240.EUR.N",
    "Large (>1m), fix >10Y":        "MIR.M.U2.B.A2A.P.R.1.2240.EUR.N",
}

BASES = [
    "https://data-api.ecb.europa.eu/service/data/MIR/{}?format=csvdata&detail=dataonly",
    "https://sdw-wsrest.ecb.europa.eu/service/data/MIR/{}?format=csvdata&detail=dataonly",
]

def _read_ecb_csv(url: str) -> pd.DataFrame:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    return pd.read_csv(io.BytesIO(data))

def load_series(key: str) -> pd.Series:
    last_err = None
    df = None
    for base in BASES:
        url = base.format(key)
        try:
            df = _read_ecb_csv(url)
            break
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code != 400:
                raise
        except Exception as exc:
            last_err = exc
    if df is None:
        raise last_err
    # Die EZB-CSV enthält typischerweise TIME_PERIOD und OBS_VALUE
    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"])
    s = df.set_index("TIME_PERIOD")["OBS_VALUE"].astype(float).sort_index()
    return s

# 1) laden
data = {name: load_series(key) for name, key in SERIES.items()}
df = pd.DataFrame(data)

# 2) Filter: ab 2016
df_2016 = df[df.index >= "2016-01-01"].copy()

# 3) Jahresmittel (Kalenderjahr), 2026 als YTD bleibt automatisch drin
yearly = df_2016.resample("Y").mean()
yearly.index = yearly.index.year

# 4) Quartalsmittel ab 2022 (Q1–heute)
q = df_2016[df_2016.index >= "2022-01-01"].resample("Q").mean()
q.index = q.index.to_period("Q").astype(str)

# ---- Plot 1: Jahr seit 2016
plt.figure()
for col in yearly.columns:
    plt.plot(yearly.index, yearly[col], marker="o", linewidth=1)

plt.title("Unternehmerzinsen (EZB MIR) – Jahresdurchschnitt seit 2016")
plt.xlabel("Jahr")
plt.ylabel("Zins (% p.a.)")
plt.grid(True, which="both", linestyle="--", linewidth=0.5)
plt.legend()
plt.tight_layout()
plt.show()

# ---- Plot 2: Quartal seit 2022
plt.figure()
for col in q.columns:
    plt.plot(q.index, q[col], marker="o", linewidth=1)

plt.title("Unternehmerzinsen (EZB MIR) – Quartalsdurchschnitt seit 2022")
plt.xlabel("Quartal")
plt.ylabel("Zins (% p.a.)")
plt.xticks(rotation=45, ha="right")
plt.grid(True, which="both", linestyle="--", linewidth=0.5)
plt.legend()
plt.tight_layout()
plt.show()
