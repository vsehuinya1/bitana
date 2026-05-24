"""
V3 Dynamic Risk Allocation — Optimized
=======================================
Only computes aggression at signal bars (not all bars).
Layers: baseline → +agg_sizing → +correlation → +exit_rework → +regime
"""
import csv, math, sqlite3, time, uuid, ast
from collections import defaultdict, Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

DATA_DIR = Path("/root/bitana/backtest_data")
OUTPUT_DIR = Path("/root/bitana/backtest_output")
KLINES_DB = DATA_DIR / "klines_5m.db"
LIQ_DB = DATA_DIR / "coinalyze_liq.db"

ALL_SYMBOLS = [
    "NEARUSDT","ZECUSDT","ADAUSDT","WLDUSDT","UNIUSDT","NMRUSDT","PENDLEUSDT",
    "ARBUSDT","RENDERUSDT","RUNEUSDT","FETUSDT","DOTUSDT","TONUSDT","SOLUSDT",
    "1000LUNCUSDT","ENAUSDT","1000PEPEUSDT","XRPUSDT","FILUSDT","BNBUSDT",
    "TAOUSDT","CHZUSDT","DASHUSDT","QNTUSDT","ICPUSDT","XLMUSDT","APTUSDT","ETHUSDT",
]
BTC_SYMBOL = "BTCUSDT"

SECTORS = {
    "L1":["NEARUSDT","ADAUSDT","DOTUSDT","APTUSDT","ICPUSDT","XLMUSDT","TONUSDT"],
    "L2":["ARBUSDT","RENDERUSDT"],"AI":["FETUSDT","TAOUSDT","WLDUSDT"],
    "MEME":["1000PEPEUSDT","1000LUNCUSDT"],"DEFI":["UNIUSDT","RUNEUSDT","PENDLEUSDT"],
    "OTHER":["ZECUSDT","NMRUSDT","SOLUSDT","ENAUSDT","XRPUSDT","FILUSDT","BNBUSDT","CHZUSDT","DASHUSDT","QNTUSDT","ETHUSDT"],
}
SYM2SEC = {s:sec for sec,syms in SECTORS.items() for s in syms}

BASE_RISK=2.0; MAX_LEV=10; MAX_POS=10
INIT_EQ=10000.0; TAKER_BPS=4.5; SLIP_BPS=2.0

def load_5m(sym):
    conn=sqlite3.connect(str(KLINES_DB))
    rows=conn.execute("SELECT open_time,close_time,open,high,low,close,volume,taker_buy_volume FROM klines WHERE symbol=? AND open_time>=? AND open_time<=? ORDER BY open_time",(sym,1767225600000,1777593599000)).fetchall()
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

def _atr(h,l,c,p):
    if len(h)<2: return h[0]-l[0] if len(h) else 0.0
    tr=np.empty(len(h)); tr[0]=h[0]-l[0]
    for i in range(1,len(h)): tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    a=2.0/(p+1); e=tr[0]
    for v in tr[1:]: e=a*v+(1-a)*e
    return e

