"""
V3 Liq-Cluster Comprehensive Backtest — Real Coinalyze Data
=============================================================
Uses actual liquidation history from Coinalyze for all 28 symbols.
"""
import csv, math, sqlite3, time, uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

ALL_SYMBOLS = [
    "NEARUSDT","ZECUSDT","ADAUSDT","WLDUSDT","UNIUSDT","NMRUSDT","PENDLEUSDT",
    "ARBUSDT","RENDERUSDT","RUNEUSDT","FETUSDT","DOTUSDT","TONUSDT","SOLUSDT",
    "1000LUNCUSDT","ENAUSDT","1000PEPEUSDT","XRPUSDT","FILUSDT","BNBUSDT",
    "TAOUSDT","CHZUSDT","DASHUSDT","QNTUSDT","ICPUSDT","XLMUSDT","APTUSDT","ETHUSDT",
]
BTC_SYMBOL = "BTCUSDT"

class V3C:
    liq_lookback=90; liq_percentile=0.90; liq_min_lookback=30; liq_window=2
    require_short_squeeze=True; ret5d_min=-5.0
    range_lookback=60; imb_z_threshold=2.0; vol_z_threshold=3.0
    body_strength_min=0.60; impulse_min_pct=0.30; ema_period=20
    z_lookback=100; min_confirmations=4; cooldown_bars=36
    no_reentry_after_stop=True; atr_period=14; initial_stop_atr=2.5
    vol_trail_atr=2.0; struct_lookback=12; decay_threshold=0.30
    partial_r=2.5; partial_fraction=0.50; max_hold_bars=288

CFG = V3C()
BASE_RISK=2.0; BTC_ALIGNED_RISK=4.0; MAX_LEV=10; MAX_POS=10; MAX_PER_SYM=1
INIT_EQ=10000.0; TAKER_BPS=4.5; SLIP_BPS=2.0
JAN1_MS=1767225600000; APR30_MS=1777593599000
DATA_DIR=Path("/root/bitana/backtest_data")
OUTPUT_DIR=Path("/root/bitana/backtest_output")
KLINES_DB=DATA_DIR/"klines_5m.db"; LIQ_DB=DATA_DIR/"coinalyze_liq.db"

def _ema(v, span):
    if len(v)<2: return v[-1] if len(v) else 0.0
    a=2.0/(span+1); e=v[0]
    for x in v[1:]: e=a*x+(1-a)*e
    return e

def _atr(h,l,c,p):
    if len(h)<2: return h[0]-l[0] if len(h) else 0.0
    tr=np.empty(len(h)); tr[0]=h[0]-l[0]
    for i in range(1,len(h)): tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    return _ema(tr,p)

def load_5m(sym):
    conn=sqlite3.connect(str(KLINES_DB))
    rows=conn.execute("SELECT open_time,close_time,open,high,low,close,volume,taker_buy_volume FROM klines WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",(sym,JAN1_MS,APR30_MS)).fetchall()
    conn.close()
    if not rows: return None
    n=len(rows)
    return {"symbol":sym,"ot":np.array([r[0]for r in rows],dtype=np.int64),"ct":np.array([r[1]for r in rows],dtype=np.int64),
            "o":np.array([r[2]for r in rows]),"h":np.array([r[3]for r in rows]),"l":np.array([r[4]for r in rows]),
            "c":np.array([r[5]for r in rows]),"v":np.array([r[6]for r in rows]),"tbv":np.array([r[7]for r in rows]),"n":n}

def load_liq(sym):
    conn=sqlite3.connect(str(LIQ_DB))
    rows=conn.execute("SELECT timestamp,long_liq,short_liq FROM liquidation_history WHERE symbol=? ORDER BY timestamp",(f"{sym}_PERP.A",)).fetchall()
    conn.close()
    if not rows: return None
    return {"t":np.array([r[0]for r in rows],dtype=np.int64),"ll":np.array([r[1]for r in rows]),"sl":np.array([r[2]for r in rows]),"n":len(rows)}

