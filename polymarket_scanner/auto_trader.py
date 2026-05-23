"""
Polymarket Auto-Trader Daemon
==============================
后台常驻进程，定时扫描市场，自动按规则执行交易。

架构:
  定时器(APScheduler) → 扫描(scanner) → 规则过滤 → 浏览器下单(trader_browser) → 日志+通知

用法:
  python auto_trader.py              # 前台运行
  python auto_trader.py --once        # 只跑一次扫描（测试用）
  python auto_trader.py --dry-run     # Dry-run 模式（不实际下单）
"""

import json
import os
import sys
import time
import logging
import subprocess
import signal
import traceback
from datetime import datetime, timedelta
from pathlib import Path

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:
    print("请先安装 apscheduler: pip install apscheduler")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from scanner import fetch_markets, ev_signal, parse_prices, time_urgency

CONFIG_FILE  = BASE_DIR / "auto_config.json"
HISTORY_FILE = BASE_DIR / "trade_history.json"
STATUS_FILE  = BASE_DIR / "daemon_status.json"
PID_FILE     = BASE_DIR / "auto_trader.pid"

# ── Logging ──────────────────────────────────────────
LOG_FILE = BASE_DIR / "auto_trader.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("auto_trader")

# ── Default Config ───────────────────────────────────
DEFAULT_CONFIG = {
    "_comment": "$20 budget conservative settings",
    "enabled": True,
    "scan_interval_min": 5,
    "min_ev_score": 6,
    "min_volume": 50000,
    "max_amount_per_trade": 1.0,
    "max_daily_trades": 3,
    "cooldown_minutes": 60,
    "dry_run": True,
    "urgency_filter": [1, 2],
    "require_dispute_market": False,
    "blacklist": [],
    "telegram": {"bot_token": "", "chat_id": ""},
}

# ── File I/O ─────────────────────────────────────────
def _read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

def _append_json(path, record):
    history = _read_json(path, [])
    history.append(record)
    _write_json(path, history)
    return history

# ── Config ───────────────────────────────────────────
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    saved = _read_json(CONFIG_FILE, {})
    cfg.update(saved)
    return cfg

def save_config(cfg):
    _write_json(CONFIG_FILE, cfg)

# ── Status (for Streamlit to read) ───────────────────
def write_status(status, **extra):
    data = {"status": status, "last_update": datetime.now().isoformat(), **extra}
    _write_json(STATUS_FILE, data)

