#!/usr/bin/env python
"""QMT xttrader connection diagnostics — find correct port and session."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtquant import xttrader, xtdata
import os, glob, json

print("=" * 60)
print("QMT xttrader Connection Diagnostics")
print("=" * 60)

# 1. Check running QMT processes
print("\n[1] Running QMT processes:")
import subprocess
try:
    out = subprocess.check_output(
        'powershell -Command "Get-Process | Where-Object {$_.ProcessName -like \'*qmt*\' -or $_.ProcessName -like \'*xt*\'} | Select-Object ProcessName, Id, MainWindowTitle"',
        shell=True, text=True, timeout=10
    )
    print(out if out.strip() else "  (none found)")
except Exception as e:
    print(f"  check failed: {e}")

# 2. Check QMT installation paths
print("\n[2] QMT installation paths:")
candidates = [
    r"D:\国金证券QMT交易端",
    r"D:\QMT",
    r"C:\国金证券QMT交易端",
    r"C:\QMT",
    r"D:\Program Files\QMT",
    r"C:\Program Files\QMT",
]
for p in candidates:
    if os.path.exists(p):
        print(f"  EXISTS: {p}")
        # List subdirs
        subdirs = [d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))]
        print(f"    subdirs: {subdirs}")
        # Check for config files
        for root, dirs, files in os.walk(p):
            for f in files:
                if f in ("config.ini", "xtconfig.xml", "qmt.cfg", "QMT.ini", "settings.ini"):
                    fpath = os.path.join(root, f)
                    print(f"    config: {fpath}")
                    try:
                        with open(fpath, 'r', encoding='gbk', errors='ignore') as cf:
                            content = cf.read()
                            # Look for port numbers
                            import re
                            ports = re.findall(r'(?:port|Port|PORT)[=:]\s*(\d+)', content)
                            if ports:
                                print(f"    PORTS found: {ports}")
                    except:
                        pass

# 3. Check xtquant's own config
print("\n[3] xtquant module location:")
for m in [xttrader, xtdata]:
    if m and hasattr(m, '__file__'):
        mod_path = Path(m.__file__)
        print(f"  {m.__name__}: {mod_path}")
        # Look for config in same directory
        cfg_dir = mod_path.parent
        for f in cfg_dir.glob("*"):
            if f.suffix in ('.ini', '.cfg', '.xml', '.json'):
                print(f"    config file: {f}")

# 4. Try to detect xttrader port from xtdata config
print("\n[4] xtdata connection info:")
try:
    # xtdata might expose its server address
    data_dir = Path(xtdata.__file__).parent if hasattr(xtdata, '__file__') else None
    print(f"  xtdata dir: {data_dir}")
except Exception as e:
    print(f"  {e}")

# 5. Enumerate xttrader session options
print("\n[5] Testing xttrader.connect() with various params:")
print("  xttrader.connect() signature help:")
import inspect
try:
    sig = inspect.signature(xttrader.connect)
    print(f"  connect{sig}")
except:
    print("  can't inspect — trying docstring:")
    print(f"  {xttrader.connect.__doc__[:500] if xttrader.connect.__doc__ else 'no doc'}")

# 6. Search userdata paths
print("\n[6] Searching for QMT userdata:")
search_roots = ["C:\\", "D:\\"]
for root in search_roots:
    try:
        for d in os.listdir(root):
            full = os.path.join(root, d)
            if os.path.isdir(full) and ('qmt' in d.lower() or '国金' in d or 'guojin' in d.lower()):
                print(f"  Found: {full}")
                # Walk 2 levels for userdata
                for rt, dirs, files in os.walk(full):
                    depth = rt.replace(full, '').count(os.sep)
                    if depth > 2:
                        dirs.clear()
                        continue
                    for dr in dirs:
                        if 'user' in dr.lower():
                            ud = os.path.join(rt, dr)
                            print(f"    userdata: {ud}")
    except PermissionError:
        pass

# 7. Try connect with different approaches
print("\n[7] Attempting connection with discovered info:")

# First — figure out the exact xttrader connect API
# Some versions use path + session_id, others might accept port as well
test_pairs = []

# Common QMT install roots
for base in [r"D:", r"C:"]:
    for sub in [r"\国金证券QMT交易端", r"\QMT", r"\Program Files\QMT", r"\guojin_qmt", r"\GJZQ\QMT"]:
        p = base + sub
        if os.path.exists(p):
            test_pairs.append((p, 0))
            test_pairs.append((p, 1))
            test_pairs.append((p, 2))
            # Also check session_id from possible naming
            for s in [6868, 58610, 20000, 20001, 8080]:
                test_pairs.append((p, s))

# Try each
seen = set()
for path, session in test_pairs:
    key = (path, session)
    if key in seen:
        continue
    seen.add(key)
    try:
        result = xttrader.connect(path, session)
        print(f"  connect('{path}', {session}) = {result}", end="")
        if result == 0 or result is True:
            print(" ✅ SUCCESS")
            break
        else:
            print(" ❌")
    except Exception as e:
        print(f"  connect('{path}', {session}) = ERROR: {e}")

print("\n" + "=" * 60)
print("Diagnostics complete. Paste output above to debug.")
print("=" * 60)