def compute_aggression_at_bar(candles, liq_data, daily_closes, i):
    """Compute aggression score at a specific bar. Returns (score, vars_dict)."""
    n=candles["n"]
    if i<60 or i>=n: return 0.5,{}
    o,h,l,c,v,tbv=candles["o"],candles["h"],candles["l"],candles["c"],candles["v"],candles["tbv"]

    # 1. Taker imb z-score
    if tbv[i]>0 and v[i]>0:
        ir=(tbv[i]-(v[i]-tbv[i]))/v[i]
        iw=[(tbv[j]-(v[j]-tbv[j]))/v[j] for j in range(max(0,i-100),i) if tbv[j]>0 and v[j]>0]
        imb_z=(ir-np.mean(iw))/max(np.std(iw),1e-12) if len(iw)>10 else 0.0
    else: imb_z=0.0

    # 2. Delta persistence
    dp=0
    for j in range(i,max(i-10,-1),-1):
        if c[j]>o[j]:
            if j==i or dp>=0: dp+=1
            else: break
        elif c[j]<o[j]:
            if j==i or dp<=0: dp-=1
            else: break
        else: break

    # 3. Range expansion pctile
    cr=h[i]-l[i]; hr=h[max(0,i-60):i]-l[max(0,i-60):i]
    rp=float(np.sum(hr<=cr)/len(hr)*100) if len(hr)>10 else 50.0

    # 4. Volume concentration
    s=max(0,i-20); tv=float(np.sum(v[s:i+1]))
    if tv>0:
        dv=sum(float(v[j]) for j in range(s,i+1) if c[j]>o[j]) if c[i]>o[i] else sum(float(v[j]) for j in range(s,i+1) if c[j]<o[j])
        vc=dv/tv*100
    else: vc=50.0

    # 5. CLV
    br=h[i]-l[i]; clv=float((c[i]-l[i])/br) if br>0 else 0.5

    # 6. Wick rejection
    bt=max(o[i],c[i]); bb=min(o[i],c[i]); uw=h[i]-bt; lw=bb-l[i]
    wr=float(lw/br if br>0 and c[i]>o[i] else (-uw/br if br>0 else 0.0))

    # 7. Velocity
    vel=float((c[i]-c[i-5])/c[i-5]*100) if i>=5 and c[i-5]>0 else 0.0

    # 8. Cascade intensity
    ci=1.0
    if liq_data and liq_data["n"]>0:
        bd=datetime.fromtimestamp(candles["ot"][i]/1000,tz=timezone.utc).strftime("%Y-%m-%d")
        for j in range(liq_data["n"]):
            ld=datetime.fromtimestamp(liq_data["t"][j],tz=timezone.utc).strftime("%Y-%m-%d")
            if ld==bd:
                tl=liq_data["ll"][j]+liq_data["sl"][j]
                rl=[liq_data["ll"][k]+liq_data["sl"][k] for k in range(max(0,j-30),j+1)]
                ci=tl/np.mean(rl) if np.mean(rl)>0 else 1.0
                break

    # 9. OI acceleration
    if i>=10:
        pc=(c[i]-c[i-10])/c[i-10] if c[i-10]>0 else 0
        vc2=(np.mean(v[max(0,i-5):i+1])-np.mean(v[max(0,i-10):i-5]))/max(np.mean(v[max(0,i-10):i-5]),1e-12)
        oia=pc*vc2*1000
    else: oia=0.0

    # 10. Spread expansion
    se=float(abs(uw-lw)/br) if br>0 else 0.0

    # Normalize and composite
    def nz(v,m="z"):
        if m=="z": return max(-1,min(1,v/3))
        elif m=="p": return v/100
        else: return 2/(1+math.exp(-v))-1

    scores={"t":nz(imb_z,"z"),"d":nz(dp,"s"),"r":nz(rp,"p"),"v":nz(vc,"p"),"c":clv,"w":nz(wr,"r"),"vel":nz(vel,"s"),"cas":nz(ci-1,"s"),"oi":nz(oia,"z"),"s":nz(se,"p")}
    weights={"t":0.15,"d":0.10,"r":0.10,"v":0.10,"c":0.10,"w":0.05,"vel":0.10,"cas":0.15,"oi":0.10,"s":0.05}
    comp=sum(scores[k]*weights[k] for k in weights)
    score=(comp+1)/2
    return round(score,4), {"imb_z":imb_z,"dp":dp,"rp":rp,"vc":vc,"clv":clv,"wr":wr,"vel":vel,"ci":ci,"oia":oia,"se":se}

def agg_risk_mult(decile):
    if decile<=2: return 0.5
    elif decile<=6: return 1.0
    elif decile<=8: return 1.5
    else: return 0.75

def compute_regime(btc_candles, bar_time_ms):
    """Returns regime score [0,1]. 1=fully permissive."""
    if btc_candles is None or btc_candles["n"]<60: return 1.0
    idx=np.searchsorted(btc_candles["ct"],bar_time_ms)
    if idx>=btc_candles["n"]: idx=btc_candles["n"]-1
    if idx<60: return 1.0
    bc=btc_candles["c"]
    def ema(v,s):
        if len(v)<s: return v[-1]
        a=2/(s+1); e=v[0]
        for x in v[1:]: e=a*x+(1-a)*e
        return e
    e50=ema(bc[:idx+1],50); e100=ema(bc[:idx+1],100); e200=ema(bc[:idx+1],200)
    if e50>e100>e200: bt=1.0
    elif e50>e100: bt=0.75
    elif e50<e100<e200: bt=0.25
    else: bt=0.5
    # Vol regime
    if idx>=60*288:
        dr=np.diff(np.log(bc[idx-20*288:idx+1]))
        lr=np.diff(np.log(bc[idx-60*288:idx+1]))
        rv=np.std(dr)*np.sqrt(288)/max(np.std(lr)*np.sqrt(288),1e-12)
        vol=0.75 if rv>1.5 else (1.0 if rv>1.0 else (0.75 if rv>0.5 else 0.5))
    else: vol=1.0
    return round(bt*0.5+vol*0.5,4)

