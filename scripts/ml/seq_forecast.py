#!/usr/bin/env python3
"""Phase 6 — does a sequence model on the 10 Hz traces beat engineered lags?

Every model up to here reads hand-made summaries of an 11-22 s series: three
lags, a first difference, a three-cycle rolling mean and standard deviation.
Four runs carry the UE's own RSRP and SINR at 10 Hz, one per terrain, so a
window of 30 s is 300 samples of real measured radio rather than four numbers.
If raw temporal structure carries anything the lag features discard, this is
the only dataset in the project where it could show up.

**The honest sample size.** 862,696 rows at 10 Hz, but the C2 label updates
once per measurement cycle, so one label is inherited by 110-550 consecutive
samples. Independent observations = CYCLES, not samples. Striding is used for
training augmentation; every reported score is computed on exactly one window
per cycle (`anchor="cycle"`), and the cycle counts are printed beside it.

**Three folds, not four.** Forest's cycle cadence is 55 s in the 10 Hz run, so
at a 20 s horizon almost no cycle has a valid target: 14 usable onset cycles
against hills' 1,648. Four-fold LOTO is not viable. The sweep runs over
hills / open_field / urban and reports forest separately as a low-n check.

The comparison that matters is against `summary_logistic` -- a logistic
regression on per-channel mean, slope, min, max and standard deviation of the
same window. If the networks do not beat that, the raw trace adds nothing over
summarising it, which is the Phase 2c result restated at 100x the sample rate.

Usage:
    python3 scripts/ml/seq_forecast.py                 # h=20, 3-fold
    python3 scripts/ml/seq_forecast.py --horizon 60
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
from fastradio_dataset import build_fast, make_windows, CHANNELS, FS  # noqa: E402

warnings.filterwarnings("ignore")
OUT_DIR = ds.REPO / "logs" / "ml"
DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")
MAIN_FOLDS = ["hills", "open_field", "urban"]


# --------------------------------------------------------------------------
class TCN(nn.Module):
    """Dilated temporal CNN. Small on purpose -- Phase 2c showed capacity is
    the enemy under leave-one-terrain-out, and this has fewer folds still."""

    def __init__(self, n_ch: int, width: int = 24):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_ch, width, 7, stride=2, padding=3),
            nn.BatchNorm1d(width), nn.ReLU(),
            nn.Conv1d(width, width, 5, stride=2, padding=2, dilation=1),
            nn.BatchNorm1d(width), nn.ReLU(),
            nn.Conv1d(width, width, 3, stride=2, padding=2, dilation=2),
            nn.BatchNorm1d(width), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1))
        self.head = nn.Linear(width, 1)

    def forward(self, x):
        return self.head(self.net(x).squeeze(-1)).squeeze(-1)


class GRUNet(nn.Module):
    def __init__(self, n_ch: int, hid: int = 24):
        super().__init__()
        self.rnn = nn.GRU(n_ch, hid, batch_first=True)
        self.head = nn.Linear(hid, 1)

    def forward(self, x):
        out, _ = self.rnn(x.transpose(1, 2))
        return self.head(out[:, -1]).squeeze(-1)


# --------------------------------------------------------------------------
def gather(starts, series, W):
    """Materialise (n, C, W) float32 from lazy window indices."""
    out = np.empty((len(starts), len(CHANNELS), W), np.float32)
    for i, (k, end) in enumerate(starts):
        out[i] = series[k][end - W:end].T
    return out


def fit_torch(model, Xtr, ytr, Xte, epochs=30, bs=256, lr=3e-3):
    model = model.to(DEV)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    pos = max(float(ytr.sum()), 1.0)
    w = torch.tensor((len(ytr) - pos) / pos, dtype=torch.float32, device=DEV)
    lossf = nn.BCEWithLogitsLoss(pos_weight=w)
    Xt = torch.tensor(Xtr, device=DEV)
    yt = torch.tensor(ytr, dtype=torch.float32, device=DEV)
    n = len(Xt)
    for _ in range(epochs):
        model.train()
        perm = torch.randperm(n, device=DEV)
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            lossf(model(Xt[idx]), yt[idx]).backward()
            opt.step()
    model.eval()
    with torch.no_grad():
        p = torch.sigmoid(model(torch.tensor(Xte, device=DEV))).cpu().numpy()
    return p


def summary_features(X):
    """Per-channel mean, slope, min, max, std -- what a lag feature set is."""
    n, C, W = X.shape
    t = np.arange(W, dtype=np.float32)
    t = (t - t.mean()) / t.std()
    feats = [X.mean(2), X.min(2), X.max(2), X.std(2),
             (X * t).mean(2)]                 # slope, t standardised
    return np.concatenate(feats, 1).reshape(n, -1)


def report(name, y, p, rows, fold, n_cycles):
    if len(y) < 30 or y.min() == y.max():
        return None
    base = float(y.mean())
    pr = float(average_precision_score(y, p))
    roc = float(roc_auc_score(y, p))
    rows.append(dict(model=name, fold=fold, n=len(y), cycles=n_cycles,
                     pos_rate=base, pr_auc=pr, pr_lift=pr / base, roc_auc=roc))
    return pr / base, roc


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=float, default=20.0)
    ap.add_argument("--win", type=float, default=30.0)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    t0 = time.time()
    W = int(round(args.win * FS))
    torch.manual_seed(0); np.random.seed(0)

    d = build_fast(horizon_s=args.horizon)
    st_tr, ser, y_tr, m_tr = make_windows(d, win_s=args.win, stride_s=1.0,
                                          anchor="stride")
    st_ev, _, y_ev, m_ev = make_windows(d, win_s=args.win, anchor="cycle")
    print(f"device {DEV}   horizon {int(args.horizon)} s   window {int(args.win)} s"
          f" = {W} samples x {len(CHANNELS)} channels")
    print(f"train windows (strided) {len(st_tr):,}   "
          f"eval windows (one per cycle) {len(st_ev):,}")
    print(m_ev.groupby("terrain").agg(cycles=("group", "nunique")).to_string())

    Xtr_all = gather(st_tr, ser, W)
    Xev_all = gather(st_ev, ser, W)

    rows = []
    folds = [t for t in MAIN_FOLDS if (m_ev.terrain == t).sum() >= 30]
    extra = [t for t in sorted(m_ev.terrain.unique()) if t not in folds]
    print(f"\nLOTO folds: {folds}" + (f"   (low-n, reported separately: {extra})"
                                      if extra else ""))
    print(f"\n{'fold':<12}{'model':<20}{'n':>7}{'FAIL%':>7}{'PR-AUC':>9}"
          f"{'xno-skill':>11}{'ROC':>8}")

    for held in folds + extra:
        tr = (m_tr.terrain != held).values & m_tr.terrain.isin(folds).values
        te = (m_ev.terrain == held).values
        if te.sum() < 30 or len(np.unique(y_ev[te])) < 2:
            print(f"{held:<12}{'-- too few labelled onset cycles --':<40}"
                  f"{int(te.sum()):>7}")
            continue
        Xtr, ytr = Xtr_all[tr], y_tr[tr]
        Xte, yte = Xev_all[te], y_ev[te]
        mu = Xtr.mean((0, 2), keepdims=True)
        sd = Xtr.std((0, 2), keepdims=True) + 1e-6
        Xtr_n, Xte_n = (Xtr - mu) / sd, (Xte - mu) / sd
        ncyc = m_ev.loc[te, "group"].nunique()

        Str, Ste = summary_features(Xtr_n), summary_features(Xte_n)
        lin = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                            LogisticRegression(max_iter=4000, C=0.1,
                                               class_weight="balanced"))
        lin.fit(Str, ytr)
        for nm, p in (("summary_logistic", lin.predict_proba(Ste)[:, 1]),
                      ("tcn", fit_torch(TCN(len(CHANNELS)), Xtr_n, ytr, Xte_n,
                                        epochs=args.epochs)),
                      ("gru", fit_torch(GRUNet(len(CHANNELS)), Xtr_n, ytr,
                                        Xte_n, epochs=args.epochs))):
            r = report(nm, yte, p, rows, held, ncyc)
            if r:
                print(f"{held:<12}{nm:<20}{len(yte):>7,}"
                      f"{100*yte.mean():>7.1f}{rows[-1]['pr_auc']:>9.3f}"
                      f"{r[0]:>11.2f}{r[1]:>8.3f}   [{time.time()-t0:.0f}s]")
        print(f"{held:<12}{'no_skill':<20}{len(yte):>7,}"
              f"{100*yte.mean():>7.1f}{yte.mean():>9.3f}{1.0:>11.2f}{0.5:>8.3f}")

    res = pd.DataFrame(rows)
    if not res.empty:
        main_res = res[res.fold.isin(folds)]
        print(f"\n{'='*70}\nMEAN OVER THE {len(folds)} MAIN FOLDS "
              f"(equal weight per terrain)\n{'='*70}")
        print(f"  {'model':<20}{'mean lift':>11}{'mean ROC':>10}{'worst ROC':>11}")
        for nm, g in main_res.groupby("model"):
            print(f"  {nm:<20}{g.pr_lift.mean():>11.2f}"
                  f"{g.roc_auc.mean():>10.3f}{g.roc_auc.min():>11.3f}")
        outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
        res.to_csv(outd / "phase6_seq.csv", index=False)
        print(f"\nwrote {outd/'phase6_seq.csv'}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