def load_daily(sym):
    conn=sqlite3.connect(str(LIQ_DB))
    rows=conn.execute("SELECT date,close FROM daily_closes WHERE symbol=? ORDER BY date",(sym,)).fetchall()
    conn.close()
    return {r[0]:r[1] for r in rows} if rows else None

class CascadeTracker:
    def __init__(self): self.h=deque(maxlen=100)
    def update(self,tl,ll,sl,cl):
        self.h.append({"tl":tl,"ll":ll,"sl":sl,"cl":cl})
        if len(self.h)<CFG.liq_min_lookback: return False,0.0
        liqs=[r["tl"] for r in self.h]
        lb=liqs[-CFG.liq_lookback:] if len(liqs)>=CFG.liq_lookback else liqs
        p90=np.percentile(lb,CFG.liq_percentile*100)
        if p90<=0: return False,0.0
        ca=any(liqs[-(i+1)]>p90 for i in range(min(CFG.liq_window+1,len(liqs))))
        st=liqs[-1]/p90 if p90>0 else 0
        last=self.h[-1]; total=last["tl"]
        imb=(last["ll"]-last["sl"])/total if total>0 else 0
        ch=[r["cl"] for r in self.h]
        r5=((ch[-1]/ch[-6])-1)*100 if len(ch)>=6 and ch[-6]>0 else 0
        if CFG.require_short_squeeze and imb>=0: ca=False
        if CFG.ret5d_min is not None and r5<=CFG.ret5d_min: ca=False
        return ca,st

