# Capture public futures bookTicker stream (market data only, no account calls) for N seconds.
import asyncio, json, sys, time, websockets
SY = sys.argv[1].split(','); DUR = int(sys.argv[2]); OUT = sys.argv[3]
async def main():
    url = 'wss://fstream.binance.com/stream?streams=' + '/'.join(s.lower() + '@bookTicker' for s in SY)
    t_end = time.time() + DUR
    with open(OUT, 'w') as f:
        async with websockets.connect(url, max_size=None) as ws:
            while time.time() < t_end:
                d = json.loads(await ws.recv())['data']
                f.write(f"{d['s']},{d['T']},{d['b']},{d['B']},{d['a']},{d['A']}\n")
asyncio.run(main())