def check_exit(pos, candles, i, use_v2, agg_score):
    """Exit engine. use_v2=True for revised exits."""
    sd=pos["sd"]
    if sd<=0: return None,None
    price=candles["c"][i]; hi=candles["h"][i]; lo=candles["l"][i]
    pos["bars"]+=1
    if hi>pos["bp"]: pos["bp"]=hi
    cr=(price-pos["ep"])/sd; lr=(lo-pos["ep"])/sd; hr=(hi-pos["ep"])/sd
    if lr<pos["mae"]: pos["mae"]=lr
    if hr>pos["mfe"]: pos["mfe"]=hr
    aw=min(50,i+1)
    atr_v=_atr(candles["h"][i-aw+1:i+1],candles["l"][i-aw+1:i+1],candles["c"][i-aw+1:i+1],14) if aw>1 else 0

    # Stop loss
    if lo<=pos["ep"]-sd: return "stop_loss",pos["ep"]-sd

    # Partial TP at 2R
    if not pos["pt"] and hr>=2.0:
        pos["pt"]=True; pos["qty"]*=0.5
        pos["rpnl"]+=(hi-pos["ep"])*pos["orig_qty"]*0.5

    # Vol trail
    tm=2.5 if (use_v2 and agg_score>0.8) else 2.0
    if atr_v>0:
        nvt=price-atr_v*tm
        if nvt>pos["vt"]: pos["vt"]=nvt
        if pos["vt"]>pos["ep"] and lo<=pos["vt"]: return "vol_trail",pos["vt"]

    # Struct trail
    if i>=12:
        sw=np.min(candles["l"][i-12+1:i+1])
        if sw>pos["st"]: pos["st"]=sw
        if pos["st"]>pos["ep"] and lo<=pos["st"]: return "struct_trail",pos["st"]

    # Expansion decay
    if use_v2:
        # Revised: require 12+ bars, 1.5R+ peak, 40%+ pullback, 3+ red closes
        if pos["bars"]>=12 and cr>0:
            pr=(pos["bp"]-pos["ep"])/sd
            if pr>1.5 and (cr/pr)<0.6:
                rc=sum(1 for j in range(i,max(i-5,-1),-1) if candles["c"][j]<candles["o"][j])
                if rc>=3: return "expansion_decay",price
    else:
        # Original: simpler threshold
        if pos["bars"]>6 and cr>0.5:
            pr=(pos["bp"]-pos["ep"])/sd
            if pr>0 and (cr/pr)<0.7: return "expansion_decay",price

    # Time stop
    if pos["bars"]>=288: return "time_stop",price
    return None,None

