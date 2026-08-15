from datetime import datetime

import numpy as np

from vnpy.trader.utility import BarGenerator
from vnpy.trader.object import TickData, BarData
from vnpy.trader.constant import Direction, Interval

from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine


class PairTradingStrategy(StrategyTemplate):
    """配对交易策略（30分钟K + 平仓冷却期）"""

    author = "用Python的交易员"

    tick_add = 1
    boll_window = 20
    boll_dev = 2.5
    fixed_size = 1
    leg1_ratio = 1
    leg2_ratio = 1
    cooldown_bars = 10
    bar_window = 30

    leg1_symbol = ""
    leg2_symbol = ""
    current_spread = 0.0
    boll_mid = 0.0
    boll_down = 0.0
    boll_up = 0.0

    parameters = [
        "tick_add",
        "boll_window",
        "boll_dev",
        "fixed_size",
        "leg1_ratio",
        "leg2_ratio",
        "cooldown_bars",
        "bar_window",
    ]
    variables = [
        "leg1_symbol",
        "leg2_symbol",
        "current_spread",
        "boll_mid",
        "boll_down",
        "boll_up",
    ]

    def __init__(
        self,
        strategy_engine: StrategyEngine,
        strategy_name: str,
        vt_symbols: list[str],
        setting: dict
    ) -> None:
        """构造函数"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        # 1分钟 bar 生成器（tick 合成用，实盘）
        self.bgs: dict[str, BarGenerator] = {}
        # 30分钟 bar 生成器（1分钟 bar 合成用）
        self.bgs_window: dict[str, BarGenerator] = {}

        self.last_tick_time: datetime | None = None
        self.window_bars: dict[str, BarData] = {}

        self.spread_count: int = 0
        self.spread_data: np.ndarray = np.zeros(100)
        self.cooldown_remaining: int = 0

        # Obtain contract info
        self.leg1_symbol, self.leg2_symbol = vt_symbols

        def on_bar(bar: BarData) -> None:
            """"""
            pass

        def on_window_bar(bar: BarData) -> None:
            """30分钟 bar 完成回调"""
            self.window_bars[bar.vt_symbol] = bar

        for vt_symbol in self.vt_symbols:
            self.bgs[vt_symbol] = BarGenerator(on_bar)
            self.bgs_window[vt_symbol] = BarGenerator(
                on_bar=on_bar,
                window=self.bar_window,
                on_window_bar=on_window_bar,
                interval=Interval.MINUTE,
            )

    def on_init(self) -> None:
        """策略初始化回调"""
        self.write_log("策略初始化")

        self.load_bars(1)

    def on_start(self) -> None:
        """策略启动回调"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止回调"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        """行情推送回调（实盘）"""
        if (
            self.last_tick_time
            and self.last_tick_time.minute != tick.datetime.minute
        ):
            bars = {}
            for vt_symbol, bg in self.bgs.items():
                bars[vt_symbol] = bg.generate()
            self.on_bars(bars)

        bg = self.bgs[tick.vt_symbol]
        bg.update_tick(tick)

        self.last_tick_time = tick.datetime

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K线切片回调：将1分钟 bar 喂给 30分钟 bar 生成器"""
        self.window_bars = {}

        for vt_symbol, bar in bars.items():
            bg = self.bgs_window.get(vt_symbol)
            if bg and bar:
                bg.update_bar(bar)

        # 两条腿的30分钟 bar 都完成后，执行配对逻辑
        if len(self.window_bars) == len(self.vt_symbols):
            self.on_window_bars(self.window_bars)

    def on_window_bars(self, bars: dict[str, BarData]) -> None:
        """30分钟K线切片回调"""
        leg1_bar = bars.get(self.leg1_symbol, None)
        leg2_bar = bars.get(self.leg2_symbol, None)

        if not leg1_bar or not leg2_bar:
            return

        # 计算当前价差
        self.current_spread = leg1_bar.close_price * self.leg1_ratio - leg2_bar.close_price * self.leg2_ratio

        # 更新到价差序列
        self.spread_data[:-1] = self.spread_data[1:]
        self.spread_data[-1] = self.current_spread

        self.spread_count += 1
        if self.spread_count <= self.boll_window:
            return

        # 计算布林带
        buf: np.ndarray = self.spread_data[-self.boll_window:]

        std = buf.std()
        self.boll_mid = buf.mean()
        self.boll_up = self.boll_mid + self.boll_dev * std
        self.boll_down = self.boll_mid - self.boll_dev * std

        # 冷却期：平仓后 N 根 bar 内不重新开仓
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            self.put_event()
            return

        # 计算目标持仓
        leg1_pos = self.get_pos(self.leg1_symbol)

        if not leg1_pos:
            if self.current_spread >= self.boll_up:
                self.set_target(self.leg1_symbol, -self.fixed_size)
                self.set_target(self.leg2_symbol, self.fixed_size)
            elif self.current_spread <= self.boll_down:
                self.set_target(self.leg1_symbol, self.fixed_size)
                self.set_target(self.leg2_symbol, -self.fixed_size)
        elif leg1_pos > 0:
            if self.current_spread >= self.boll_mid:
                self.set_target(self.leg1_symbol, 0)
                self.set_target(self.leg2_symbol, 0)
                self.cooldown_remaining = self.cooldown_bars
        else:
            if self.current_spread <= self.boll_mid:
                self.set_target(self.leg1_symbol, 0)
                self.set_target(self.leg2_symbol, 0)
                self.cooldown_remaining = self.cooldown_bars

        # 执行调仓交易
        self.rebalance_portfolio(bars)

        # 推送更新事件
        self.put_event()

    def calculate_price(self, vt_symbol: str, direction: Direction, reference: float) -> float:
        """计算调仓委托价格（支持按需重载实现）"""
        pricetick: float = self.get_pricetick(vt_symbol)

        if direction == Direction.LONG:
            price: float = reference + self.tick_add * pricetick
        else:
            price = reference - self.tick_add * pricetick

        return price
