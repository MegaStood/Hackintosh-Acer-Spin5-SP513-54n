"""Render results.json / tails.json / path.json into the HTML report.
Usage: python -m rblxmc.report.build <output dir>"""
import json, math, os, sys
D = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
R = json.load(open(f"{D}/results.json"))
S0 = R["spot"]; SC = R["scenarios"]; B = R["blend"]; RN = R["risk_neutral"]
KEYS = ["bear", "base", "bull"]
NAME = {"bear": "Bear", "base": "Base", "bull": "Bull"}
THESIS = {
 "bear": "The age check permanently shrank the funnel. Under-13 monetisation never "
         "recovers, more markets copy the mandate, organic sign-ups keep sliding and the "
         "securities class action drags into 2027. Bookings decline year over year and "
         "free cash flow compresses toward $0.7B as capex stays elevated.",
 "base": "Q3 is the trough. Verification coverage climbs past 51%, app-store ratings "
         "normalise and the developer economy pulls engagement back into higher-monetising "
         "experiences. Bookings return to high-single-digit growth through 2027 and free "
         "cash flow holds roughly flat with 2025's $1.35B.",
 "bull": "A fully age-verified base turns out to be worth more than the users it cost. "
         "Verified identity unlocks brand advertising and 17+ content, ARPDAU recovers, "
         "and the 36% revenue growth already printing in Q2 converts into bookings "
         "re-acceleration. Free cash flow scales to $1.9B and the multiple re-rates.",
}

# ---------------------------------------------------------------- fan panels
YMIN, YMAX = 8.0, 240.0
W, H = 340, 268
ML, MR, MT, MB = 44, 10, 14, 26
PW, PH = W - ML - MR, H - MT - MB
WEEKS = R["weeks"]; LASTD = WEEKS[-1]
def fx(d): return ML + PW * d / LASTD
def fy(v): 
    v = min(max(v, YMIN), YMAX)
    return MT + PH * (1 - (math.log(v) - math.log(YMIN)) / (math.log(YMAX) - math.log(YMIN)))
def band(lo, hi):
    up = " ".join(f"{fx(d):.1f},{fy(v):.1f}" for d, v in zip(WEEKS, lo))
    dn = " ".join(f"{fx(d):.1f},{fy(v):.1f}" for d, v in reversed(list(zip(WEEKS, hi))))
    return f"{up} {dn}"
def line(vs): return " ".join(f"{fx(d):.1f},{fy(v):.1f}" for d, v in zip(WEEKS, vs))

YT = [10, 20, 40, 80, 160]
EARN_D = [41, 110, 165, 228]

def fan(k, first):
    s = SC[k]; b = s["bands"]
    g = []
    for v in YT:
        g.append(f'<line class="grid" x1="{ML}" x2="{W-MR}" y1="{fy(v):.1f}" y2="{fy(v):.1f}"/>')
        if first:
            g.append(f'<text class="ax ay" x="{ML-8}" y="{fy(v)+3.5:.1f}">{v}</text>')
    for d, lb in [(0,"now"),(63,"+3m"),(126,"+6m"),(189,"+9m"),(252,"+12m")]:
        g.append(f'<text class="ax axx" x="{fx(d):.1f}" y="{H-MB+15}">{lb}</text>')
    for e in EARN_D:
        g.append(f'<line class="earn" x1="{fx(e):.1f}" x2="{fx(e):.1f}" y1="{H-MB}" y2="{H-MB-5}"/>')
    g.append(f'<line class="spot" x1="{ML}" x2="{W-MR}" y1="{fy(S0):.1f}" y2="{fy(S0):.1f}"/>')
    g.append(f'<polygon class="b90 f-{k}" points="{band(b["p5"], b["p95"])}"/>')
    g.append(f'<polygon class="b50 f-{k}" points="{band(b["p25"], b["p75"])}"/>')
    g.append(f'<polyline class="med s-{k}" points="{line(b["p50"])}"/>')
    return "\n".join(g)

# ---------------------------------------------------------------- density
DW, DH = 760, 300
DL, DR, DT, DB = 52, 16, 18, 34
DPW, DPH = DW - DL - DR, DH - DT - DB
NBINS = R["hist_bins"]; LO, HI = R["hist_range"]; BWD = (HI - LO) / NBINS
XMAX = 160.0
mass = {k: [c / R["npaths"] * SC[k]["prob"] for c in SC[k]["hist"]] for k in KEYS}
peak = max(sum(mass[k][i] for k in KEYS) for i in range(NBINS))
def dx(v): return DL + DPW * min(v, XMAX) / XMAX
def dy(m): return DT + DPH * (1 - m / (peak * 1.08))
def area(lower, upper):
    pts = []
    for i in range(NBINS):
        c = LO + (i + .5) * BWD
        if c > XMAX: break
        pts.append(f"{dx(c):.1f},{dy(upper[i]):.1f}")
    back = []
    for i in reversed(range(len(pts))):
        c = LO + (i + .5) * BWD
        back.append(f"{dx(c):.1f},{dy(lower[i]):.1f}")
    return " ".join(pts + back)
cum = [0.0] * NBINS; layers = []
for k in KEYS:
    lo = cum[:]; cum = [cum[i] + mass[k][i] for i in range(NBINS)]
    layers.append((k, area(lo, cum)))
dens = []
for v in (0, 25, 50, 75, 100, 125, 150):
    dens.append(f'<line class="grid" x1="{dx(v):.1f}" x2="{dx(v):.1f}" y1="{DT}" y2="{DT+DPH}"/>')
    dens.append(f'<text class="ax axx" x="{dx(v):.1f}" y="{DT+DPH+18}">${v}</text>')
for k, pts in layers:
    dens.append(f'<polygon class="dlayer f-{k}" points="{pts}"/>')
OFF_MASS = sum(mass[k][i] for k in KEYS for i in range(NBINS) if LO + (i + .5) * BWD > XMAX)
dens.append(f'<text class="marklab" x="{DW-DR-4:.1f}" y="{DT+9}" text-anchor="end">'
            f'{OFF_MASS*100:.1f}% of the mass lies beyond $160 →</text>')
dens.append(f'<line class="mark spotm" x1="{dx(S0):.1f}" x2="{dx(S0):.1f}" y1="{DT-2}" y2="{DT+DPH}"/>')
dens.append(f'<text class="marklab" x="{dx(S0)-6:.1f}" y="{DT+9}" text-anchor="end">spot ${S0:.2f}</text>')
dens.append(f'<line class="mark medm" x1="{dx(B["p50"]):.1f}" x2="{dx(B["p50"]):.1f}" y1="{DT-2}" y2="{DT+DPH}"/>')
dens.append(f'<text class="marklab strong" x="{dx(B["p50"])+6:.1f}" y="{DT+26}">blended median ${B["p50"]:.2f}</text>')
DENS = "\n".join(dens)

W_ANCHOR = sum(SC[k]["prob"] * SC[k]["px_anchor"] for k in KEYS)
EVENT_IV = (0.57**2 + 4 * (SC["base"]["earn_sd"] * R["evps0"] / S0) ** 2) ** 0.5


# ---------------------------------------------------------------- tables
def pc(x): return f"{x*100:.1f}%"
def usd(x): return f"${x:,.2f}"

RNE = R["risk_neutral_evt"]
vsens_rows = ""
for r in R["vsens"]:
    hi = ' class="hi"' if abs(r["f"] - 1.0) < 1e-9 else ""
    lbl = {0.75: "Within-scenario shocks ×0.75", 1.0: "As modelled", 1.25: "Within-scenario shocks ×1.25"}[r["f"]]
    vsens_rows += (f'<tr{hi}><th scope="row">{lbl}</th>'
                   f'<td class="num">{r["logvol"]*100:.0f}%</td>'
                   f'<td class="num">{usd(r["p5"])}</td><td class="num">{usd(r["p50"])}</td>'
                   f'<td class="num">{usd(r["p95"])}</td><td class="num">{pc(r["P_gain"])}</td>'
                   f'<td class="num">{pc(r["P_halved"])}</td></tr>')