def precompute_signals(candles, liq_data, daily_closes, btc_candles):
    n=candles["n"]
    if n<200: return []
    ct=CascadeTracker()
    ca_arr=np.zeros(n,dtype=bool); cs_arr=np.zeros(n)
    if liq_data is not None and liq_data["n"]>0:
        liq_idx=0; cur_ca=False
        for i in range(n):
            bar_date=datetime.fromtimestamp(candles["ot"][i]/1000,tz=timezone.utc).strftime("%Y-%m-%d")
            while liq_idx<liq_data["n"]:
                ld=datetime.fromtimestamp(liq_data["t"][liq_idx],tz=timezone.utc)
                if ld.strftime("%Y-%m-%d")<=bar_date:
                    cl=daily_closes.get(bar_date,candles["c"][i]) if daily_closes else candles["c"][i]
                    ct.update(liq_data["ll"][liq_idx]+liq_data["sl"][liq_idx],liq_data["ll"][liq_idx],liq_data["sl"][liq_idx],cl)
                    liq_idx+=1
                else: break
            if i==0 or datetime.fromtimestamp(candles["ot"][i-1]/1000,tz=timezone.utc).strftime("%Y-%m-%d")!=bar_date:
                if len(ct.h)>0:
                    liqs=[r["tl"] for r in ct.h]
                    p90=np.percentile(liqs[-CFG.liq_lookback:],90) if len(liqs)>=CFG.liq_min_lookback else 0
                    spike=any(liqs[-(j+1)]>p90 for j in range(min(CFG.liq_window+1,len(liqs)))) if p90>0 else False
                    last=ct.h[-1]; total=last["tl"]
                    imb=(last["ll"]-last["sl"])/total if total>0 else 0
                    ch=[r["cl"] for r in ct.h]
                    r5=((ch[-1]/ch[-6])-1)*100 if len(ch)>=6 and ch[-6]>0 else 0
                    cur_ca=spike and (imb<0 if CFG.require_short_squeeze else True) and (r5>CFG.ret5d_min if CFG.ret5d_min is not None else True)
            ca_arr[i]=cur_ca
            if len(ct.h)>0:
                liqs2=[r["tl"] for r in ct.h]
                p2=np.percentile(liqs2[-CFG.liq_lookback:],90) if len(liqs2)>=CFG.liq_min_lookback else 1
                cs_arr[i]=liqs2[-1]/p2 if p2>0 else 0

    c=candles["c"]; h=candles["h"]; l=candles["l"]; v=candles["v"]; tbv=candles["tbv"]; o=candles["o"]
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
    atr=np.zeros(n); atr[0]=tr[0] if len(tr)>0 else 0
    aa=2.0/(CFG.atr_period+1)
    for i in range(1,n): atr[i]=aa*tr[min(i-1,len(tr)-1)]+(1-aa)*atr[i-1]

    ema=np.zeros(n); ema[0]=c[0]; ae=2.0/(CFG.ema_period+1)
    for i in range(1,n): ema[i]=ae*c[i]+(1-ae)*ema[i-1]

    rl=CFG.range_lookback; rh=np.zeros(n)
    for i in range(rl+1,n): rh[i]=np.max(h[i-rl-1:i-1])

    vz=np.zeros(n)
    for i in range(CFG.z_lookback,n):
        w=v[i-CFG.z_lookback:i]; s=np.std(w)
        vz[i]=(v[i]-np.mean(w))/s if s>1e-12 else 0

    has_taker=tbv[-1]>0; imb_z=np.zeros(n)
    if has_taker:
        ts=v-tbv; safe=np.where(v>0,v,1.0); ir=(tbv-ts)/safe
        for i in range(CFG.z_lookback,n):
            w=ir[i-CFG.z_lookback:i]; s=np.std(w)
            imb_z[i]=(ir[i]-np.mean(w))/s if s>1e-12 else 0

    cr=h-l; cb=np.abs(c-o); bs=np.where(cr>0,cb/cr,0)
    br=np.where(o>0,(c-o)/o*100,0)

    btc_al=np.zeros(n,dtype=bool)
    if btc_candles and btc_candles["n"]>21:
        bc=btc_candles["c"]; be=np.zeros(btc_candles["n"]); be[0]=bc[0]
        a2=2.0/21
        for i in range(1,btc_candles["n"]): be[i]=a2*bc[i]+(1-a2)*be[i-1]
        btc_t=btc_candles["ct"]
        for i in range(n):
            idx=np.searchsorted(btc_t,candles["ct"][i])
            if idx>=btc_candles["n"]: idx=btc_candles["n"]-1
            if idx>=20: btc_al[i]=bc[idx]>be[idx] and bc[idx]>bc[max(0,idx-12)]

    n_needed=max(CFG.range_lookback,CFG.z_lookback,CFG.ema_period*3)
    signals=[]; cd=0; stopped=False; last_ca=False

    for i in range(n_needed,n):
        if cd>0: cd-=1; continue
        if CFG.no_reentry_after_stop and stopped: continue
        if not ca_arr[i]:
            if last_ca and not ca_arr[i]: stopped=False
            last_ca=ca_arr[i]; continue

        conf={"breakout":bool(c[i]>rh[i]),"imb":bool(imb_z[i]>CFG.imb_z_threshold) if has_taker else False,
              "vol":bool(vz[i]>CFG.vol_z_threshold),"body":bool(bs[i]>CFG.body_strength_min),
              "impulse":bool(br[i]>CFG.impulse_min_pct),"momentum":bool(c[i]>ema[i])}
        cc=sum(1 for v in conf.values() if v)
        if cc<CFG.min_confirmations: continue

        ep=c[i]; sd=atr[i]*CFG.initial_stop_atr; sp=ep-sd
        signals.append({"uuid":str(uuid.uuid4()),"symbol":candles["symbol"],"bar_idx":i,"entry_price":ep,
                        "stop_price":sp,"risk_distance":sd,"atr":atr[i],"confirmations":conf,"confirm_count":cc,
                        "cascade_strength":cs_arr[i],"imb_z":round(float(imb_z[i]),2),"vol_z":round(float(vz[i]),2),
                        "body_strength":round(float(bs[i]),2),"bar_return_pct":round(float(br[i]),3),
                        "close_time":int(candles["ct"][i]),"btc_aligned":bool(btc_al[i])})
        cd=CFG.cooldown_bars
    return signals

