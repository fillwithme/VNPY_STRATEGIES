# -*- coding: utf-8 -*-
"""缠论三买组合策略（vnpy_portfoliostrategy 标准框架）

由旧 cta_stock 架构 strategy_stock_153_chan_3rd_group_v1.py 改造。
缠论组件（分型/笔/线段/中枢）从旧版 vnpy_stock 移植到本地 chanlib 包，
用真实 check_zs_3rd 中枢三买判断替换占位逻辑。
回测用 1m 数据：on_bars(1m) -> 合成 30m -> 合成日线 -> 日线级出信号与调仓。
"""
from vnpy.trader.constant import Direction, Interval
from vnpy.trader.object import BarData, TickData
from vnpy.trader.utility import ArrayManager

from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator

from chanlib.cta_line_bar import CtaDayBar
from chanlib.cta_utility import check_zs_3rd
from chanlib.compat import get_underlying_symbol


STATUS_OBSERVATE = "OBSERVATE"
STATUS_READY = "READY"
STATUS_ORDERING = "ORDERING"
STATUS_OPENED = "OPENED"
STATUS_CLOSED = "CLOSED"


class ChanThirdBuyGroupStrategy(StrategyTemplate):
    """缠论三买组合策略（真实中枢三买）"""

    author = "sandytaoli"

    total_margin = 1000000        # 总资金
    max_invest_rate = 0.8         # 最大仓位占用比例
    max_single_margin = 0.0       # 单票最大资金占比（0=不限制）
    share_symbol_count = 6        # 组合持仓标的数（用于资金均分）

    d1_3rd_zh_window = 20         # 中枢观察窗口（日线根数）
    d1_fast_ma = 5                # 短期均线
    d1_slow_ma = 20               # 长期均线

    stop_ratio = 0.08             # 硬止损比例

    parameters = [
        "total_margin", "max_invest_rate", "max_single_margin",
        "share_symbol_count", "d1_3rd_zh_window",
        "d1_fast_ma", "d1_slow_ma", "stop_ratio",
    ]
    variables = ["open_count", "close_count"]

    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        self.m1_am = {s: ArrayManager(60) for s in self.vt_symbols}
        self.m30_am = {s: ArrayManager(60) for s in self.vt_symbols}
        self.d1_am = {s: ArrayManager(100) for s in self.vt_symbols}

        self.signals = {
            s: {"status": STATUS_OBSERVATE, "entry_price": 0.0, "target_volume": 0}
            for s in self.vt_symbols
        }
        self.daily_cache = {}

        # 缠论日线K线（真实中枢三买判断用）
        self.day_klines = {s: self._create_day_kline(s) for s in self.vt_symbols}

        self.open_count = 0
        self.close_count = 0

        self.pbg_30 = PortfolioBarGenerator(
            self.on_bars,
            window=30,
            on_window_bars=self.on_30min_bars,
            interval=Interval.MINUTE,
        )

    def on_init(self):
        self.write_log("策略初始化")
        self.load_bars(150, Interval.MINUTE)

    def on_start(self):
        self.write_log("策略启动")

    def on_stop(self):
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        self.pbg_30.update_tick(tick)

    def on_bars(self, bars: dict):
        for vt_symbol, bar in bars.items():
            self.m1_am[vt_symbol].update_bar(bar)
        self.pbg_30.update_bars(bars)

    def on_30min_bars(self, bars: dict):
        finished = {}
        for vt_symbol, bar in bars.items():
            self.m30_am[vt_symbol].update_bar(bar)
            daily = self._update_daily(vt_symbol, bar)
            if daily is not None:
                finished[vt_symbol] = daily
        if finished:
            self.on_daily_bars(finished)

    def _update_daily(self, vt_symbol, bar):
        cache = self.daily_cache.get(vt_symbol)
        date = bar.datetime.date()

        if cache is None or cache["date"] != date:
            finished = None
            if cache is not None:
                finished = self._cache_to_bar(cache)
            self.daily_cache[vt_symbol] = {
                "date": date,
                "symbol": bar.symbol,
                "exchange": bar.exchange,
                "gateway_name": bar.gateway_name,
                "datetime": bar.datetime,
                "open_price": bar.open_price,
                "high_price": bar.high_price,
                "low_price": bar.low_price,
                "close_price": bar.close_price,
                "volume": bar.volume,
                "turnover": bar.turnover,
                "open_interest": bar.open_interest,
            }
            return finished

        cache["high_price"] = max(cache["high_price"], bar.high_price)
        cache["low_price"] = min(cache["low_price"], bar.low_price)
        cache["close_price"] = bar.close_price
        cache["volume"] += bar.volume
        cache["turnover"] += bar.turnover
        cache["open_interest"] = bar.open_interest
        return None

    def _cache_to_bar(self, cache):
        return BarData(
            symbol=cache["symbol"],
            exchange=cache["exchange"],
            datetime=cache["datetime"],
            gateway_name=cache["gateway_name"],
            interval=Interval.DAILY,
            open_price=cache["open_price"],
            high_price=cache["high_price"],
            low_price=cache["low_price"],
            close_price=cache["close_price"],
            volume=cache["volume"],
            turnover=cache["turnover"],
            open_interest=cache["open_interest"],
        )

    def on_daily_bars(self, bars: dict):
        for vt_symbol, bar in bars.items():
            self.d1_am[vt_symbol].update_bar(bar)
            self._feed_day_kline(vt_symbol, bar)
            self.run_signal(vt_symbol, bar)
        self.rebalance_portfolio(bars)
        self.put_event()

    def run_signal(self, vt_symbol, bar):
        sig = self.signals[vt_symbol]
        pos = self.get_pos(vt_symbol)

        if pos > 0:
            if self._should_close(vt_symbol, bar):
                self.set_target(vt_symbol, 0)
                sig["status"] = STATUS_CLOSED
                sig["entry_price"] = 0.0
                self.close_count += 1
            return

        if self._check_d1_3rd_buy(vt_symbol, bar):
            target = self._calc_target_volume(vt_symbol, bar)
            if target > 0:
                self.set_target(vt_symbol, target)
                sig["status"] = STATUS_OPENED
                sig["entry_price"] = bar.close_price
                sig["target_volume"] = target
                self.open_count += 1
        else:
            self.set_target(vt_symbol, 0)

    def _create_day_kline(self, vt_symbol):
        """创建缠论日线K线（CtaDayBar），驱动分型/笔/线段/中枢计算"""
        setting = {
            "name": f"{vt_symbol}_D1",
            "para_ma1_len": 55,
            "para_ma2_len": 89,
            "para_macd_fast_len": 12,
            "para_macd_slow_len": 26,
            "para_macd_signal_len": 9,
            "para_active_chanlun": True,   # 激活缠论（分型/笔/线段/中枢）
            "para_active_chan_xt": True,   # 激活缠论形态分析
            "price_tick": 0.01,            # A股最小变动单位
            "underly_symbol": get_underlying_symbol(vt_symbol.split(".")[0]).upper(),
            "is_stock": True,
        }
        return CtaDayBar(strategy=None, cb_on_bar=None, setting=setting)

    def _feed_day_kline(self, vt_symbol, bar):
        """将日线bar喂给缠论日线K线，驱动分型/笔/中枢计算"""
        kline = self.day_klines[vt_symbol]
        # 旧版 BarData 有 trading_day 字段，新版没有；缠论组件 add_bar 依赖它判断新日线
        bar.trading_day = bar.datetime.strftime("%Y-%m-%d")
        kline.add_bar(bar)

    def _check_d1_3rd_buy(self, vt_symbol, bar):
        """日线缠论中枢三买判断（真实 check_zs_3rd）"""
        kline = self.day_klines[vt_symbol]

        # 缠论对象尚未形成（数据不足），无法判断
        if kline.pre_duan is None or kline.cur_bi_zs is None:
            return False
        if kline.cur_duan is None or kline.cur_bi is None or kline.cur_fenxing is None:
            return False

        try:
            return check_zs_3rd(
                big_kline=kline,
                small_kline=None,
                signal_direction=Direction.LONG,
                first_zs=False,
                all_zs=False,
            )
        except Exception:
            return False

    def _should_close(self, vt_symbol, bar):
        sig = self.signals[vt_symbol]
        entry = sig.get("entry_price", 0.0)
        if entry <= 0:
            return False

        if bar.close_price <= entry * (1 - self.stop_ratio):
            return True

        am = self.d1_am[vt_symbol]
        if am.inited and am.sma(self.d1_fast_ma) < am.sma(self.d1_slow_ma):
            return True

        return False

    def _calc_target_volume(self, vt_symbol, bar):
        if bar.close_price <= 0:
            return 0

        per = self.total_margin * self.max_invest_rate / max(self.share_symbol_count, 1)
        if self.max_single_margin > 0:
            per = min(per, self.total_margin * self.max_single_margin)

        return int(per / bar.close_price)

    def calculate_price(self, vt_symbol, direction, reference):
        if direction == Direction.LONG:
            return reference * 1.001
        return reference * 0.999