wsens_rows = ""
for r in R["wsens"]:
    hi = ' class="hi"' if "this report" in r["label"] else ""
    wsens_rows += (f'<tr{hi}><th scope="row">{r["label"]}</th>'
                   f'<td class="num">{usd(r["median"])}</td>'
                   f'<td class="num">{r["med_ret"]*100:+.1f}%</td>'
                   f'<td class="num">{pc(r["P_gain"])}</td>'
                   f'<td class="num">{pc(r["P_gt_4832"])}</td>'
                   f'<td class="num">{pc(r["P_halved"])}</td></tr>')

PROWS = [("P_gain", "Finishes above today’s $40.86"),
         ("P_gt_4832", "Clears the $48.32 street average target"),
         ("P_gt_70", "Clears the $70 street high target"),
         ("P_gt_100", "Trades above $100"),
         ("P_doubled", "Doubles"),
         ("P_lt_3388", "Below the $33.88 52-week low"),
         ("P_lt_25", "Below $25"),
         ("P_lt_20", "Below $20"),
         ("P_halved", "Halves")]
prob_rows = ""
for kk, lbl in PROWS:
    cells = "".join(f'<td class="num">{pc(SC[k][kk])}</td>' for k in KEYS)
    bl = B[kk]
    prob_rows += (f'<tr><th scope="row">{lbl}</th>{cells}'
                  f'<td class="num blend"><span class="pbar" style="--w:{bl*100:.1f}%"></span>'
                  f'<span class="pv">{pc(bl)}</span></td>'
                  f'<td class="num rn">{pc(RN[kk])}</td></tr>')

PCT_ROWS = [("p5", "5th percentile"), ("p25", "25th"), ("p50", "Median"),
            ("p75", "75th"), ("p95", "95th percentile"), ("mean", "Mean")]
pct_rows = ""
for kk, lbl in PCT_ROWS:
    cells = "".join(f'<td class="num">{usd(SC[k][kk])}</td>' for k in KEYS)
    cls = ' class="hi"' if kk == "p50" else ""
    pct_rows += (f'<tr{cls}><th scope="row">{lbl}</th>{cells}'
                 f'<td class="num blend">{usd(B[kk])}</td>'
                 f'<td class="num rn">{usd(RN[kk])}</td></tr>')

hz_rows = ""
for lbl, key in [("3 months", "h3m"), ("6 months", "h6m"), ("12 months", None)]:
    cells = ""
    for k in KEYS:
        s = SC[k] if key is None else SC[k][key]
        cells += f'<td class="num">{usd(s["p50"])}<span class="rng">{usd(s["p5"])} – {usd(s["p95"])}</span></td>'
    hz_rows += f'<tr><th scope="row">{lbl}</th>{cells}</tr>'

cards = ""
for k in KEYS:
    s = SC[k]
    cards += f'''<article class="card c-{k}">
      <header><span class="tag t-{k}">{NAME[k]}</span><span class="prob">{s["prob"]*100:.0f}% weight</span></header>
      <h3>{s["name"]}</h3>
      <p class="thesis">{THESIS[k]}</p>
      <dl class="anchor">
        <div><dt>FY27 free cash flow</dt><dd>${s["fcf27"]:.2f}B</dd></div>
        <div><dt>Exit multiple on FCF</dt><dd>{s["mult"]:.0f}×</dd></div>
        <div><dt>Implied 12-month anchor</dt><dd class="big">{usd(s["px_anchor"])}</dd></div>
      </dl>
      <ul class="facts">
        <li><span>Simulated median</span><b>{usd(s["p50"])}</b></li>
        <li><span>90% range</span><b>{usd(s["p5"])} – {usd(s["p95"])}</b></li>
        <li><span>Odds of a gain</span><b>{pc(s["P_gain"])}</b></li>
        <li><span>Median worst drawdown</span><b>{s["med_trough"]*100:.0f}%</b></li>
      </ul></article>'''

# ================================================================ anatomy of a path
PJ = json.load(open(f"{D}/path.json"))
AW = 760; APH = 112; AGAP = 10; ATOP = 4; AXB = 30
AL, ARr = 46, 12
NL = len(PJ["layers"])
AH = ATOP + NL*APH + (NL-1)*AGAP + AXB
APW = AW - AL - ARr
YLO = PJ["ymin"] - 1.5; YHI = PJ["ymax"] + 1.5
def ax_(d): return AL + APW*d/PJ["N"]
def ay_(v, i): 
    top = ATOP + i*(APH+AGAP) + 20
    return top + (APH-26)*(1 - (v-YLO)/(YHI-YLO))

EARN_D = set(PJ["earn_days"]); HOP_D = set(PJ["hop_days"])
def anatomy():
    g = []
    for i, L in enumerate(PJ["layers"]):
        top = ATOP + i*(APH+AGAP)
        g.append(f'<rect class="lane" x="{AL}" y="{top+14:.0f}" width="{APW}" '
                 f'height="{APH-20}" rx="4"/>')
        for v in (40, 60, 80):
            if YLO < v < YHI:
                g.append(f'<line class="grid" x1="{AL}" x2="{AW-ARr}" '
                         f'y1="{ay_(v,i):.1f}" y2="{ay_(v,i):.1f}"/>')
                g.append(f'<text class="ax ay" x="{AL-7}" y="{ay_(v,i)+3.5:.1f}">${v}</text>')
        g.append(f'<text class="lanelab" x="{AL+9}" y="{top+11:.0f}">{L["label"]}</text>')
        g.append(f'<text class="lanesub" x="{AW-ARr:.0f}" '
                 f'y="{top+11:.0f}">{L["sub"]}</text>')
        pts = " ".join(f"{ax_(x):.1f},{ay_(v,i):.1f}" for x, v in L["pts"])
        g.append(f'<polyline class="ppath" points="{pts}"/>')
        # overdraw the discontinuities so they read as breaks, not steep slopes
        if L["key"] in ("earn", "full"):
            seen = {}
            for x, v in L["pts"]:
                seen.setdefault(x, []).append(v)
            for x, vs in seen.items():
                if len(vs) < 2: continue
                d = int(x) - 1
                if L["key"] == "earn" and d not in EARN_D: continue
                cls = "gapline" if d in EARN_D else "gapline hop"
                g.append(f'<line class="{cls}" x1="{ax_(x):.1f}" x2="{ax_(x):.1f}" '
                         f'y1="{ay_(vs[0],i):.1f}" y2="{ay_(vs[-1],i):.1f}"/>')
                mk = (f'<rect class="gapmk" x="{ax_(x)-3:.1f}" y="{ay_(vs[-1],i)-3:.1f}" '
                      f'width="6" height="6" rx="1"/>') if d in EARN_D else \
                     (f'<circle class="gapmk" cx="{ax_(x):.1f}" cy="{ay_(vs[-1],i):.1f}" r="3.4"/>')
                g.append(mk)
                if L["key"] == "full":
                    mv = PJ["earn_moves"].get(str(d)) or PJ["hop_moves"].get(str(d))
                    if mv is not None and abs(mv) > 0.14:
                        mv_px = vs[-1] / vs[0] - 1        # the PLOTTED price gap
                        up = vs[-1] > vs[0]
                        gx = ax_(x) + (8 if up else -8)
                        gy = ay_(vs[-1], i) + (-6 if up else 14)
                        anc = "start" if up else "end"
                        g.append(f'<text class="gaplab" x="{gx:.1f}" y="{gy:.1f}" '
                                 f'text-anchor="{anc}">{mv_px*100:+.1f}%</text>')
    yb = ATOP + NL*APH + (NL-1)*AGAP
    for d, lb in [(0,"now"),(63,"+3m"),(126,"+6m"),(189,"+9m"),(252,"+12m")]:
        g.append(f'<text class="ax axx" x="{ax_(d):.1f}" y="{yb+16:.0f}">{lb}</text>')
    return "\n".join(g)
ANATOMY = anatomy()

