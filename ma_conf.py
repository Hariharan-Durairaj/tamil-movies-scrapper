"""
ma_conf.py - Movie Automator config helper
Usage:
    python ma_conf.py check             -> prints EXISTS or MISSING
    python ma_conf.py read              -> prints SET commands for each key
    python ma_conf.py write             -> writes db.conf from env vars
"""
import os, sys

conf = os.path.join(os.environ.get("SCRIPT_DIR", "."), "db.conf")
cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "check":
    print("EXISTS" if os.path.exists(conf) else "MISSING")

elif cmd == "read":
    with open(conf) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                print('set "%s=%s"' % (k.strip(), v.strip()))

elif cmd == "write":
    keys = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS", "PG_SUPERPASS"]
    with open(conf, "w") as f:
        for k in keys:
            f.write("%s=%s\n" % (k, os.environ.get(k, "")))
    print("[OK] Config saved to db.conf")