def quality_score(sig):
    cs=sig.get("cascade_strength",0); vz=sig.get("vol_z",0); iz=sig.get("imb_z",0)
    brp=sig.get("bar_return_pct",0); bs=sig.get("body_strength",0)
    ls=min(cs/3,1); vs=min(vz/6,1); ims=min(abs(iz)/4,1); is_=min(brp/2,1); bts=bs
    btc=1.0 if sig.get("btc_aligned") else 0.0
    return round(ls*0.3+vs*0.2+is_*0.15+ims*0.15+btc*0.1+bts*0.1,4)

def is_ny(ct_ms):
    h=datetime.fromtimestamp(ct_ms/1000,tz=timezone.utc).hour
    return 13<=h<22

def replay(signals, candles_data, btc_candles, config):
    equity=INIT_EQ; peak=INIT_EQ; open_pos={}; closed=[]; atrh=defaultdict(list)
    sectors={"L1":["NEARUSDT","ADAUSDT","DOTUSDT","APTUSDT","ICPUSDT","XLMUSDT","TONUSDT"],
             "L2":["ARBUSDT","RENDERUSDT"],"AI":["FETUSDT","TAOUSDT","WLDUSDT"],
             "MEME":["1000PEPEUSDT","1000LUNCUSDT"],"DEFI":["UNIUSDT","RUNEUSDT","PENDLEUSDT"],
             "OTHER":["ZECUSDT","NMRUSDT","SOLUSDT","ENAUSDT","XRPUSDT","FILUSDT","BNBUSDT","CHZUSDT","DASHUSDT","QNTUSDT","ETHUSDT"]}
    s2sec={}
    for sec,syms in sectors.items():
        for s in syms: s2sec[s]=sec

    sig_sorted=sorted(signals,key=lambda s:s["close_time"]); si=0
    all_times=set()
    for sym,c in candles_data.items():
        if c:
            for i in range(c["n"]): all_times.add(c["ct"][i])
    sorted_times=sorted(all_times)

    for t_idx,ct in enumerate(sorted_times):
        for sym,c in candles_data.items():
            if c is None: continue
            idx=np.searchsorted(c["ct"],ct)
            if idx>=c["n"] or c["ct"][idx]!=ct: continue

            if sym in open_pos:
                pos=open_pos[sym]; pos["bars"]+=1
                price=c["c"][idx]; hi=c["h"][idx]; lo=c["l"][idx]
                if hi>pos["bp"]: pos["bp"]=hi
                sd=pos["sd"]
                cr=(price-pos["ep"])/sd if sd>0 else 0
                lr=(lo-pos["ep"])/sd if sd>0 else 0
                hr=(hi-pos["ep"])/sd if sd>0 else 0
                if lr<pos["mae"]: pos["mae"]=lr
                if hr>pos["mfe"]: pos["mfe"]=hr

                aw=min(50,idx+1)
                if aw>1: atr_v=_atr(c["h"][idx-aw+1:idx+1],c["l"][idx-aw+1:idx+1],c["c"][idx-aw+1:idx+1],CFG.atr_period)
                else: atr_v=0

                er=None; ep_exit=None
                sp=pos["ep"]-sd
                if lo<=sp: er="stop_loss"; ep_exit=sp
                elif not pos["pt"] and hr>=CFG.partial_r:
                    pos["pt"]=True; pos["qty"]*=(1-CFG.partial_fraction)
                    pos["rpnl"]+=(hi-pos["ep"])*pos["orig_qty"]*CFG.partial_fraction
                if er is None and atr_v>0:
                    nvt=price-atr_v*CFG.vol_trail_atr
                    if nvt>pos["vt"]: pos["vt"]=nvt
                    if pos["vt"]>pos["ep"] and lo<=pos["vt"]: er="vol_trail"; ep_exit=pos["vt"]
                if er is None and idx>=CFG.struct_lookback:
                    sw=min(c["l"][idx-CFG.struct_lookback+1:idx+1])
                    if sw>pos["st"]: pos["st"]=sw
                    if pos["st"]>pos["ep"] and lo<=pos["st"]: er="struct_trail"; ep_exit=pos["st"]
                if er is None and pos["bars"]>6 and cr>0.5:
                    pr=(pos["bp"]-pos["ep"])/sd if sd>0 else 0
                    if pr>0 and (cr/pr)<(1-CFG.decay_threshold): er="expansion_decay"; ep_exit=price
                if er is None and pos["bars"]>=CFG.max_hold_bars: er="time_stop"; ep_exit=price

                if er:
                    fs=ep_exit*(1-SLIP_BPS/10000); fee=pos["qty"]*fs*TAKER_BPS/10000
                    pnl=(fs-pos["ep"])*pos["qty"]; equity+=pnl-fee
                    if equity>peak: peak=equity
                    pr=(ep_exit-pos["ep"])/sd if sd>0 else 0
                    closed.append({"uuid":pos["uuid"],"symbol":sym,"side":"LONG","entry_time":pos["et"],
                                   "exit_time":datetime.fromtimestamp(ct/1000,tz=timezone.utc).isoformat(),
                                   "entry_price":round(pos["ep"],6),"exit_price":round(fs,6),
                                   "quantity":round(pos["orig_qty"],6),"leverage":pos["lev"],
                                   "stop_dist":round(sd,6),"pnl_usd":round(pnl-fee+pos.get("rpnl",0),4),
                                   "pnl_r":round(pr,4),"fees":round(pos.get("fees",0)+fee,4),
                                   "hold":pos["bars"],"exit_reason":er,"tp1":1 if pos["pt"] else 0,
                                   "equity_after":round(equity,2),"btc_aligned":pos.get("ba",0),
                                   "conf":str(pos.get("conf","")),"conf_count":pos.get("cc",0),
                                   "mae":round(pos["mae"],4),"mfe":round(pos["mfe"],4),
                                   "quality":pos.get("qs",0),"is_ny":pos.get("ny",False),
                                   "pyramid_adds":0,"sector":s2sec.get(sym,"OTHER"),"label":config.get("label","")})
                    del open_pos[sym]

        if len(open_pos)>=MAX_POS: continue

        while si<len(sig_sorted) and sig_sorted[si]["close_time"]<=ct:
            sig=sig_sorted[si]; sym=sig["symbol"]
            if sym not in open_pos and len(open_pos)<MAX_POS:
                if config.get("use_correlation"):
                    sec=s2sec.get(sym,"OTHER"); sc=sum(1 for p in open_pos.values() if p.get("sector")==sec)
                    if sc>=config.get("corr_cap",3): si+=1; continue

                qs=quality_score(sig) if config.get("use_quality") else 0.5
                rp=BTC_ALIGNED_RISK if sig.get("btc_aligned") else BASE_RISK

                if config.get("use_vol_target") and len(atrh[sym])>=20:
                    ma=np.median(atrh[sym][-100:]); ca=sig.get("atr",0)
                    if ca>0: vr=ma/ca; vr=max(0.5,min(2.0,vr)); rp*=vr

                if config.get("use_quality"): rp*=(0.25+qs*1.5)

                ny_b=1.0
                if is_ny(sig["close_time"]):
                    ny_b=1.5
                    if config.get("use_pyramid"): ny_b=2.0

                if config.get("use_regime") and len(atrh[sym])>=20:
                    ma=np.median(atrh[sym][-100:]); ca=sig.get("atr",0)
                    if ma>0 and ca>0:
                        rr=ca/ma
                        if rr>1.5: ny_b*=min(1.5,rr/1.5)
                        elif rr<0.5: ny_b*=0.5

                rp*=ny_b; rp=min(rp,8.0)
                sd=sig["risk_distance"]
                if sd<=0: si+=1; continue

                ra=equity*(rp/100); qty=ra/sd
                fill=sig["entry_price"]*(1+SLIP_BPS/10000)
                fee=qty*fill*TAKER_BPS/10000; equity-=fee
                notional=qty*sig["entry_price"]
                lev=min(int(notional/equity)+1,MAX_LEV); lev=max(lev,1)
                mn=equity*lev*0.95
                if notional>mn: qty=mn/sig["entry_price"]
                if qty<=0: si+=1; continue

                open_pos[sym]={"uuid":sig["uuid"],"symbol":sym,"ep":fill,"orig_qty":qty,"qty":qty,
                               "lev":lev,"sp":sig["stop_price"],"sd":sd,"pt":False,"bp":fill,
                               "vt":0.0,"st":0.0,"mae":0.0,"mfe":0.0,"bars":0,"rpnl":0.0,"fees":fee,
                               "ba":1 if sig.get("btc_aligned") else 0,"conf":str(sig["confirmations"]),
                               "cc":sig["confirm_count"],"qs":qs,"ny":is_ny(sig["close_time"]),
                               "et":datetime.fromtimestamp(sig["close_time"]/1000,tz=timezone.utc).isoformat(),
                               "sector":s2sec.get(sym,"OTHER")}
                atrh[sym].append(sig.get("atr",0))
            si+=1
    return closed,equity