def run_backtest(config):
    label=config["label"]
    use_agg=config.get("use_agg",False)
    use_corr=config.get("use_corr",False)
    use_exit_v2=config.get("use_exit_v2",False)
    use_regime=config.get("use_regime",False)
    print(f"\n{label}: agg={use_agg} corr={use_corr} exit_v2={use_exit_v2} regime={use_regime}")

    # Load data
    cd={}; ld={}; dd={}
    for sym in [BTC_SYMBOL]+ALL_SYMBOLS:
        cd[sym]=load_5m(sym); ld[sym]=load_liq(sym); dd[sym]=load_daily(sym)
    btc=cd[BTC_SYMBOL]

    # Find all signal bars per symbol
    print("  Finding signals...")
    all_signals=[]
    for sym in ALL_SYMBOLS:
        c=cd[sym]
        if c is None: continue
        liq=ld[sym]; daily=dd[sym]
        n=c["n"]; cc=c["c"]; hh=c["h"]; ll=c["l"]; vv=c["v"]; tbv=c["tbv"]; oo=c["o"]

        # Cascade state
        ca_arr=np.zeros(n,dtype=bool); cs_arr=np.zeros(n)
        if liq and liq["n"]>0:
            ch=deque(maxlen=100); liq_idx=0; cur_ca=False
            for i in range(n):
                bd=datetime.fromtimestamp(c["ot"][i]/1000,tz=timezone.utc).strftime("%Y-%m-%d")
                while liq_idx<liq["n"]:
                    ld2=datetime.fromtimestamp(liq["t"][liq_idx],tz=timezone.utc)
                    if ld2.strftime("%Y-%m-%d")<=bd:
                        cl=daily.get(bd,cc[i]) if daily else cc[i]
                        ch.append({"tl":liq["ll"][liq_idx]+liq["sl"][liq_idx],"ll":liq["ll"][liq_idx],"sl":liq["sl"][liq_idx],"cl":cl})
                        liq_idx+=1
                    else: break
                if i==0 or datetime.fromtimestamp(c["ot"][i-1]/1000,tz=timezone.utc).strftime("%Y-%m-%d")!=bd:
                    if len(ch)>0:
                        liqs=[r["tl"] for r in ch]
                        p90=np.percentile(liqs[-90:],90) if len(liqs)>=30 else 0
                        spike=any(liqs[-(j+1)]>p90 for j in range(min(3,len(liqs)))) if p90>0 else False
                        last=ch[-1]; total=last["tl"]
                        imb=(last["ll"]-last["sl"])/total if total>0 else 0
                        ch2=[r["cl"] for r in ch]
                        r5=((ch2[-1]/ch2[-6])-1)*100 if len(ch2)>=6 and ch2[-6]>0 else 0
                        cur_ca=spike and imb<0 and r5>-5.0
                ca_arr[i]=cur_ca
                if len(ch)>0:
                    liqs2=[r["tl"] for r in ch]
                    p2=np.percentile(liqs2[-90:],90) if len(liqs2)>=30 else 1
                    cs_arr[i]=liqs2[-1]/p2 if p2>0 else 0

        # Indicators
        tr=np.maximum(hh[1:]-ll[1:],np.maximum(np.abs(hh[1:]-cc[:-1]),np.abs(ll[1:]-cc[:-1])))
        atr=np.zeros(n); atr[0]=tr[0] if len(tr)>0 else 0
        for i in range(1,n): atr[i]=(2/15)*tr[min(i-1,len(tr)-1)]+(13/15)*atr[i-1]

        ema20=np.zeros(n); ema20[0]=cc[0]
        for i in range(1,n): ema20[i]=(2/21)*cc[i]+(19/21)*ema20[i-1]

        rh=np.zeros(n)
        for i in range(61,n): rh[i]=np.max(hh[i-60:i-1])

        vz=np.zeros(n)
        for i in range(100,n):
            w=vv[i-100:i]; s=np.std(w)
            vz[i]=(vv[i]-np.mean(w))/s if s>1e-12 else 0

        has_taker=tbv[-1]>0
        iz=np.zeros(n)
        if has_taker:
            safe=np.where(vv>0,vv,1.0); ir=(tbv-(vv-tbv))/safe
            for i in range(100,n):
                w=ir[i-100:i]; s=np.std(w)
                iz[i]=(ir[i]-np.mean(w))/s if s>1e-12 else 0

        cr=hh-ll; cb=np.abs(cc-oo)
        bs=np.where(cr>0,cb/cr,0)
        br=np.where(oo>0,(cc-oo)/oo*100,0)

        # Find signals
        cd2=0; stopped=False
        for i in range(200,n):
            if cd2>0: cd2-=1; continue
            if stopped: continue
            if not ca_arr[i]: continue

            conf={"breakout":bool(cc[i]>rh[i]),"imb":bool(iz[i]>2.0) if has_taker else False,
                  "vol":bool(vz[i]>3.0),"body":bool(bs[i]>0.6),"impulse":bool(br[i]>0.3),"momentum":bool(cc[i]>ema20[i])}
            cc_count=sum(1 for v2 in conf.values() if v2)
            if cc_count<4: continue

            ep=cc[i]; sd=atr[i]*2.5; sp=ep-sd
            if sd<=0: continue

            # Compute aggression ONLY at signal bars
            agg_score,vars_dict=compute_aggression_at_bar(c,liq,daily,i)

            all_signals.append({"uuid":str(uuid.uuid4()),"symbol":sym,"bar_idx":i,
                                "entry_price":ep,"stop_price":sp,"risk_distance":sd,
                                "atr":atr[i],"conf":str(conf),"conf_count":cc_count,
                                "cascade_strength":cs_arr[i],"close_time":int(c["ct"][i]),
                                "aggression_score":agg_score})
            cd2=36

    print(f"  Signals: {len(all_signals)}")

    # Compute aggression decile thresholds
    all_scores=[s["aggression_score"] for s in all_signals]
    percentiles=np.percentile(all_scores,[10,20,30,40,50,60,70,80,90]) if all_scores else [0.5]*9
    for s in all_signals:
        s["aggression_decile"]=get_decile(s["aggression_score"],percentiles)

    all_signals.sort(key=lambda s:s["close_time"])

    # Replay
    print("  Replaying...")
    equity=10000.0; peak_eq=equity
    open_pos={}; closed=[]; si=0

    all_times=set()
    for sym,c in cd.items():
        if c:
            for i in range(c["n"]): all_times.add(c["ct"][i])
    sorted_times=sorted(all_times)

    # Pre-compute regime daily
    regime_cache={}
    for t in sorted_times:
        day=t//86400000*86400000
        if day not in regime_cache:
            regime_cache[day]=compute_regime(btc,t)

    for t_idx,ct in enumerate(sorted_times):
        day=ct//86400000*86400000
        regime_score=regime_cache.get(day,1.0) if use_regime else 1.0

        # Manage positions
        for sym in list(open_pos.keys()):
            pos=open_pos[sym]; c=cd[sym]
            idx=np.searchsorted(c["ct"],ct)
            if idx>=c["n"] or c["ct"][idx]!=ct: continue

            er,ep_exit=check_exit(pos,c,idx,use_exit_v2,pos.get("agg_score",0.5))
            if er:
                fs=ep_exit*(1-SLIP_BPS/10000); fee=pos["qty"]*fs*TAKER_BPS/10000
                pnl=(fs-pos["ep"])*pos["qty"]; equity+=pnl-fee
                if equity>peak_eq: peak_eq=equity
                pr=(ep_exit-pos["ep"])/pos["sd"] if pos["sd"]>0 else 0
                net_pnl=pnl-fee+pos.get("rpnl",0)
                closed.append({"uuid":pos["uuid"],"symbol":sym,"side":"LONG",
                    "entry_time":pos["et"],"exit_time":datetime.fromtimestamp(ct/1000,tz=timezone.utc).isoformat(),
                    "entry_price":round(pos["ep"],6),"exit_price":round(fs,6),
                    "quantity":round(pos["orig_qty"],6),"leverage":pos["lev"],
                    "stop_dist":round(pos["sd"],6),"pnl_usd":round(net_pnl,4),
                    "pnl_r":round(pr,4),"fees":round(pos.get("fees",0)+fee,4),
                    "hold":pos["bars"],"exit_reason":er,"tp1":1 if pos["pt"] else 0,
                    "equity_after":round(equity,2),"conf":pos.get("conf",""),
                    "conf_count":pos.get("cc",0),"mae":round(pos["mae"],4),"mfe":round(pos["mfe"],4),
                    "aggression_score":pos.get("agg_score",0),"aggression_decile":pos.get("agg_decile",5),
                    "sector":SYM2SEC.get(sym,"OTHER"),"regime_score":regime_score,"label":label})
                del open_pos[sym]

        if len(open_pos)>=MAX_POS: continue

        # New signals
        while si<len(all_signals) and all_signals[si]["close_time"]<=ct:
            sig=all_signals[si]; sym=sig["symbol"]
            if sym not in open_pos and len(open_pos)<MAX_POS:
                sec=SYM2SEC.get(sym,"OTHER")

                # Correlation control
                if use_corr:
                    sc=sum(1 for p in open_pos.values() if p.get("sector")==sec)
                    if sc>=3: si+=1; continue
                    tr=sum(p["sd"]*p["qty"] for p in open_pos.values())
                    if tr>equity*0.15: si+=1; continue

                # Risk sizing
                agg_decile=sig["aggression_decile"]
                if use_agg:
                    risk_pct=BASE_RISK*agg_risk_mult(agg_decile)*regime_score
                else:
                    risk_pct=BASE_RISK*regime_score
                risk_pct=max(0.5,min(risk_pct,6.0))

                sd=sig["risk_distance"]
                if sd<=0: si+=1; continue
                ra=equity*(risk_pct/100); qty=ra/sd
                fill=sig["entry_price"]*(1+SLIP_BPS/10000)
                fee=qty*fill*TAKER_BPS/10000; equity-=fee
                notional=qty*sig["entry_price"]
                lev=min(int(notional/equity)+1,MAX_LEV); lev=max(lev,1)
                mn=equity*lev*0.95
                if notional>mn: qty=mn/sig["entry_price"]
                if qty<=0: si+=1; continue

                open_pos[sym]={"uuid":sig["uuid"],"symbol":sym,"ep":fill,
                    "orig_qty":qty,"qty":qty,"lev":lev,"sp":sig["stop_price"],"sd":sd,
                    "pt":False,"bp":fill,"vt":0.0,"st":0.0,"mae":0.0,"mfe":0.0,
                    "bars":0,"rpnl":0.0,"fees":fee,"conf":sig["conf"],"cc":sig["conf_count"],
                    "agg_score":sig["aggression_score"],"agg_decile":agg_decile,
                    "sector":sec,"et":datetime.fromtimestamp(sig["close_time"]/1000,tz=timezone.utc).isoformat()}
            si+=1

    print(f"  Done: {len(closed)} trades, ${equity:.2f}")
    return closed,equity

