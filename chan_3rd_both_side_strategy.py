# -*- coding: utf-8 -*-
"""缠论三买三卖双向组合策略（vnpy_portfoliostrategy 标准框架）

由 chan_3rd_buy_group_strategy.py（纯多头三买）扩展而来，支持双向交易：
    - 日线中枢第三类买点（三买） -> 做多（目标仓位为正）
    - 日线中枢第三类卖点（三卖） -> 做空（目标仓位为负）

平仓条件与多头版完全对称：
    - 多头持仓：价格跌破硬止损 / 日线均线死叉 -> 平多
    - 空头持仓：价格涨破硬止损 / 日线均线金叉 -> 平空

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


class ChanThirdBothSideGroupStrategy(StrategyTemplate):
    """缠论三买三卖双向组合策略（真实中枢三买/三卖）"""

    author = "sandytaoli"

    total_margin = 1000000        # 总资金
    max_invest_rate = 0.8         # 最大仓位占用比例
    max_single_margin = 0.0       # 单票最大资金占比（0=不限制）
    share_symbol_count = 6        # 组合持仓标的数（用于资金均分）

    d1_3rd_zh_window = 20         # 中枢观察窗口（日线根数）
    d1_fast_ma = 5                # 短期均线
    d1_slow_ma = 30               # 长期均线（由 20 调慢为 30，过滤假信号、降低交易频率）

    stop_ratio = 0.12             # 硬止损比例（由 0.08 放宽，减少震荡市反复扫损）
    use_atr_stop = True           # 是否启用 ATR 动态止损（取硬止损与 ATR 止损中更宽松者）
    atr_stop_multiple = 2.0       # ATR 止损倍数
    atr_window = 20               # ATR 周期

    allow_short = True            # 是否允许做空（False 时仅三买做多）
    volume_confirm = True         # 是否启用量价确认（放量 + 创新高/新低）
    volume_multiple = 1.5         # 量价确认：当日量 / N日均量 的最小倍数
    volume_window = 20            # 量价确认窗口（均量周期 / 新高新低回看根数）

    parameters = [
        "total_margin", "max_invest_rate", "max_single_margin",
        "share_symbol_count", "d1_3rd_zh_window",
        "d1_fast_ma", "d1_slow_ma", "stop_ratio",
        "use_atr_stop", "atr_stop_multiple", "atr_window",
        "allow_short", "volume_confirm", "volume_multiple", "volume_window",
    ]
    variables = ["open_count", "close_count"]

    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        self.m1_am = {s: ArrayManager(60) for s in self.vt_symbols}
        self.m30_am = {s: ArrayManager(60) for s in self.vt_symbols}
        self.d1_am = {s: ArrayManager(100) for s in self.vt_symbols}

        self.signals = {
            s: {
                "status": STATUS_OBSERVATE,
                "direction": None,          # 当前持仓方向：Direction.LONG / Direction.SHORT / None
                "entry_price": 0.0,
                "target_volume": 0,
            }
            for s in self.vt_symbols
        }
        self.daily_cache = {}

        # 缠论日线K线（真实中枢三买/三卖判断用）
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

        # 持有多头：判断平多（止损 / 均线死叉）
        if pos > 0:
            if self._should_close_long(vt_symbol, bar):
                self._close_position(vt_symbol, sig)
            return

        # 持有空头：判断平空（止损 / 均线金叉）
        if pos < 0:
            if self._should_close_short(vt_symbol, bar):
                self._close_position(vt_symbol, sig)
            return

        # 空仓：优先三买做多，其次三卖做空（做空受 allow_short 开关控制，且均需通过量价确认）
        direction = None
        if self._check_d1_3rd(vt_symbol, bar, Direction.LONG):
            direction = Direction.LONG
        elif self.allow_short and self._check_d1_3rd(vt_symbol, bar, Direction.SHORT):
            direction = Direction.SHORT

        if direction is not None and self._pass_volume_confirm(vt_symbol, bar, direction):
            self._open_position(vt_symbol, bar, sig, direction)
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

    def _check_d1_3rd(self, vt_symbol, bar, direction):
        """日线缠论中枢三买/三卖判断（真实 check_zs_3rd）"""
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
                signal_direction=direction,
                first_zs=False,
                all_zs=False,
            )
        except Exception:
            return False

    def _open_position(self, vt_symbol, bar, sig, direction):
        """按方向开仓：三买 -> 正目标仓位（做多），三卖 -> 负目标仓位（做空）"""
        target = self._calc_target_volume(vt_symbol, bar)
        if target <= 0:
            return

        signed_target = target if direction == Direction.LONG else -target
        self.set_target(vt_symbol, signed_target)
        sig["status"] = STATUS_OPENED
        sig["direction"] = direction
        sig["entry_price"] = bar.close_price
        sig["target_volume"] = signed_target
        self.open_count += 1

    def _close_position(self, vt_symbol, sig):
        """清仓（多空通用）"""
        self.set_target(vt_symbol, 0)
        sig["status"] = STATUS_CLOSED
        sig["direction"] = None
        sig["entry_price"] = 0.0
        sig["target_volume"] = 0
        self.close_count += 1

    def _should_close_long(self, vt_symbol, bar):
        """多头平仓：价格跌破止损价 或 均线死叉

        止损价 = 硬止损(entry*(1-stop_ratio)) 与 ATR 止损(entry - k*ATR) 中的更宽松者（更低者）。
        """
        sig = self.signals[vt_symbol]
        entry = sig.get("entry_price", 0.0)
        if entry <= 0:
            return False

        am = self.d1_am[vt_symbol]
        stop_price = entry * (1 - self.stop_ratio)
        if self.use_atr_stop and am.inited:
            atr_val = am.atr(self.atr_window)
            if atr_val > 0:
                stop_price = min(stop_price, entry - self.atr_stop_multiple * atr_val)

        if bar.close_price <= stop_price:
            return True

        if am.inited and am.sma(self.d1_fast_ma) < am.sma(self.d1_slow_ma):
            return True

        return False

    def _should_close_short(self, vt_symbol, bar):
        """空头平仓：价格涨破止损价 或 均线金叉

        止损价 = 硬止损(entry*(1+stop_ratio)) 与 ATR 止损(entry + k*ATR) 中的更宽松者（更高者）。
        """
        sig = self.signals[vt_symbol]
        entry = sig.get("entry_price", 0.0)
        if entry <= 0:
            return False

        am = self.d1_am[vt_symbol]
        stop_price = entry * (1 + self.stop_ratio)
        if self.use_atr_stop and am.inited:
            atr_val = am.atr(self.atr_window)
            if atr_val > 0:
                stop_price = max(stop_price, entry + self.atr_stop_multiple * atr_val)

        if bar.close_price >= stop_price:
            return True

        if am.inited and am.sma(self.d1_fast_ma) > am.sma(self.d1_slow_ma):
            return True

        return False

    def _pass_volume_confirm(self, vt_symbol, bar, direction):
        """量价确认：当日放量（>= volume_multiple * N日均量）且创近 N 日新高/新低

        - 做多：要求当日成交量放大，且收盘价创近 N 日新高（规避假突破追高）；
        - 做空：要求当日成交量放大，且收盘价创近 N 日新低。
        - 关闭 volume_confirm 或数据未预热时直接通过（不确认）。
        """
        if not self.volume_confirm:
            return True

        am = self.d1_am[vt_symbol]
        if not am.inited:
            return True

        n = max(self.volume_window, 2)
        avg_volume = float(am.volume_array[-n:].mean())
        if avg_volume <= 0 or bar.volume < self.volume_multiple * avg_volume:
            return False

        if direction == Direction.LONG:
            recent_high = float(am.close_array[-n - 1:-1].max())
            return bar.close_price >= recent_high
        recent_low = float(am.close_array[-n - 1:-1].min())
        return bar.close_price <= recent_low

    def _calc_target_volume(self, vt_symbol, bar):
        """计算目标仓位手数（正数，开仓方向由 _open_position 决定符号）"""
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