# ---------------- zoom on the biggest gap ----------------
ZC = PJ["zoom_center"]; ZR = 9
ZW, ZH = 760, 250; ZL, ZRr, ZT, ZB = 52, 16, 22, 34
ZPW, ZPH = ZW-ZL-ZRr, ZH-ZT-ZB
zpts = [(x, v) for x, v in PJ["layers"][-1]["pts"] if ZC-ZR <= x-1 <= ZC+ZR]
zv = [v for _, v in zpts]
ZLO, ZHI = min(zv)-0.8, max(zv)+0.8
def zx(x): return ZL + ZPW*((x-1)-(ZC-ZR))/(2*ZR)
def zy(v): return ZT + ZPH*(1-(v-ZLO)/(ZHI-ZLO))
zg = []
for v in range(int(ZLO)+1, int(ZHI)+1):
    if (v - int(ZLO)) % 2: continue
    zg.append(f'<line class="grid" x1="{ZL}" x2="{ZW-ZRr}" y1="{zy(v):.1f}" y2="{zy(v):.1f}"/>')
    zg.append(f'<text class="ax ay" x="{ZL-8}" y="{zy(v)+3.5:.1f}">${v}</text>')
zg.append(f'<polyline class="ppath zoomp" points="'
          + " ".join(f"{zx(x):.1f},{zy(v):.1f}" for x, v in zpts) + '"/>')
pre = [v for x, v in zpts if x-1 == ZC][0]
post = [v for x, v in zpts if x-1 == ZC][-1]
zg.append(f'<line class="gapline zoomg" x1="{zx(ZC+1):.1f}" x2="{zx(ZC+1):.1f}" '
          f'y1="{zy(pre):.1f}" y2="{zy(post):.1f}"/>')
for v in (pre, post):
    zg.append(f'<circle class="gapmk" cx="{zx(ZC+1):.1f}" cy="{zy(v):.1f}" r="4.5"/>')
ZGAP = post / pre - 1                                  # plotted price gap
za = 'start' if ZGAP > 0 else 'end'
zoff = 12 if ZGAP > 0 else -12
zg.append(f'<text class="gaplab big" x="{zx(ZC+1)+zoff:.1f}" y="{(zy(pre)+zy(post))/2+4:.1f}" '
          f'text-anchor="{za}">{ZGAP*100:+.1f}% in one day</text>')
zg.append(f'<text class="ax axx" x="{zx(ZC+1):.1f}" y="{ZT+ZPH+18}">earnings</text>')
for off in (-8, -4, 4, 8):
    zg.append(f'<text class="ax axx" x="{zx(ZC+1+off):.1f}" y="{ZT+ZPH+18}">{off:+d}d</text>')
zg.append(f'<text class="ax axx kfoot" x="{ZL+ZPW/2:.1f}" y="{ZT+ZPH+36}">'
          f'no price exists between the two dots — the stock does not trade through the print</text>')
ZOOM = "\n".join(zg)

# ---------------- P vs Q: drift versus martingale ----------------
MW, MH = 760, 280; ML2, MR2, MT2, MB2 = 52, 76, 18, 34
MPW, MPH = MW-ML2-MR2, MH-MT2-MB2
MS = PJ["measure"]; MWK = MS["weeks"]
MLO, MHI = 20.0, 100.0
def mx(w): return ML2 + MPW*w/MWK[-1]
def my(v): return MT2 + MPH*(1-(v-MLO)/(MHI-MLO))
mg = []
for v in (20,40,60,80,100):
    mg.append(f'<line class="grid" x1="{ML2}" x2="{MW-MR2}" y1="{my(v):.1f}" y2="{my(v):.1f}"/>')
    mg.append(f'<text class="ax ay" x="{ML2-8}" y="{my(v)+3.5:.1f}">${v}</text>')
for w, lb in [(0,"now"),(63,"+3m"),(126,"+6m"),(189,"+9m"),(252,"+12m")]:
    mg.append(f'<text class="ax axx" x="{mx(w):.1f}" y="{MT2+MPH+18}">{lb}</text>')
mg.append(f'<line class="spot" x1="{ML2}" x2="{MW-MR2}" y1="{my(40.86):.1f}" y2="{my(40.86):.1f}"/>')
for k in KEYS:
    pts = " ".join(f"{mx(w):.1f},{my(v):.1f}" for w, v in zip(MWK, MS[k]))
    mg.append(f'<polyline class="med s-{k}" points="{pts}"/>')
    mg.append(f'<text class="mlab s-{k}" x="{MW-MR2+7}" y="{my(MS[k][-1])+4:.1f}">'
              f'{NAME[k]} ${MS[k][-1]:,.0f}</text>')
qpts = " ".join(f"{mx(w):.1f},{my(v):.1f}" for w, v in zip(MWK, MS["q"]))
mg.append(f'<polyline class="qline" points="{qpts}"/>')
mg.append(f'<text class="mlab qlab" x="{MW-MR2+7}" y="{my(MS["q"][-1])+4:.1f}">'
          f'Risk-neutral ${MS["q"][-1]:,.0f}</text>')
MEASURE = "\n".join(mg)

# ================================================================ t vs Gaussian
TL = json.load(open(f"{D}/tails.json"))
TW, TH = 372, 250
TLm, TRm, TTm, TBm = 42, 12, 16, 30
TPW, TPH = TW-TLm-TRm, TH-TTm-TBm
XLO, XHI = -5.0, 5.0
def tx(x): return TLm + TPW*(x-XLO)/(XHI-XLO)
def lin_y(d): return TTm + TPH*(1 - d/0.50)
LOG_LO, LOG_HI = -7.0, 0.0
def log_y(d):
    v = math.log10(max(d, 1e-12))
    return TTm + TPH*(1 - (max(v, LOG_LO)-LOG_LO)/(LOG_HI-LOG_LO))
def poly(ys, key):
    pts = [(tx(x), ys(d)) for x, d in zip(TL["curves"]["x"], TL["curves"][key])
           if XLO <= x <= XHI]
    return " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)

def dist_panel(ys, yticks, fmt, label):
    g = []
    for v in yticks:
        g.append(f'<line class="grid" x1="{TLm}" x2="{TW-TRm}" y1="{ys(v):.1f}" y2="{ys(v):.1f}"/>')
        g.append(f'<text class="ax ay" x="{TLm-7}" y="{ys(v)+3.5:.1f}">{fmt(v)}</text>')
    for x in (-4,-2,0,2,4):
        g.append(f'<text class="ax axx" x="{tx(x):.1f}" y="{TH-TBm+16}">{x:+d}σ</text>' if x
                 else f'<text class="ax axx" x="{tx(x):.1f}" y="{TH-TBm+16}">0</text>')
    g.append(f'<polyline class="gauss" points="{poly(ys,"g")}"/>')
    g.append(f'<polyline class="tdist" points="{poly(ys,"t")}"/>')
    return "\n".join(g)

PANEL_LIN = dist_panel(lin_y, [0.1,0.2,0.3,0.4,0.5], lambda v: f"{v:.1f}", "density")
PANEL_LOG = dist_panel(log_y, [1.0, 1e-2, 1e-4, 1e-6],
                       lambda v: ("1" if v == 1.0 else f"1e{int(round(math.log10(v)))}"),
                       "log density")

# tail table
def rat(v): return (f"{v:,.1f}×" if v < 10 else f"{v:,.0f}×")
tail_rows = "".join(
    f'<tr><th scope="row">Beyond ±{r["k"]}σ</th><td class="num">{r["g"]:.2e}</td>'
    f'<td class="num">{r["t"]:.2e}</td><td class="num blend">{rat(r["ratio"])}</td></tr>'
    for r in TL["tails"])

# ================================================================ kurtosis decay
KW, KH = 760, 230
KL, KR, KT, KB = 52, 16, 18, 46
KPW, KPH = KW-KL-KR, KH-KT-KB
HZ = TL["horizon"]
KTOP = max(4, int(max(h["k"] for h in HZ)) + 1)
KMAX = float(KTOP)
KSTEP = 1 if KTOP <= 5 else 2
def ky(v): return KT + KPH*(1 - v/KMAX)
bw = KPW/len(HZ)*0.52
kb = []
for v in range(0, KTOP + 1, KSTEP):
    kb.append(f'<line class="grid" x1="{KL}" x2="{KW-KR}" y1="{ky(v):.1f}" y2="{ky(v):.1f}"/>')
    kb.append(f'<text class="ax ay" x="{KL-8}" y="{ky(v)+3.5:.1f}">{v}</text>')