def get_decile(score, pcts):
    for i,p in enumerate(pcts):
        if score<=p: return i+1
    return 10

def compute_metrics(trades,label):
    if not trades: return {"label":label,"trades":0}
    n=len(trades); wins=[t for t in trades if float(t["pnl_r"])>0]
    losses=[t for t in trades if float(t["pnl_r"])<=0]
    tr=sum(float(t["pnl_r"]) for t in trades)
    gp=sum(float(t["pnl_r"]) for t in wins) if wins else 0
    gl=abs(sum(float(t["pnl_r"]) for t in losses)) if losses else 0
    pf=gp/gl if gl>0 else float("inf"); wr=len(wins)/n*100
    rv=[float(t["pnl_r"]) for t in trades]
    sh=(np.mean(rv)/np.std(rv)*math.sqrt(252*288/max(n,1))) if len(rv)>1 and np.std(rv)>0 else 0
    by_d=defaultdict(lambda:{"n":0,"r":0})
    for t in trades:
        d=t.get("aggression_decile",5); by_d[d]["n"]+=1; by_d[d]["r"]+=float(t["pnl_r"])
    ds=" | ".join(f"D{d}:{by_d[d]['n']}t/{by_d[d]['r']:+.1f}R" for d in range(1,11) if by_d[d]["n"]>0)
    return {"label":label,"trades":n,"wins":len(wins),"losses":len(losses),
            "win_rate":round(wr,1),"total_r":round(tr,4),"avg_r":round(tr/n,4),
            "profit_factor":round(pf,3),"sharpe":round(sh,3),
            "final_equity":round(10000+sum(float(t["pnl_usd"]) for t in trades),2),"deciles":ds}