# ═══════════════════════════════════════════════════════
#  AutoTrader Engine
# ═══════════════════════════════════════════════════════
class AutoTrader:
    def __init__(self, dry_run_override=None):
        self.config = load_config()
        if dry_run_override is not None:
            self.config["dry_run"] = dry_run_override
        self.history = _read_json(HISTORY_FILE, [])
        self.scheduler = BackgroundScheduler()
        self._running = False
        self._trade_count_today = 0
        self._last_trade_at = {}   # slug → datetime
        self._today = datetime.now().date()

    # ── Lifecycle ────────────────────────────────────
    def start(self):
        if self._running:
            logger.warning("Already running")
            return

        # Write PID
        PID_FILE.write_text(str(os.getpid()))

        interval = self.config["scan_interval_min"]
        self.scheduler.add_job(
            self.scan_cycle,
            IntervalTrigger(minutes=interval),
            id="scan_cycle",
            name="Market Scan Cycle",
            misfire_grace_time=30,
        )
        self.scheduler.start()
        self._running = True

        write_status("running",
            interval_min=interval,
            dry_run=self.config["dry_run"],
            min_ev_score=self.config["min_ev_score"],
        )
        logger.info(f"AutoTrader started — interval={interval}min, dry_run={self.config['dry_run']}, min_ev={self.config['min_ev_score']}")

    def stop(self):
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            write_status("stopped")
            if PID_FILE.exists():
                PID_FILE.unlink()
        logger.info("AutoTrader stopped")

    def is_running(self):
        return self._running

    # ── Scan Cycle ───────────────────────────────────
    def scan_cycle(self):
        """一个完整周期: 扫描 → 过滤 → 执行"""
        # Reset daily counter if new day
        now = datetime.now()
        if now.date() != self._today:
            self._trade_count_today = 0
            self._today = now.date()

        logger.info("━━━ Scan Cycle Start ━━━")
        write_status("scanning")

        # 1. Fetch markets (paginate to get ~300)
        try:
            markets = []
            for offset in [0, 100, 200]:
                batch = fetch_markets(limit=100, offset=offset)
                markets.extend(batch)
                if len(batch) < 100:
                    break
            logger.info(f"Fetched {len(markets)} markets")
        except Exception as e:
            logger.error(f"Fetch failed: {e}")
            write_status("error", error=str(e))
            return

        # 2. Calculate EV signals
        signals = []
        for m in markets:
            try:
                prices = parse_prices(m)
                signal = ev_signal(m, prices)
                urgency = time_urgency(m.get("endDate", ""))
                if urgency[0] == 0:  # 已结束
                    continue

                score = signal["score"]
                if score >= self.config["min_ev_score"]:
                    slug = m.get("slug", "")
                    signals.append({
                        "slug": slug,
                        "title": m.get("question", "")[:80],
                        "yes": prices["yes"],
                        "no": prices["no"],
                        "volume": float(m.get("volume24hr") or m.get("volumeNum") or m.get("volume") or 0),
                        "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                        "ev_score": score,
                        "ev_flags": signal["flags"],
                        "ev_summary": signal["summary"],
                        "urgency_level": urgency[0],
                        "urgency_label": urgency[1],
                        "urgency_emoji": urgency[2],
                        "end_date": m.get("endDate", ""),
                    })
            except Exception:
                continue

        signals.sort(key=lambda x: x["ev_score"], reverse=True)

        # 3. Log top signals
        top5 = signals[:5]
        for s in top5:
            logger.info(f"  [{s['ev_score']}] {s['urgency_emoji']} {s['title'][:50]} | YES={s['yes']:.3f} | ${s['volume']:,.0f}")

        # 4. Filter & Execute
        executed = 0
        skipped_reasons = {"cooldown": 0, "volume": 0, "urgency": 0, "blacklist": 0, "limit": 0}

        for sig in signals[:20]:  # only consider top 20 signals
            reason = self._should_skip(sig)
            if reason:
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                continue

            success = self._execute_trade(sig)
            if success:
                executed += 1
            time.sleep(3)  # short pause between trades

        logger.info(f"  Executed: {executed} | Skipped: {sum(skipped_reasons.values())} ({skipped_reasons})")

        # 5. Update status
        write_status("idle",
            last_scan=datetime.now().isoformat(),
            markets_scanned=len(markets),
            signals_found=len(signals),
            trades_executed=executed,
            trade_count_today=self._trade_count_today,
            top_signal=top5[0]["title"][:50] if top5 else None,
            top_score=top5[0]["ev_score"] if top5 else 0,
        )
        logger.info("━━━ Scan Cycle End ━━━")

    # ── Rule Engine ──────────────────────────────────
    def _should_skip(self, signal):
        """返回跳过原因(str)，如果应该交易则返回 None"""
        slug = signal["slug"]

        if not self.config.get("enabled", True):
            return "disabled"

        if slug in self.config.get("blacklist", []):
            return "blacklist"

        if signal["urgency_level"] not in self.config.get("urgency_filter", [1, 2, 3]):
            return "urgency"

        if signal["volume"] < self.config.get("min_volume", 100):
            return "volume"

        if slug in self._last_trade_at:
            elapsed = (datetime.now() - self._last_trade_at[slug]).total_seconds() / 60
            if elapsed < self.config["cooldown_minutes"]:
                return "cooldown"

        if self._trade_count_today >= self.config.get("max_daily_trades", 10):
            return "limit"

        return None

    # ── Trade Execution ──────────────────────────────
    def _execute_trade(self, signal):
        """通过 trader_browser.py 执行浏览器下单"""
        slug = signal["slug"]
        # Decide direction: buy YES if price < 0.70 (room to rise), else NO
        side = "yes" if signal["yes"] < 0.70 else "no"
        amount = min(self.config["max_amount_per_trade"], 5.0)

        logger.info(f"  >>> TRADE: {signal['title'][:40]} | {side.upper()} | ${amount} | EV={signal['ev_score']} | {signal['urgency_emoji']}")

        # Dry-run
        if self.config["dry_run"]:
            logger.info(f"  [DRY RUN] 跳过实际下单")
            self._record(signal, side, amount, "dry_run")
            return True

        # Real trade via subprocess
        trader_script = BASE_DIR / "trader_browser.py"
        cmd = [
            sys.executable, str(trader_script),
            "--url", f"https://polymarket.com/event/{slug}",
            "--side", side,
            "--amount", str(amount),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=120,
                cwd=str(BASE_DIR),
            )

            stdout_tail = result.stdout[-300:] if result.stdout else ""
            stderr_tail = result.stderr[-300:] if result.stderr else ""

            if result.returncode == 0 and "MetaMask" not in stderr_tail:
                logger.info(f"  ✅ Trade OK: {slug}")
                self._record(signal, side, amount, "executed")
                self._notify(signal, side, amount, "success")
                return True
            else:
                error_msg = stderr_tail or f"exit={result.returncode}"
                logger.error(f"  ❌ Trade failed: {error_msg[:120]}")
                self._record(signal, side, amount, "failed", error_msg)
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"  ⏰ Trade timeout: {slug}")
            self._record(signal, side, amount, "timeout")
            return False
        except Exception as e:
            logger.error(f"  💥 Trade error: {e}")
            self._record(signal, side, amount, "error", str(e))
            return False

    # ── Record & Notify ──────────────────────────────
    def _record(self, signal, side, amount, status, error=None):
        record = {
            "timestamp": datetime.now().isoformat(),
            "slug": signal["slug"],
            "title": signal["title"],
            "side": side,
            "amount": amount,
            "yes_price": signal["yes"],
            "no_price": signal["no"],
            "ev_score": signal["ev_score"],
            "ev_summary": signal["ev_summary"],
            "urgency": signal["urgency_label"],
            "status": status,
            "error": error,
        }
        self.history = _append_json(HISTORY_FILE, record)

        if status in ("executed", "dry_run"):
            self._last_trade_at[signal["slug"]] = datetime.now()
            self._trade_count_today += 1

    def _notify(self, signal, side, amount, result):
        """Telegram 推送（如果配置了）"""
        # TODO: Telegram bot integration
        pass


# ═══════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Polymarket Auto-Trader Daemon")
    parser.add_argument("--once", action="store_true", help="只跑一次扫描")
    parser.add_argument("--dry-run", action="store_true", help="强制 dry-run 模式")
    parser.add_argument("--live", action="store_true", help="强制 live 模式（真实交易）")
    args = parser.parse_args()

    # Determine dry_run
    dry_run = None
    if args.dry_run:
        dry_run = True
    elif args.live:
        dry_run = False

    trader = AutoTrader(dry_run_override=dry_run)

    # Handle signals
    def _shutdown(sig, frame):
        trader.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if args.once:
        trader.scan_cycle()
        print("\n单次扫描完成。查看日志: auto_trader.log")
    else:
        trader.start()
        print(f"\n🚀 AutoTrader 已启动 (间隔={trader.config['scan_interval_min']}min, DryRun={trader.config['dry_run']})")
        print("  按 Ctrl+C 停止\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            trader.stop()


if __name__ == "__main__":
    main()