def compute_metrics(trades,label):
    if not trades: return {"label":label,"trades":0}
    n=len(trades); wins=[t for t in trades if t["pnl_r"]>0]; losses=[t for t in trades if t["pnl_r"]<=0]
    tr=sum(t["pnl_r"] for t in trades); wr=len(wins)/n*100
    gp=sum(t["pnl_r"] for t in wins) if wins else 0; gl=abs(sum(t["pnl_r"] for t in losses)) if losses else 0
    pf=gp/gl if gl>0 else float("inf")
    rv=[t["pnl_r"] for t in trades]
    sh=(np.mean(rv)/np.std(rv)*math.sqrt(252*288)) if len(rv)>1 and np.std(rv)>0 else 0
    ny=[t for t in trades if t.get("is_ny")]; ba=[t for t in trades if t.get("btc_aligned")]
    return {"label":label,"trades":n,"wins":len(wins),"losses":len(losses),"win_rate":round(wr,1),
            "total_r":round(tr,4),"avg_r":round(tr/n,4),"profit_factor":round(pf,3),
            "avg_win_r":round(sum(t["pnl_r"] for t in wins)/len(wins),4) if wins else 0,
            "avg_loss_r":round(sum(t["pnl_r"] for t in losses)/len(losses),4) if losses else 0,
            "sharpe":round(sh,3),"ny_trades":len(ny),
            "ny_wr":round(sum(1 for t in ny if t["pnl_r"]>0)/len(ny)*100,1) if ny else 0,
            "ny_total_r":round(sum(t["pnl_r"] for t in ny),4),
            "btc_trades":len(ba),"btc_wr":round(sum(1 for t in ba if t["pnl_r"]>0)/len(ba)*100,1) if ba else 0,
            "btc_total_r":round(sum(t["pnl_r"] for t in ba),4)}