def save_csv(trades,fp):
    if not trades: return
    fn=list(trades[0].keys())
    with open(fp,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fn); w.writeheader()
        for t in trades: w.writerow(t)

def main():
    print("V3 DYNAMIC RISK ALLOCATION")
    print("="*60)
    layers=[
        ("baseline",{"use_agg":False,"use_corr":False,"use_exit_v2":False,"use_regime":False}),
        ("agg_sizing",{"use_agg":True,"use_corr":False,"use_exit_v2":False,"use_regime":False}),
        ("agg_correlation",{"use_agg":True,"use_corr":True,"use_exit_v2":False,"use_regime":False}),
        ("agg_exit_v2",{"use_agg":True,"use_corr":True,"use_exit_v2":True,"use_regime":False}),
        ("full_stack",{"use_agg":True,"use_corr":True,"use_exit_v2":True,"use_regime":True}),
    ]
    all_metrics=[]
    for name,lc in layers:
        t0=time.time()
        trades,eq=run_backtest({"label":name,**lc})
        m=compute_metrics(trades,name)
        all_metrics.append(m)
        save_csv(trades,OUTPUT_DIR/f"final_{name}_trades.csv")
        print(f"  Time: {time.time()-t0:.0f}s\n")

    print("\nCOMPARISON")
    print("="*70)
    h="{:<20} {:>6} {:>6} {:>10} {:>6} {:>8} {:>12}".format("Layer","Trades","WR%","Total R","PF","Sharpe","Final Eq")
    print(h); print("-"*70)
    for m in all_metrics:
        print("{:<20} {:>6} {:>6} {:>+10.2f} {:>6} {:>8} ${:>11,.2f}".format(
            m["label"],m.get("trades",0),m.get("win_rate",0),m.get("total_r",0),
            m.get("profit_factor",0),m.get("sharpe",0),m.get("final_equity",0)))
    print("\nDecile breakdown:")
    for m in all_metrics:
        if m.get("deciles"):
            print(f"  {m['label']}: {m['deciles']}")
    print(f"\nOutputs: {OUTPUT_DIR}")

if __name__=="__main__":
    main()
