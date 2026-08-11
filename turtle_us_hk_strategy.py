# -*- coding: utf-8 -*-
"""
turtle_us_hk_strategy.py - 美股/港股通用海龟组合策略

将原期货版海龟策略（examples/tdx_formula/strategies/turtle_strategy.py）改造为
美股/港股通用版，核心变化：

1. 数据合成层：弃用自研 MinuteBarsGenerator/DailyBarGenerator 与 index_contract.csv
   期货乘数表，改用官方 vnpy_portfoliostrategy 的 PortfolioBarGenerator 完成
   "tick -> 多品种分钟截面 -> 盘中日K截面" 两级合成。
2. 市场参数化：
   - 超价从固定金额改为 pricetick 倍数（price_add_ticks），对美股(tick 0.01)、
     港股(tick 0.001~0.05)语义统一；
   - 日K合成收盘时间参数化（daily_end，默认 15:59，兼容美股/港股 16:00 收盘）；
   - 做空开关 allow_short（默认开启，可关闭仅做多）。
3. 港股整手对齐：每手股数以参数 lot_sizes 显式配置（回测引擎无 min_volume 来源），
   unit 与仓差 diff 均按整手对齐，不足一手归零，避免碎股导致回测实盘不一致。
4. 合约信息适配：删除 get_product_name 查表逻辑，乘数改用模板 get_size()（股票=1），
   超价用 get_pricetick()。
"""

from datetime import datetime, time
from typing import Dict, List

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, TickData

from vnpy_portfoliostrategy import (
    ArrayManager,
    StrategyEngine,
    StrategyTemplate,
)
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator


class TurtleUsHkStrategy(StrategyTemplate):
    """美股/港股通用海龟策略"""

    author: str = "用Python的交易员"

    # 参数
    entry_window: int = 20          # 入场通道周期
    exit_window: int = 10           # 出场通道周期
    cci_window: int = 14            # CCI周期
    cci_signal: int = 20            # CCI信号阈值
    n_window: int = 20              # N值（ATR）周期
    unit_limit: int = 4             # 最大开仓级别
    trading_size: int = 1           # 每级别交易手数
    price_add_ticks: int = 8        # 超价 = price_add_ticks * pricetick
    daily_end: str = "15:59"        # 日K合成收盘时间（交易所本地时间 HH:MM）
    allow_short: bool = True        # 是否允许做空
    lot_sizes: dict = {}            # vt_symbol -> 每手股数（港股如 {"700-HKD-STK.SEHK": 100}，缺省按 1）
    capital: int = 10_000_000       # 风险预算基准资金
    risk_level: float = 0.002       # 单笔风险比例

    # 名称列表
    parameters = [
        "entry_window",
        "exit_window",
        "cci_window",
        "cci_signal",
        "n_window",
        "unit_limit",
        "trading_size",
        "price_add_ticks",
        "daily_end",
        "allow_short",
        "lot_sizes",
        "capital",
        "risk_level"
    ]
    variables = []

    def __init__(
        self,
        strategy_engine: "StrategyEngine",
        strategy_name: str,
        vt_symbols: List[str],
        setting: dict
    ):
        """构造函数"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        # 解析日K合成收盘时间
        self.daily_end_time: time = datetime.strptime(self.daily_end, "%H:%M").time()

        # 初始化信号字典
        self.signals: Dict[str, TurtleSignal] = {}
        for vt_symbol in vt_symbols:
            # 合约乘数（股票为1）
            contract_size: int = self.get_size(vt_symbol)
            # 每手股数（缺省按 1 手 = 1 股）
            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1))

            self.signals[vt_symbol] = TurtleSignal(
                vt_symbol,
                self.entry_window,
                self.exit_window,
                self.cci_window,
                self.cci_signal,
                self.n_window,
                self.unit_limit,
                self.trading_size,
                contract_size,
                lot_size,
                self.capital,
                self.risk_level,
                self.allow_short
            )

        # 初始化目标字典
        self.targets: Dict[str, int] = {}

        # 初始化组合K线生成器（tick -> 分钟截面 -> 盘中日K截面）
        self.pbg: PortfolioBarGenerator = PortfolioBarGenerator(
            self.on_bars,
            on_window_bars=self.on_daily_bars,
            interval=Interval.DAILY,
            daily_end=self.daily_end_time
        )

    def on_init(self):
        """初始化"""
        self.write_log("策略初始化")

        # 加载用于初始化的数据天数，需要超过所需最长的窗口
        self.load_bars(30)

    def on_start(self):
        """启动"""
        self.write_log("策略启动")

    def on_stop(self):
        """停止"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """Tick推送"""
        self.pbg.update_tick(tick)

    def on_bars(self, bars: Dict[str, BarData]):
        """原始K线推送"""
        if not bars:
            return

        bar: BarData = list(bars.values())[0]
        self.write_log(f"{bar.datetime} - {list(bars.keys())}")

        # 全撤之前委托
        self.cancel_all()

        # 计算合约目标
        self.calculate_targets(bars)

        # 发送交易委托
        self.send_orders(bars)

        # 推进日K线合成
        self.pbg.update_bars(bars)

    def on_daily_bars(self, bars: Dict[str, BarData]):
        """日K线推送"""
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.update_daily_bar(bar)

    def calculate_targets(self, bars: Dict[str, BarData]) -> None:
        """计算每个合约的目标"""
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.on_bar(bar)
            self.targets[vt_symbol] = signal.get_target()

    def send_orders(self, bars: Dict[str, BarData]) -> None:
        """发送委托"""
        for vt_symbol, bar in bars.items():
            # 计算目标和实际仓位差
            target: int = self.targets[vt_symbol]
            pos: int = self.get_pos(vt_symbol)
            diff: int = target - pos

            # 按每手股数整手对齐，不足一手跳过
            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1))
            diff = int(diff / lot_size) * lot_size
            if not diff:
                continue

            pricetick: float = self.get_pricetick(vt_symbol)

            # 基于仓位差执行交易
            if diff > 0:
                price: float = bar.close_price + self.price_add_ticks * pricetick

                # 由于海龟所有开平仓都会先回到仓位0的情况
                # 因此只需要考虑本次是开仓还是平仓即可
                if pos < 0:
                    self.cover(vt_symbol, price, abs(diff))
                else:
                    self.buy(vt_symbol, price, abs(diff))
            elif diff < 0:
                # 禁止做空时跳过空头开仓（平多头不受影响）
                if not self.allow_short and pos <= 0:
                    continue

                price: float = bar.close_price - self.price_add_ticks * pricetick

                if pos > 0:
                    self.sell(vt_symbol, price, abs(diff))
                else:
                    self.short(vt_symbol, price, abs(diff))


class TurtleSignal:
    """海龟信号"""

    def __init__(
        self,
        vt_symbol: str,
        entry_window: int,
        exit_window: int,
        cci_window: int,
        cci_signal: int,
        n_window: int,
        unit_limit: int,
        trading_size: int,
        contract_size: int,
        lot_size: int,
        capital: int,
        risk_level: float,
        allow_short: bool
    ) -> None:
        """构造函数"""
        # 参数
        self.vt_symbol: str = vt_symbol
        self.entry_window: int = entry_window
        self.exit_window: int = exit_window
        self.cci_window: int = cci_window
        self.cci_signal: int = cci_signal
        self.n_window: int = n_window
        self.unit_limit: int = unit_limit
        self.trading_size: int = trading_size
        self.contract_size: int = contract_size
        self.lot_size: int = lot_size
        self.capital: int = capital
        self.risk_level: float = risk_level
        self.allow_short: bool = allow_short

        # 变量
        self.target: int = 0
        self.unit: int = 0

        # 因子
        self.factor = TurtleFactor(
            entry_window=self.entry_window,
            exit_window=self.exit_window,
            cci_window=self.cci_window,
            cci_signal=self.cci_signal,
            n_window=self.n_window,
            trading_size=self.trading_size,
            contract_size=self.contract_size,
            unit_limit=self.unit_limit,
            allow_short=self.allow_short
        )

    def on_bar(self, bar: BarData) -> None:
        """K线推送"""
        # 推送给因子计算
        self.factor.on_bar(bar)

        # 无仓位时更新unit
        if not self.target:
            if self.factor.n:
                unit: float = (self.capital * self.risk_level) / (
                    self.factor.n * self.contract_size
                )
            else:
                unit = 0.0

            # 按每手股数整手对齐，不足一手归0（美股 lot_size=1 时保底1手）
            unit = int(unit / self.lot_size) * self.lot_size
            if unit == 0 and self.lot_size == 1:
                unit = 1
            self.unit = unit

        # 获取因子目标仓位
        self.target = int(self.factor.get_target() * self.unit)

    def update_daily_bar(self, bar: BarData) -> None:
        """日K线推送"""
        self.factor.update_daily_bar(bar)

    def get_target(self) -> int:
        """获取信号"""
        return self.target


class TurtleFactor:
    """海龟因子"""

    def __init__(
        self,
        entry_window: int,
        exit_window: int,
        cci_window: int,
        cci_signal: int,
        n_window: int,
        trading_size: int,
        contract_size: int,
        unit_limit: int,
        allow_short: bool
    ) -> None:
        """构造函数"""
        # 参数
        self.entry_window: int = entry_window
        self.exit_window: int = exit_window
        self.cci_window: int = cci_window
        self.cci_signal: int = cci_signal
        self.n_window: int = n_window
        self.trading_size: int = trading_size
        self.contract_size: int = contract_size
        self.unit_limit: int = unit_limit
        self.allow_short: bool = allow_short

        # 变量
        self.entry_up: float = 0.0       # 入场通道
        self.entry_down: float = 0.0

        self.exit_up: float = 0.0        # 出场通道
        self.exit_down: float = 0.0

        self.cci: float = 0.0            # cci数值

        self.n: float = 0.0              # 波动度量

        self.long_entry: float = 0.0     # 开仓价格
        self.short_entry: float = 0.0

        self.target: int = 0             # 目标仓位
        self.traded: bool = False        # 日内交易过

        # 工具
        self.am = ArrayManager()

    def on_bar(self, bar: BarData) -> None:
        """原始K线推送"""
        # 每日只允许交易一次
        if self.am.inited and not self.traded:
            old_target: int = self.target

            # 判断当前目标
            if not self.target:
                self.check_long_target(bar)

                if self.allow_short:
                    self.check_short_target(bar)
            elif self.target > 0:
                self.check_long_target(bar)

                # 计算固定止损价格
                long_stop: float = self.long_entry - 2 * self.n
                # 和离场通道比较，取更高价挂出停止单
                long_stop = max(long_stop, self.exit_down)

                if bar.low_price <= long_stop:
                    self.target = 0
            elif self.target < 0:
                self.check_short_target(bar)

                short_stop: float = self.short_entry + 2 * self.n
                short_stop = min(short_stop, self.exit_up)

                if bar.high_price >= short_stop:
                    self.target = 0

            # 记录今天已经执行过交易
            if old_target != self.target:
                self.traded = True

    def update_daily_bar(self, bar: BarData) -> None:
        """日K线推送（由策略层 on_daily_bars 按品种分发）"""
        # 缓存K线序列
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        # 只有无持仓时，才更新入场通道位置和波动度量
        if not self.target:
            self.entry_up, self.entry_down = self.am.donchian(self.entry_window)
            self.n = self.am.atr(self.n_window)

        self.exit_up, self.exit_down = self.am.donchian(self.exit_window)

        self.cci = self.am.cci(self.cci_window)

        # 新的一天清空交易记录
        self.traded = False

    def check_long_target(self, bar: BarData) -> None:
        """检查多头"""
        level: int = self.unit_limit

        while level > 0:
            level_limit: int = level * self.trading_size
            level_price: float = self.entry_up + self.n * (level - 1) * 0.5

            if self.target < level_limit and bar.high_price >= level_price and self.cci > self.cci_signal:
                self.target = level_limit
                self.long_entry = level_price

            level -= 1

    def check_short_target(self, bar: BarData) -> None:
        """检查空头"""
        level: int = self.unit_limit

        while level > 0:
            level_limit: int = -level * self.trading_size
            level_price: float = self.entry_down - self.n * (level - 1) * 0.5

            if self.target > level_limit and bar.low_price <= level_price and self.cci < -self.cci_signal:
                self.target = level_limit
                self.short_entry = level_price

            level -= 1

    def get_target(self) -> int:
        """获取因子目标"""
        return self.target