def save_csv(trades,fp):
    if not trades: return
    fn=list(trades[0].keys())
    with open(fp,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fn); w.writeheader()
        for t in trades: w.writerow(t)

def save_metrics_csv(all_m,fp):
    rows=[{k:v for k,v in m.items() if not isinstance(v,dict)} for m in all_m]
    if rows:
        fn=list(rows[0].keys())
        with open(fp,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fn); w.writeheader()
            for r in rows: w.writerow(r)

def main():
    start=time.time()
    print("V3 COMPREHENSIVE BACKTEST — Real Coinalyze Data")
    print("="*60)

    print("\n[1] Loading data...")
    cd={}
    for sym in [BTC_SYMBOL]+ALL_SYMBOLS:
        cd[sym]=load_5m(sym)
        if cd[sym]: print(f"  {sym}: {cd[sym]['n']} candles")
        else: print(f"  {sym}: NO DATA")

    btc=cd[BTC_SYMBOL]
    ld={}; dc={}
    for sym in ALL_SYMBOLS:
        ld[sym]=load_liq(sym)
        dc[sym]=load_daily(sym)
        if ld[sym]: print(f"  {sym}: {ld[sym]['n']} liq rows")
        else: print(f"  {sym}: NO LIQ DATA")

    print("\n[2] Pre-computing signals...")
    sigs_baseline=[]; sigs_relaxed=[]
    for sym in ALL_SYMBOLS:
        if cd[sym] is None: continue
        s1=precompute_signals(cd[sym],ld[sym],dc[sym],btc)
        sigs_baseline.extend(s1)
        print(f"  {sym}: {len(s1)} signals (baseline)")

    CFG.ret5d_min=-10.0
    for sym in ALL_SYMBOLS:
        if cd[sym] is None: continue
        s2=precompute_signals(cd[sym],ld[sym],dc[sym],btc)
        sigs_relaxed.extend(s2)
        print(f"  {sym}: {len(s2)} signals (relaxed)")

    print(f"\n  Total baseline signals: {len(sigs_baseline)}")
    print(f"  Total relaxed signals: {len(sigs_relaxed)}")

    print("\n[3] Running backtest layers...")
    all_metrics=[]
    layers=[
        ("baseline",{"ret5d":-5.0,"use_quality":False,"use_vol":False,"use_regime":False,"use_pyramid":False,"use_corr":False}),
        ("relax_ret5d",{"ret5d":-10.0,"use_quality":False,"use_vol":False,"use_regime":False,"use_pyramid":False,"use_corr":False}),
        ("quality_scoring",{"ret5d":-10.0,"use_quality":True,"use_vol":False,"use_regime":False,"use_pyramid":False,"use_corr":False}),
        ("vol_targeting",{"ret5d":-10.0,"use_quality":True,"use_vol":True,"use_regime":False,"use_pyramid":False,"use_corr":False}),
        ("regime_sizing",{"ret5d":-10.0,"use_quality":True,"use_vol":True,"use_regime":True,"use_pyramid":False,"use_corr":False}),
        ("pyramiding",{"ret5d":-10.0,"use_quality":True,"use_vol":True,"use_regime":True,"use_pyramid":True,"use_corr":False}),
        ("correlation_control",{"ret5d":-10.0,"use_quality":True,"use_vol":True,"use_regime":True,"use_pyramid":True,"use_corr":True}),
    ]

    for name,lc in layers:
        CFG.ret5d_min=lc["ret5d"]
        sigs=sigs_baseline if lc["ret5d"]==-5.0 else sigs_relaxed
        config={"label":name,"use_quality":lc["use_quality"],"use_vol_target":lc["use_vol"],
                "use_regime":lc["use_regime"],"use_pyramid":lc["use_pyramid"],
                "use_correlation":lc["use_corr"],"corr_cap":3}
        print(f"\n--- {name} ---")
        trades,eq=replay(sigs,cd,btc,config)
        m=compute_metrics(trades,name)
        all_metrics.append(m)
        save_csv(trades,OUTPUT_DIR/f"{name}_trades.csv")
        print(f"  {m.get('trades',0)} trades, WR={m.get('win_rate',0)}%, Total R={m.get('total_r',0)}, PF={m.get('profit_factor',0)}")

    print("\n[4] Saving comparison...")
    save_metrics_csv(all_metrics,OUTPUT_DIR/"comparison_report.csv")

    print("\nCOMPARISON SUMMARY")
    print("="*70)
    print(f"{'Layer':<25} {'Trades':>6} {'WR%':>6} {'Total R':>10} {'PF':>6} {'Sharpe':>8}")
    print("-"*70)
    for m in all_metrics:
        print(f"{m['label']:<25} {m.get('trades',0):>6} {m.get('win_rate',0):>6} {m.get('total_r',0):>10} {m.get('profit_factor',0):>6} {m.get('sharpe',0):>8}")

    print(f"\nTotal time: {time.time()-start:.0f}s")
    print(f"Outputs: {OUTPUT_DIR}")

if __name__=="__main__":
    main()