for i, h in enumerate(HZ):
    cx = KL + KPW*(i+0.5)/len(HZ)
    top = ky(max(h["k"], 0.02))
    kb.append(f'<rect class="kbar" x="{cx-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
              f'height="{ky(0)-top:.1f}" rx="3"/>')
    kb.append(f'<text class="ax axx klab" x="{cx:.1f}" y="{top-7:.1f}">{h["k"]:.2f}</text>')
    kb.append(f'<text class="ax axx" x="{cx:.1f}" y="{KT+KPH+18}">{h["h"]}</text>')
kb.append(f'<line class="zero" x1="{KL}" x2="{KW-KR}" y1="{ky(0):.1f}" y2="{ky(0):.1f}"/>')
kb.append(f'<text class="ax axx kfoot" x="{KL+KPW/2:.1f}" y="{KT+KPH+38}">'
          f'horizon over which the log-return is measured</text>')
KURT = "\n".join(kb)

fans = "".join(
    f'<figure class="fanfig"><figcaption><span class="tag t-{k}">{NAME[k]}</span>'
    f'<span class="fcap">{SC[k]["prob"]*100:.0f}% weight · median path to {usd(SC[k]["p50"])}</span></figcaption>'
    f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{NAME[k]} scenario price fan">'
    f'{fan(k, k=="bear")}</svg></figure>' for k in KEYS)

