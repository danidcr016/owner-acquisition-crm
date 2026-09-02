"""Run one scale point per process so Chromium/Python memory is fully released."""
import argparse
import json
import os
import resource
import subprocess
import sys
import time


def child(limit):
    os.environ["SCRAPER_MAX_ADS"] = str(limit)
    start = time.monotonic()
    from craigslist_source_optimized import scan, memory_mb
    ads = scan()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps({"target": limit, "processed": len(ads), "phones": sum(bool(a.get("phone")) for a in ads), "phone_rate": round(100 * sum(bool(a.get("phone")) for a in ads) / max(1, len(ads)), 1), "rss_end_mb": round(memory_mb(), 1), "peak_rss_mb": round(peak, 1), "seconds": round(time.monotonic() - start, 1)}))


def parent(points):
    for limit in points:
        env = os.environ.copy()
        env["SCRAPER_MAX_ADS"] = str(limit)
        print(f"\n=== SCALE TEST {limit} ADS ===", flush=True)
        subprocess.run([sys.executable, __file__, "--child", str(limit)], env=env, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=int)
    parser.add_argument("--points", nargs="+", type=int, default=[150, 200, 300])
    args = parser.parse_args()
    child(args.child) if args.child else parent(args.points)
