#!/usr/bin/env python
"""QMT xttrader connection test — using correct XtQuantTrader API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtquant import xttrader, xtdata, xttype
import time

print("=" * 60)
print("QMT xttrader Connection Test (Correct API)")
print("=" * 60)

# QMT install path (updated version 2026-07-03)
QMT_PATH = r"D:\国金QMT\国金证券QMT交易端"

# Try both userdata paths
for userdata_dir in ["userdata_mini", "userdata"]:
    path = f"{QMT_PATH}\\{userdata_dir}"
    print(f"\nTrying: {path}")

    for session_id in [0, 1, 2, 6868, 8888, 10000]:
        try:
            class MyCallback(xttrader.XtQuantTraderCallback):
                def on_connected(self):
                    print(f"  ✅ on_connected callback fired!")

                def on_disconnected(self):
                    print(f"  ❌ on_disconnected callback fired!")

                def on_account_status(self, status):
                    print(f"  📊 Account status: account_id={status.account_id}, type={status.account_type}, status={status.status}")

                def on_stock_asset(self, asset):
                    print(f"  💰 Asset: {asset}")

                def on_stock_position(self, position):
                    print(f"  📦 Position: {position.stock_code if hasattr(position, 'stock_code') else position}")

                def on_stock_order(self, order):
                    print(f"  📝 Order: {order.order_id if hasattr(order, 'order_id') else order}")

                def on_stock_trade(self, trade):
                    print(f"  🤝 Trade: {trade}")

                def on_order_error(self, error):
                    print(f"  ⚠️ Order error: {error}")

                def on_cancel_error(self, error):
                    print(f"  ⚠️ Cancel error: {error}")

            cb = MyCallback()
            trader = xttrader.XtQuantTrader(path, session_id, cb)

            print(f"  session={session_id} ... ", end="", flush=True)

            trader.start()  # init + start async client
            time.sleep(1)

            result = trader.connect()
            print(f"connect() = {result}", end="")

            if result == 0:
                print(" ✅ SUCCESS!")

                # Wait for connection callback
                time.sleep(2)

                # Query account info
                try:
                    accounts = trader.query_account_infos()
                    print(f"  📋 Accounts: {accounts}")
                    if accounts and len(accounts) > 0:
                        print(f"  Account detail: {accounts[0]}")
                        for acc in accounts:
                            if hasattr(acc, 'account_id'):
                                print(f"    ID={acc.account_id}, type={acc.account_type}")
                except Exception as e:
                    print(f"  Query accounts error: {e}")

                # Query positions
                try:
                    if accounts and len(accounts) > 0:
                        positions = trader.query_stock_positions(accounts[0])
                        print(f"  📦 Positions: {len(positions) if positions else 0}")
                except Exception as e:
                    print(f"  Query positions error: {e}")

                # Query asset
                try:
                    if accounts and len(accounts) > 0:
                        asset = trader.query_stock_asset(accounts[0])
                        print(f"  💰 Asset: {asset}")
                except Exception as e:
                    print(f"  Query asset error: {e}")

                trader.stop()
                break  # success, no need to try more sessions
            else:
                print(" ❌")
                trader.stop()

        except Exception as e:
            print(f"ERROR: {e}")

print("\n" + "=" * 60)
print("Done.")