HTML = f'''<title>RBLX Three-Scenario Monte Carlo</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:ital,wght@0,400;0,600;1,400&display=swap">
<style>
:root {{
  color-scheme: light;
  --bg:#eff2f5; --surface:#ffffff; --surface-2:#f7f9fb; --sunken:#e6ebf0;
  --ink:#12161c; --ink-2:#4d5a6b; --ink-3:#7b8798; --rule:#dde3ea;
  --navy:#1b3a5c; --navy-soft:#e8eef4;
  --bear:#eb6834; --base:#2a78d6; --bull:#1baf7a; --jump:#4a3aa7;
  --shadow:0 1px 2px rgba(18,22,28,.05), 0 8px 24px -18px rgba(18,22,28,.35);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg:#0d1116; --surface:#151b22; --surface-2:#1a212a; --sunken:#10161c;
    --ink:#eef2f6; --ink-2:#a6b2c1; --ink-3:#6f7d8d; --rule:#252e39;
    --navy:#9dc4e8; --navy-soft:#18242f;
    --bear:#d95926; --base:#3987e5; --bull:#199e70; --jump:#9085e9;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 10px 28px -20px rgba(0,0,0,.9);
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg:#0d1116; --surface:#151b22; --surface-2:#1a212a; --sunken:#10161c;
  --ink:#eef2f6; --ink-2:#a6b2c1; --ink-3:#6f7d8d; --rule:#252e39;
  --navy:#9dc4e8; --navy-soft:#18242f;
  --bear:#d95926; --base:#3987e5; --bull:#199e70; --jump:#9085e9;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 10px 28px -20px rgba(0,0,0,.9);
}}
*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:16px; line-height:1.62; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:0 24px 96px; }}
.col {{ max-width:68ch; }}
h1,h2,h3 {{ font-family:"IBM Plex Serif",Georgia,serif; text-wrap:balance; margin:0; }}
:focus-visible {{ outline:2px solid var(--navy); outline-offset:3px; border-radius:3px; }}
.eyebrow {{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px; font-weight:500;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3);
}}
.num, .mono, td.num, .stat b {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums; }}

/* ---------- masthead ---------- */
header.top {{ border-bottom:1px solid var(--rule); background:var(--surface); }}
.top .wrap {{ padding-top:34px; padding-bottom:34px; }}
.ticker {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:14px; }}
.ticker .sym {{ font-family:"IBM Plex Mono",monospace; font-size:13px; font-weight:600;
  letter-spacing:.1em; padding:4px 9px; border:1px solid var(--rule);
  background:var(--navy-soft); color:var(--navy); border-radius:3px; }}
.ticker .px {{ font-family:"IBM Plex Mono",monospace; font-size:30px; font-weight:600;
  font-variant-numeric:tabular-nums; letter-spacing:-.02em; }}
.ticker .meta {{ font-size:13px; color:var(--ink-3); }}
h1 {{ font-size:clamp(30px,4.4vw,46px); line-height:1.12; font-weight:600; margin:18px 0 0;
  letter-spacing:-.015em; max-width:19ch; }}
.standfirst {{ font-size:18px; color:var(--ink-2); margin:16px 0 0; max-width:62ch; }}
.standfirst em {{ font-style:normal; color:var(--ink); font-weight:500;
  box-shadow:inset 0 -.42em 0 var(--navy-soft); }}

/* ---------- verdict strip ---------- */
.verdict {{ display:grid; gap:1px; background:var(--rule); border:1px solid var(--rule);
  border-radius:5px; overflow:hidden; margin-top:30px;
  grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); }}
.stat {{ background:var(--surface); padding:16px 18px; display:flex; flex-direction:column; gap:5px; }}
.stat span {{ font-size:12px; color:var(--ink-3); line-height:1.35; }}
.stat b {{ font-size:23px; font-weight:600; letter-spacing:-.01em; }}
.stat .sub {{ font-size:11.5px; color:var(--ink-3); }}

/* ---------- sections ---------- */
section {{ margin-top:64px; }}
h2 {{ font-size:27px; font-weight:600; letter-spacing:-.01em; margin-bottom:6px; }}
h2 + .lede {{ color:var(--ink-2); margin:0 0 26px; max-width:66ch; }}
.sechead {{ display:flex; align-items:baseline; gap:14px; padding-bottom:12px;
  border-bottom:2px solid var(--navy); margin-bottom:22px; }}
.sechead h2 {{ margin:0; }} .sechead .eyebrow {{ margin-left:auto; }}
p {{ margin:0 0 16px; }} .col p:last-child {{ margin-bottom:0; }}

/* ---------- scenario cards ---------- */
.cards {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(288px,1fr)); }}
.card {{ background:var(--surface); border:1px solid var(--rule); border-radius:6px;
  padding:20px 20px 6px; box-shadow:var(--shadow); display:flex; flex-direction:column;
  border-top:3px solid var(--edge); }}
.c-bear {{ --edge:var(--bear); }} .c-base {{ --edge:var(--base); }} .c-bull {{ --edge:var(--bull); }}
.card header {{ display:flex; align-items:center; gap:10px; margin-bottom:12px; }}
.tag {{ font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:600;
  letter-spacing:.11em; text-transform:uppercase; color:#fff; background:var(--edge);
  padding:3px 8px; border-radius:3px; }}
.t-bear {{ --edge:var(--bear); }} .t-base {{ --edge:var(--base); }} .t-bull {{ --edge:var(--bull); }}
.card .prob {{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-3);
  margin-left:auto; font-variant-numeric:tabular-nums; }}
.card h3 {{ font-size:19px; font-weight:600; line-height:1.28; margin-bottom:10px; }}
.thesis {{ font-size:14.5px; color:var(--ink-2); line-height:1.58; margin:0 0 16px; }}
.anchor {{ margin:0 0 4px; padding:13px 14px; background:var(--surface-2);
  border:1px solid var(--rule); border-radius:4px; display:flex; flex-direction:column; gap:7px; }}
.anchor div {{ display:flex; align-items:baseline; gap:10px; }}
.anchor dt {{ font-size:12.5px; color:var(--ink-3); }}
.anchor dd {{ margin:0 0 0 auto; font-family:"IBM Plex Mono",monospace; font-size:13.5px;
  font-weight:500; font-variant-numeric:tabular-nums; }}
.anchor dd.big {{ font-size:19px; font-weight:600; color:var(--edge); }}
.anchor div:last-child {{ border-top:1px solid var(--rule); padding-top:8px; margin-top:1px; }}
.facts {{ list-style:none; margin:14px 0 0; padding:0; }}
.facts li {{ display:flex; align-items:baseline; gap:12px; padding:8px 0;
  border-top:1px solid var(--rule); font-size:13.5px; }}
.facts li span {{ color:var(--ink-3); }}
.facts li b {{ margin-left:auto; font-family:"IBM Plex Mono",monospace; font-weight:500;
  font-variant-numeric:tabular-nums; }}

/* ---------- charts ---------- */
.panel {{ background:var(--surface); border:1px solid var(--rule); border-radius:6px;
  padding:22px; box-shadow:var(--shadow); }}
.fans {{ display:grid; gap:4px; grid-template-columns:repeat(auto-fit,minmax(258px,1fr)); }}
.fanfig {{ margin:0; min-width:0; }}
.fanfig figcaption {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap;
  margin-bottom:8px; padding-left:4px; }}
.fcap {{ font-size:12px; color:var(--ink-3); font-family:"IBM Plex Mono",monospace;
  font-variant-numeric:tabular-nums; }}
svg {{ display:block; width:100%; height:auto; overflow:visible; }}
.grid {{ stroke:var(--rule); stroke-width:1; }}
.ax {{ font-family:"IBM Plex Mono",monospace; font-size:10px; fill:var(--ink-3);
  font-variant-numeric:tabular-nums; }}
.ay {{ text-anchor:end; }} .axx {{ text-anchor:middle; }}
.spot {{ stroke:var(--ink-3); stroke-width:1; stroke-dasharray:2 3; }}
.earn {{ stroke:var(--ink-3); stroke-width:1.5; opacity:.55; }}
.b90 {{ opacity:.16; }} .b50 {{ opacity:.34; }}
polyline.med {{ fill:none; stroke-width:2; stroke-linejoin:round; }}
.f-bear {{ fill:var(--bear); }} .f-base {{ fill:var(--base); }} .f-bull {{ fill:var(--bull); }}
.s-bear {{ stroke:var(--bear); fill:var(--bear); }}
.s-base {{ stroke:var(--base); fill:var(--base); }}
.s-bull {{ stroke:var(--bull); fill:var(--bull); }}
.lane {{ fill:var(--sunken); opacity:.5; }}
.lanelab {{ font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:600;
  fill:var(--ink); letter-spacing:.02em; }}
.lanesub {{ font-family:"IBM Plex Sans",sans-serif; font-size:10.5px; fill:var(--ink-3);
  text-anchor:end; }}
.ppath {{ fill:none; stroke:var(--navy); stroke-width:1.5; stroke-linejoin:round;
  stroke-linecap:round; }}
.zoomp {{ stroke-width:2.4; }}
.gapline {{ stroke:var(--jump); stroke-width:2.6; stroke-linecap:round; }}
.gapline.hop {{ stroke-dasharray:3 2.5; }}
.zoomg {{ stroke-width:3.4; }}
.gapmk {{ fill:var(--jump); }}
.gaplab {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; font-weight:600;
  fill:var(--jump); }}
.gaplab.big {{ font-size:14px; }}
.mlab {{ font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:600;
  font-variant-numeric:tabular-nums; }}
.qline {{ fill:none; stroke:var(--ink-2); stroke-width:2; stroke-dasharray:6 4; }}
.qlab {{ fill:var(--ink-2); }}
.gauss {{ fill:none; stroke:var(--ink-3); stroke-width:2; stroke-dasharray:5 4; }}
.tdist {{ fill:none; stroke:var(--navy); stroke-width:2.4; stroke-linejoin:round; }}
.kbar {{ fill:var(--navy); opacity:.85; }}
.klab {{ font-weight:600; fill:var(--ink-2); }}
.kfoot {{ fill:var(--ink-3); }}
.zero {{ stroke:var(--ink-3); stroke-width:1.2; }}
.twin {{ display:grid; gap:20px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
.twin figcaption {{ font-size:12.5px; color:var(--ink-3); margin-bottom:6px;
  font-family:"IBM Plex Mono",monospace; }}
.dlayer {{ opacity:.82; stroke:var(--surface); stroke-width:2; }}
.mark {{ stroke-width:1.5; }}
.spotm {{ stroke:var(--ink); stroke-dasharray:3 3; }}
.medm {{ stroke:var(--navy); }}
.marklab {{ font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:500;
  fill:var(--ink-2); stroke:var(--surface); stroke-width:3; paint-order:stroke fill;
  stroke-linejoin:round; }}
.marklab.strong {{ fill:var(--navy); font-weight:600; }}
.legend {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:14px; padding-top:14px;
  border-top:1px solid var(--rule); font-size:13px; color:var(--ink-2); }}
.legend i {{ width:11px; height:11px; border-radius:2px; display:inline-block;
  margin-right:7px; vertical-align:-1px; }}
.legend i.ln {{ width:18px; height:0; border-radius:0; border-top:2px dashed var(--ink-3);
  vertical-align:4px; }}
.legend i.tk {{ width:2px; height:12px; border-radius:0; background:var(--ink-3);
  opacity:.7; vertical-align:-2px; margin-right:12px; margin-left:8px; }}
.note {{ font-size:12.5px; color:var(--ink-3); margin:12px 0 0; }}

/* ---------- tables ---------- */
.tblwrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; min-width:600px; }}
caption {{ text-align:left; font-size:12.5px; color:var(--ink-3); padding-bottom:10px; }}
th, td {{ padding:9px 12px; text-align:left; border-bottom:1px solid var(--rule); }}
thead th {{ font-size:11px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--ink-3); font-weight:600; border-bottom:1.5px solid var(--ink-3);
  font-family:"IBM Plex Mono",monospace; }}
thead th.num, td.num {{ text-align:right; }}
tbody th {{ font-weight:400; color:var(--ink-2); }}
td.num {{ font-variant-numeric:tabular-nums; }}
td.blend {{ font-weight:600; background:var(--surface-2); position:relative; }}
td.rn {{ color:var(--ink-3); }}
tr.hi td, tr.hi th {{ background:var(--navy-soft); font-weight:600; color:var(--ink); }}
.pbar {{ position:absolute; left:0; top:50%; transform:translateY(-50%); height:22px;
  width:var(--w); background:var(--navy); opacity:.13; border-radius:0 2px 2px 0; }}
.pv {{ position:relative; }}
.rng {{ display:block; font-size:11.5px; color:var(--ink-3); font-weight:400; }}
thead th.cbear {{ color:var(--bear); }} thead th.cbase {{ color:var(--base); }}
thead th.cbull {{ color:var(--bull); }}

/* ---------- callout ---------- */
.callout {{ border-left:3px solid var(--navy); background:var(--surface);
  border-radius:0 5px 5px 0; padding:18px 22px; margin-top:26px; }}
.callout h3 {{ font-size:17px; font-weight:600; margin-bottom:8px; }}
.callout p {{ font-size:14.5px; color:var(--ink-2); margin-bottom:0; }}
.limits {{ list-style:none; padding:0; margin:0; display:grid; gap:1px;
  background:var(--rule); border:1px solid var(--rule); border-radius:5px; overflow:hidden; }}
.limits li {{ background:var(--surface); padding:15px 18px; font-size:14px; color:var(--ink-2); }}
.limits b {{ color:var(--ink); font-weight:600; display:block; margin-bottom:2px;
  font-family:"IBM Plex Serif",serif; font-size:15px; }}
footer {{ margin-top:60px; padding-top:20px; border-top:1px solid var(--rule);
  font-size:12.5px; color:var(--ink-3); }}
footer a {{ color:var(--navy); }}
@media (max-width:620px) {{
  .ticker .px {{ font-size:25px; }} .stat b {{ font-size:20px; }}
}}
</style>

<header class="top"><div class="wrap">
  <div class="ticker">
    <span class="sym">NYSE · RBLX</span>
    <span class="px">$40.86</span>
    <span class="meta">close, 31 Aug 2026 · 52-week range $33.88 – $142.00 · $29.0B market cap</span>
  </div>
  <h1>Three futures for Roblox, priced 200,000 times each</h1>
  <p class="standfirst">A jump-diffusion Monte Carlo over the next twelve months, anchored to
  three fundamental cases for what the mandatory age check did to the business. The model says
  the stock is close to <em>a coin flip with a very long left tail</em>.</p>
  <div class="verdict">
    <div class="stat"><span>Blended median, 12 months</span><b>{usd(B["p50"])}</b>
      <span class="sub">{B["med_ret"]*100:+.1f}% vs. spot</span></div>
    <div class="stat"><span>Probability of any gain</span><b>{pc(B["P_gain"])}</b>
      <span class="sub">{pc(B["P_gt_4832"])} clear the $48.32 target</span></div>
    <div class="stat"><span>90% outcome range</span><b>{usd(B["p5"])}–{usd(B["p95"])}</b>
      <span class="sub">5th to 95th percentile</span></div>
    <div class="stat"><span>Expected shortfall (worst 5%)</span><b>{B["CVaR95"]*100:.0f}%</b>
      <span class="sub">{pc(B["P_halved"])} chance the stock halves</span></div>
  </div>
</div></header>

<div class="wrap">

<section>
  <div class="sechead"><h2>Why this is a scenario problem, not a forecast</h2>
    <span class="eyebrow">Setup</span></div>
  <div class="col">
  <p>Roblox made age verification mandatory for chat in January 2026. By the Q1 print on
  30 April only 51% of daily actives had verified, organic sign-ups had slowed and app-store
  ratings had fallen — and management cut full-year bookings guidance by about $900M at the
  midpoint, taking growth from 24% to 10%. The stock fell 18% that day and roughly $6.7B of
  market value went with it. A securities class action followed.</p>
  <p>Q2 made the split sharper rather than settling it. Revenue grew <b>36%</b> to $1.5B, free
  cash flow grew <b>66%</b> to $294M and DAUs reached 123 million (+10% year over year, but a third
  straight sequential decline from the 152M peak in late 2025) — while bookings, the leading
  indicator, grew just <b>8%</b>, and Q3 bookings were guided to a <b>14–18% year-over-year
  decline</b>. Management then withdrew full-year guidance entirely.</p>
  <p>That combination — accelerating reported revenue, collapsing forward bookings, no guidance —
  is why a single price target is close to meaningless here. The distribution is genuinely
  bimodal, so the honest output is a distribution.</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>The three cases</h2><span class="eyebrow">Anchors</span></div>
  <p class="lede">Each scenario is anchored to a fundamental value — FY2027 free cash flow times
  an exit multiple, plus the $5.1B of net cash — rather than to a drift assumption pulled from
  thin air. The simulation is then centred on that anchor.</p>
  <div class="cards">{cards}</div>
  <div class="callout">
    <h3>The weights are the real assumption</h3>
    <p>35 / 45 / 20 across bear, base and bull produces a probability-weighted anchor of
    <b>{usd(W_ANCHOR)}</b> — within a dollar of the $48.32 street average, and inside the $30–$70
    range of published targets. That agreement is a sanity check on the weights, not evidence they
    are right. The table below is the honest error bar: across plausible weight sets the blended
    median swings from double-digit loss to double-digit gain, a wider band than any other input
    produces.</p>
  </div>
  <div class="panel" style="margin-top:18px">
    <div class="tblwrap">
      <table>
        <caption>The same simulated outcomes, remixed under different scenario weights
        (bear / base / bull). Nothing is re-simulated — only the probabilities change.</caption>
        <thead><tr><th scope="col">Weights</th><th scope="col" class="num">Median</th>
          <th scope="col" class="num">vs. spot</th><th scope="col" class="num">P(gain)</th>
          <th scope="col" class="num">P(&gt;$48.32)</th><th scope="col" class="num">P(halved)</th></tr></thead>
        <tbody>{wsens_rows}</tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="sechead"><h2>Twelve months of simulated paths</h2><span class="eyebrow">Fan charts</span></div>
  <p class="lede">Shaded bands are the 50% and 90% path ranges; the solid line is the median.
  Note the log scale — it is the only way the bear and bull cases fit on one axis. Tick marks on
  the baseline are the four earnings dates, where the model injects a discrete gap.</p>
  <div class="panel">
    <div class="fans">{fans}</div>
    <div class="legend">
      <span><i style="background:var(--bear)"></i>Bear median</span>
      <span><i style="background:var(--base)"></i>Base median</span>
      <span><i style="background:var(--bull)"></i>Bull median</span>
      <span><i class="ln"></i>Today's $40.86</span>
      <span><i class="tk"></i>Earnings date</span>
    </div>
    <p class="note">Even the bull case spends time underwater: it has a
    {pc(SC["bull"]["P_touch_3388"])} chance of touching the 52-week low at some point in the year.
    In the bear case that rises to {pc(SC["bear"]["P_touch_3388"])}, with a
    {pc(SC["bear"]["P_touch_25"])} chance of trading below $25. (Touch probabilities include an approximate Brownian-bridge
    correction for intraday crossings on the diffusion leg; on daily closes alone they run about
    one point lower. Note the visible kink at the first earnings tick — that is the Q3 print
    carrying 40% of each scenario's repricing.)</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>Where the price lands in twelve months</h2><span class="eyebrow">Distribution</span></div>
  <p class="lede">The blended distribution, stacked by which scenario contributed each outcome.
  Height is probability mass. The axis stops at $160: {OFF_MASS*100:.1f}% of the weighted mass —
  almost all of it the bull tail, which runs to a 99th percentile near ${SC["bull"]["p99"]:,.0f} —
  lies beyond the right edge.</p>
  <div class="panel">
    <svg viewBox="0 0 {DW} {DH}" role="img"
      aria-label="Probability-weighted distribution of the RBLX share price in twelve months, stacked by scenario. Table of the same values follows.">{DENS}</svg>
    <div class="legend">
      <span><i style="background:var(--bear)"></i>Bear · {SC["bear"]["prob"]*100:.0f}%</span>
      <span><i style="background:var(--base)"></i>Base · {SC["base"]["prob"]*100:.0f}%</span>
      <span><i style="background:var(--bull)"></i>Bull · {SC["bull"]["prob"]*100:.0f}%</span>
    </div>
    <p class="note">The left shoulder is almost entirely the bear case and the right tail almost
    entirely the bull; the two barely overlap. A mean of {usd(B["mean"])} sits well above the
    median of {usd(B["p50"])} — that gap is the right tail, not a central expectation, which is
    exactly why the median is the number to quote.</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>The numbers</h2><span class="eyebrow">Tables</span></div>
  <div class="panel">
    <div class="tblwrap">
      <table>
        <caption>Probability of each outcome twelve months out. The market column is a
        risk-neutral lognormal at the quoted 57% implied volatility (a 30-day quote, flat — no
        skew, no earnings term structure) — what short-dated options are pricing, not a
        forecast.</caption>
        <thead><tr><th scope="col">Outcome</th>
          <th scope="col" class="num cbear">Bear</th><th scope="col" class="num cbase">Base</th>
          <th scope="col" class="num cbull">Bull</th><th scope="col" class="num">Blended</th>
          <th scope="col" class="num">Market</th></tr></thead>
        <tbody>{prob_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="panel" style="margin-top:18px">
    <div class="tblwrap">
      <table>
        <caption>Simulated share price by percentile, twelve months out.</caption>
        <thead><tr><th scope="col">Percentile</th>
          <th scope="col" class="num cbear">Bear</th><th scope="col" class="num cbase">Base</th>
          <th scope="col" class="num cbull">Bull</th><th scope="col" class="num">Blended</th>
          <th scope="col" class="num">Market</th></tr></thead>
        <tbody>{pct_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="panel" style="margin-top:18px">
    <div class="tblwrap">
      <table>
        <caption>Median price by horizon, with the 5th–95th percentile range beneath. The
        3-month column already carries 40% of each scenario's repricing, delivered by the Q3
        print; the remaining 60% accrues evenly, and the ranges widen roughly with the square
        root of time.</caption>
        <thead><tr><th scope="col">Horizon</th>
          <th scope="col" class="num cbear">Bear</th><th scope="col" class="num cbase">Base</th>
          <th scope="col" class="num cbull">Bull</th></tr></thead>
        <tbody>{hz_rows}</tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="sechead"><h2>Anatomy of one path</h2><span class="eyebrow">Mechanism</span></div>
  <p class="lede">"Jump-diffusion" is four things added together. Here is a single simulated year,
  built up one component at a time — same random draw throughout, so each panel is the one above it
  plus exactly one new ingredient.</p>
  <div class="panel">
    <svg viewBox="0 0 {AW} {AH}" role="img"
      aria-label="One simulated price path built in four stages: deterministic drift, plus Student-t diffusion, plus four scheduled earnings gaps, plus Poisson headline jumps. Each stage adds structure to the same underlying random draw.">{ANATOMY}</svg>
    <div class="legend">
      <span><i style="background:var(--navy)"></i>Simulated price</span>
      <span><i style="background:var(--jump)"></i>Scheduled earnings gap (square)</span>
      <span><i style="background:var(--jump);border-radius:50%"></i>Poisson headline jump (circle)</span>
    </div>
    <p class="note">Panel 1 is what the stock would do if nothing ever happened — it is pure
    calibration, and it is the only part of the model that knows about the fundamental anchor.
    Panel 2 adds the day-to-day noise. Only in panels 3 and 4 does the line stop being a curve
    and start breaking.</p>
  </div>

  <h3 style="margin-top:34px;font-size:20px">This is what "not continuous" means</h3>
  <p class="lede" style="margin-top:8px">One earnings date from that same path, magnified.
  The line goes vertical because the price genuinely has nowhere to be in between.</p>
  <div class="panel">
    <svg viewBox="0 0 {ZW} {ZH}" role="img"
      aria-label="A magnified view of one earnings date, where the simulated price gaps vertically by {ZGAP*100:.1f} percent in a single day with no intermediate values.">{ZOOM}</svg>
    <p class="note">A pure diffusion cannot do this. To produce a {abs(ZGAP)*100:.0f}% move it would have to travel
    through every price in between — and it would need enormous volatility every other day of the year to
    make a move that size plausible at all. The jump is what lets the model be calm on Tuesday and
    catastrophic on Wednesday, which is how this stock actually behaves.</p>
  </div>

  <h3 style="margin-top:34px;font-size:20px">And this is what "not a martingale" means</h3>
  <p class="lede" style="margin-top:8px">The expected price over time, averaged across 40,000 paths
  per scenario. A martingale would go sideways.</p>
  <div class="panel">
    <svg viewBox="0 0 {MW} {MH}" role="img"
      aria-label="Expected price over twelve months. The three scenarios fan apart under the real-world measure while the risk-neutral expectation rises only at the risk-free rate.">{MEASURE}</svg>
    <p class="note">The dashed line is the same model under the risk-neutral measure: it lands at
    <b>${MS["q"][-1]:,.2f}</b>, which is exactly $40.86 × e<sup>0.04</sup> — the risk-free rate and
    nothing else. That is the martingale property, and it is the reason a risk-neutral distribution
    is a pricing device rather than a forecast. The three solid lines each carry a drift chosen by
    a human being. That is the difference between this page and an option quote.</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>What the model does</h2><span class="eyebrow">Method</span></div>
  <ul class="limits">
    <li><b>It diffuses enterprise value, not the share price.</b>
    Roblox holds $5.1B of net cash — $7.18 a share — against a $23.9B enterprise value. A plain
    geometric Brownian motion on the equity lets the price wander toward zero, which is not a
    real outcome for a company still generating over $1B of free cash flow. Modelling the
    operating value instead puts an economic floor under the equity, and gives an <i>inverse</i>
    leverage effect: the cash floor damps equity volatility as the stock falls toward cash and
    amplifies it on rallies (equity vol ≈ EV vol × EV⁄(EV+cash), about ×0.82 at spot) — one reason
    the terminal equity distribution is positively skewed. The share count is also not frozen:
    it grows 2.5% over the year, the assumed net issuance after buybacks.</li>
    <li><b>Jumps shape the path, not the endpoint.</b>
    Four scheduled earnings gaps a year, sized so a one-sigma gap is 17–19% of the share price —
    calibrated to the realized prints (RBLX fell 18% and 29% on its last two) — plus
    Poisson headline jumps for regulatory and legal news, on top of Student-t diffusion shocks.
    This makes the <i>daily</i> return distribution realistically fat-tailed and left-skewed — which
    is what drives the drawdown and touch-probability numbers. It does almost nothing to the
    twelve-month distribution, for reasons set out in the next section.</li>
    <li><b>Volatility is decomposed, not double-counted.</b>
    The spread between the three anchors alone contributes 47% annualised log-volatility, and the
    within-scenario volatility is sized so the blended total lands at {pc(B["realised_vol"])}.
    Two things to hold in mind when comparing that with the quoted 57% implied volatility: the
    quote is a <i>30-day</i> number covering a window with no earnings print in it, and adding the
    model's four gaps to it gives an event-consistent one-year level near {EVENT_IV*100:.0f}% — so
    the blend sits <i>at</i> the market's event-adjusted level, not above it. Cutting the other
    way, option-implied volatility usually carries a premium over realised volatility, which argues
    for a physical-measure number somewhat below the implied one. Net: treat this scenario set as
    deliberately wide, not as a claim that options misprice the year.</li>
    <li><b>Drift is calibrated, not assumed — and 40% of it lands on one day.</b>
    Each scenario's drift is solved so the median terminal enterprise value equals its
    fundamental anchor, with the expected jump contribution netted out so the jumps do not
    silently add return. But repricing does not accrue evenly in reality: with guidance withdrawn,
    the Q3 print on ~29 October is the first hard evidence of which scenario is unfolding. So the
    model realises 40% of each scenario's log-repricing as the <i>mean</i> of that day's gap
    ({SC["bear"]["q3_mean"]*100:+.0f}% in the bear case, {SC["bull"]["q3_mean"]*100:+.0f}% in
    the bull) and spreads the remaining 60% across the year. The 12-month median is unchanged by
    this; the 3-month distributions and the drawdown numbers are not.</li>
  </ul>
  <div class="callout">
    <h3>What the total volatility does — and doesn't — rest on</h3>
    <p>The between-scenario spread is a fundamental judgement, not a volatility calibration, and
    once the four gaps are sized to the realized prints the day-to-day diffusion left inside each
    scenario is calm — roughly 30% annualised in equity terms between prints, well below the 57%
    the market quotes for the next print-free month. The model's answer is that much of what
    options price as daily noise is, in a fixed-regime mixture, carried instead as the distance
    between the three medians. That is a defensible structure and an unverifiable calibration, so
    here is what the headline range does when every within-scenario shock is scaled together:</p>
  </div>
  <div class="panel" style="margin-top:18px">
    <div class="tblwrap">
      <table>
        <caption>Blended 12-month outcome versus the within-scenario shock scale (diffusion,
        earnings gaps and headline-jump size all multiplied by the same factor; anchors and
        weights unchanged; 60,000 paths per scenario for the alternates).</caption>
        <thead><tr><th scope="col">Shock scale</th><th scope="col" class="num">1y log-vol</th>
          <th scope="col" class="num">5th pct</th><th scope="col" class="num">Median</th>
          <th scope="col" class="num">95th pct</th><th scope="col" class="num">P(gain)</th>
          <th scope="col" class="num">P(halved)</th></tr></thead>
        <tbody>{vsens_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="callout">
    <h3>Reading the market column</h3>
    <p>The risk-neutral median of {usd(RN["p50"])} sits <i>below</i> today's price. That is not
    the options market forecasting a decline — it is the −σ²⁄2 term in a lognormal with 57%
    volatility, and the absence of any equity risk premium under the risk-neutral measure. Use
    it to judge whether this scenario set is wider or narrower than what is priced, not as a
    competing target. Two statements are both true and easy to confuse. Measured as spread
    <i>around its own median</i>, the model is wider than the benchmark on both sides (its 5th
    percentile sits {math.log(B["p50"]/B["p5"]):.2f} log-units below its median versus
    {math.log(RN["p50"]/RN["p5"]):.2f} for the benchmark). Measured in <i>absolute dollars</i>, the
    model's downside is slightly less severe ({pc(B["P_halved"])} vs {pc(RN["P_halved"])} odds of
    halving; 5th percentile {usd(B["p5"])} vs {usd(RN["p5"])}) — not because its left tail is
    thinner, but because the whole distribution sits higher. On the upside both readings agree:
    P(&gt;$100) is {pc(B["P_gt_100"])} against {pc(RN["P_gt_100"])}. Two caveats in the benchmark's
    disfavour: at the event-adjusted {RNE["iv"]*100:.0f}% the market's own numbers move to
    {pc(RNE["P_halved"])} odds of halving and {pc(RNE["P_gt_100"])} above $100, and a real option
    surface carries put skew, so the true market-implied left tail is fatter than any flat-vol
    lognormal shows.</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>Why Student-t, and where it stops mattering</h2>
    <span class="eyebrow">Shock distribution</span></div>
  <p class="lede">The diffusion shocks are Student-t with 6 degrees of freedom, standardised to
  unit variance, rather than Gaussian. Both distributions below have <i>exactly the same standard
  deviation</i>, so nothing here is about the model being more volatile — it is about where the
  volatility is allowed to show up.</p>
  <div class="panel">
    <div class="twin">
      <figure class="fanfig"><figcaption>Density, linear scale — the tails read as zero</figcaption>
        <svg viewBox="0 0 {TW} {TH}" role="img"
          aria-label="Student-t and Gaussian densities on a linear scale, nearly indistinguishable. Exceedance table follows.">{PANEL_LIN}</svg></figure>
      <figure class="fanfig"><figcaption>Same curves, log scale — the tails separate</figcaption>
        <svg viewBox="0 0 {TW} {TH}" role="img"
          aria-label="The same two densities on a logarithmic scale, where the Student-t tail sits far above the Gaussian. Exceedance table follows.">{PANEL_LOG}</svg></figure>
    </div>
    <div class="legend">
      <span><i class="ln"></i>Gaussian</span>
      <span><i style="background:var(--navy)"></i>Student-t (ν = 6)</span>
    </div>
    <p class="note">The linear panel shows the classic leptokurtic signature: at equal variance the
    t is <b>1.2× taller at the peak</b> and <b>0.8× thinner through the shoulders</b> near ±2σ —
    it has to be, because the variance is the same and the tails have to get their mass from
    somewhere. But past ±3σ both curves are pinned to the axis and the difference that actually
    matters is invisible. On the log scale it is unmissable: the t density runs
    <b>12× the Gaussian at 4σ</b> and <b>307× at 5σ</b>. A fat-tailed model is not a wider
    model — it is a differently-shaped one.</p>
  </div>

  <div class="panel" style="margin-top:18px">
    <div class="tblwrap">
      <table>
        <caption>Probability of a shock beyond ±k standard deviations, at equal variance.</caption>
        <thead><tr><th scope="col">Move</th><th scope="col" class="num">Gaussian</th>
          <th scope="col" class="num">Student-t (6)</th>
          <th scope="col" class="num">How much likelier</th></tr></thead>
        <tbody>{tail_rows}</tbody>
      </table>
    </div>
    <p class="note">A 6-sigma day is a once-in-the-age-of-the-universe event under a Gaussian and a
    once-every-few-years event under Student-t. For a stock that fell 18% in a session on a
    guidance cut, the Gaussian number is not credible.</p>
  </div>

  <div class="callout">
    <h3>But Student-t is symmetric — it adds no skew</h3>
    <p>Correct, and worth being explicit about: the t distribution has zero skewness for ν &gt; 3,
    so it contributes fat tails on <i>both</i> sides equally. Every bit of asymmetry in this model
    comes from two places: the Poisson jumps having a negative mean, and the scenario-signed
    mean of the Q3 gap. Setting the latter aside (it is deterministic repricing, not shock
    asymmetry) and measuring across every other trading day, daily log-return skew is
    {TL["daily"]["base"]["skew"]:.2f} in the base case and {TL["daily"]["bear"]["skew"]:.2f} in
    the bear case, and it is exactly zero if the Poisson jumps are switched off. The other three
    earnings gaps are zero-mean by construction, so they add dispersion but no direction.</p>
  </div>

  <h3 style="margin-top:34px;font-size:20px">The honest limitation</h3>
  <p class="lede" style="margin-top:8px">Fat tails in daily returns wash out as the horizon
  lengthens. Sum enough independent shocks and the central limit theorem flattens the excess
  kurtosis toward zero — and it happens fast:</p>
  <div class="panel">
    <svg viewBox="0 0 {KW} {KH}" role="img"
      aria-label="Excess kurtosis of the log-return falls from {HZ[0]["k"]:.2f} at one day to {HZ[-1]["k"]:.2f} at twelve months.">{KURT}</svg>
    <p class="note">Excess kurtosis of the base-case log-return. By one month it is
    essentially gone; by twelve months the single-scenario distribution is
    <b>indistinguishable from lognormal</b> (skew {TL["base_skew"]:+.2f}, excess kurtosis
    {TL["base_kurt"]:+.2f}). The scheduled
    earnings gaps are themselves Gaussian, so four of them sum to something Gaussian — they look
    fat-tailed only against the backdrop of quiet days.</p>
  </div>
  <div class="col" style="margin-top:22px">
    <p>So the Student-t choice earns its keep on path statistics — drawdowns, the probability of
    touching $25, the shape of any given quarter — and earns almost nothing on the twelve-month
    percentiles at the top of this page. Those are driven by the drift calibration and the
    variance, not by the shock distribution.</p>
    <p>Which relocates the interesting question. The blended twelve-month distribution has skew
    <b>{TL["mix_skew"]:+.2f}</b> and excess kurtosis <b>{TL["mix_kurt"]:+.2f}</b> in log terms — very slightly <i>thin</i>-tailed,
    not fat. Its non-normality is not kurtosis at all; it is the three-way mixture pulling mass out
    to the shoulders, which is what the stacked distribution chart shows. The scenario weights, once
    again, are doing the work.</p>
  </div>
</section>

<section>
  <div class="sechead"><h2>Where this could be wrong</h2><span class="eyebrow">Limits</span></div>
  <ul class="limits">
    <li><b>The weights are a judgement call.</b> Nothing in the data fixes 35/45/20. They are the
    single most consequential input and they are not estimated from anything.</li>
    <li><b>Exit multiples are assumed, and they move with the cases.</b> Pairing a low FCF number
    with a low multiple and a high one with a high multiple widens the spread deliberately —
    which is how multiples behave in practice, but it does compound the two assumptions.</li>
    <li><b>Guidance is withdrawn.</b> The FY2027 free cash flow anchors are extrapolations from a
    company that has explicitly declined to forecast itself. The FY2026 range it did give is
    $1.05–1.28B, against $1.35B in 2025.</li>
    <li><b>"40% at the Q3 print" is a stated guess.</b> How much of the year's repricing the
    29 October print delivers is unknowable in advance; the figure sets the size of the scenario
    divergence at three months and the depth of the early drawdowns, but not the 12-month
    median.</li>
    <li><b>Dilution is a guess.</b> Terminal values are divided by a share count 2.5% higher than
    today's — an assumption about how far the $3B buyback offsets ~$1B/yr of stock-based
    compensation. Each additional point of net issuance takes roughly one percent off every price
    percentile on this page.</li>
    <li><b>No path dependency between scenarios.</b> In reality a bad Q3 print would raise the
    probability of the bear case, not just move the price within it. This model fixes the regime
    at the start and only diffuses within it.</li>
    <li><b>The tail shape at twelve months is close to lognormal.</b> The jump machinery makes the
    daily process realistic but is largely averaged away by the annual horizon, so the extreme
    percentiles rest on the volatility and the scenario spread rather than on any modelled
    crash dynamics.</li>
    <li><b>Not investment advice.</b> This is a modelling exercise on public information,
    sensitive to inputs a reasonable person could set differently.</li>
  </ul>
</section>

<footer>
  200,000 paths per scenario, 252 daily steps, seed 20260901 · prices as of the 31 Aug 2026 close ·
  fundamentals from Roblox's Q1 and Q2 2026 reports and the
  <a href="https://www.sec.gov/Archives/edgar/data/0001315098/000162828026051059/ex991-robloxq22026earnin.htm">Q2 2026 8-K</a> ·
  implied volatility from Barchart · not investment advice.
</footer>
</div>'''

open(f"{D}/rblx-monte-carlo.html", "w").write(HTML)
print("wrote", len(HTML), "bytes")
