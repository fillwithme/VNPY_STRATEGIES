# -*- coding: utf-8 -*-
"""
turtle_us_hk_strategy.py - 美股/港股通用海龟组合策略

========================================================================
一、策略思路
========================================================================
经典海龟交易系统（Turtle Trading System）的美股/港股通用实现，属趋势跟踪
（Trend Following）策略，核心思路如下：

1. 突破入场：以唐奇安通道（Donchian Channel）判断趋势，价格突破过去
   entry_window 日最高价（上轨）做多、跌破过去 entry_window 日最低价
   （下轨）做空，趋势启动时尽早介入。
2. 金字塔加仓：突破后价格每顺势推进 0.5N（N 为波动度量），加仓一级，
   最多 unit_limit 级，让盈利头寸逐步放大。
3. N 值风控：N（即 ATR 波动度量）同时决定头寸规模（unit）、加仓间距
   （0.5N）与止损距离（2N），实现「高波动小仓位、低波动大仓位」的
   自适应风控。
4. CCI 过滤：用 CCI 辅助过滤，仅在 CCI 与突破方向一致时开仓，避免
   超买/超卖区间追涨杀跌。
5. 持仓冻结：入场通道与 N 值在持仓期间冻结（保持建仓时的趋势基准与
   风控基准），出场通道持续更新以跟踪止损/离场。

========================================================================
二、交易规则
========================================================================
【指标定义】由通达信公式《海龟交易系统.tdx》计算（见 formulas 目录）：
    ENTRYUP   = HHV(HIGH, entry_window)     入场通道上轨
    ENTRYDOWN = LLV(LOW,  entry_window)     入场通道下轨
    EXITUP    = HHV(HIGH, exit_window)      出场通道上轨
    EXITDOWN  = LLV(LOW,  exit_window)      出场通道下轨
    NVAL      = ATR(n_window)               波动度量 N
    CCIVAL    = CCI(cci_window)             趋势过滤

【头寸单位 unit 与 risk_level 风险比例】
    unit = (capital * risk_level) / (N * contract_size)

    - N：波动度量（ATR，即每根日K的平均波幅，单位与价格相同，如 2.5 元）。
    - contract_size：合约乘数（股票恒为 1，即每股波动 1 元 = 1 点价值 1 元）。
    - capital：风险预算基准资金（注意：是「用于计算仓位规模的名义基准」，
      不是账户实时净值，净值的涨跌不会反向改变 unit）。

    risk_level 的含义：单笔单位仓位的「每 1N 波动风险比例」——
    持有一个 unit，若价格朝不利方向波动 1 个 N，账户亏损恰好等于
    capital × risk_level。反推：N 越大（波动越大），在相同 risk_level 下
    算出的 unit 越小，从而把「每次 1N 波动的绝对亏损」锚定为固定比例，
    实现「高波动小仓位、低波动大仓位」的自适应风控。

    与止损的关系：海龟止损距离固定为 2N，因此单个 unit 建仓后一旦被止损，
    最大亏损 = 2 × capital × risk_level。例如：
        capital = 1000 万、risk_level = 0.002 → 单 unit 止损最大亏 4 万（0.4%）
        capital = 1000 万、risk_level = 0.006 → 单 unit 止损最大亏 12 万（1.2%）
    注意：金字塔加仓会放大总敞口——最多 unit_limit 级，若加满后整体止损，
    亏损约为单 unit 的数倍，因此 risk_level 越大，回撤也越深（回测中
    risk_level=0.006 的组合回撤约 -56%，risk_level=0.001 仅约 -16%）。

    整手对齐：unit 按每手股数 lot_size 向下取整，不足一手归零
    （美股 lot_size=1 时保底 1 手）。
    risk_levels 可按 vt_symbol 分档覆盖默认 risk_level（如趋势/成长股给 0.004、
    低波动价值股给 0.001）。

【开仓】（无持仓时，盘中价格突破「冻结」的入场通道，CCI 同向过滤）
    - 入场通道与 N 值只在无持仓时才随新日K更新，一旦持仓即「冻结」，
      保证建仓基准不随后续行情漂移。
    - 做多：当根最高价 >= ENTRYUP（入场通道上轨）且 CCI > cci_signal 时，
      开 +1 级（trading_size 手），开仓触发价 = ENTRYUP。
    - 做空（allow_short=True 时）：当根最低价 <= ENTRYDOWN（入场通道下轨）
      且 CCI < -cci_signal 时，开 -1 级，开仓触发价 = ENTRYDOWN。
    - CCI 是「方向过滤器」：突破上轨但 CCI 未转强（超买/动能不足）则不追多，
      反之同理，用来过滤假突破，而非独立信号。

【加仓】（0.5N 金字塔，最多 unit_limit 级，越涨越买）
    - 逻辑：突破入场后，价格每顺势推进 0.5N 就加仓一级，让盈利头寸随趋势
      逐步放大，而不是一次性满仓。多头第 level 级触发价 = ENTRYUP + N * (level - 1) * 0.5：
        第 1 级（首次开仓）触发价 = ENTRYUP
        第 2 级触发价            = ENTRYUP + 0.5N
        第 3 级触发价            = ENTRYUP + 1.0N
        …… 依此类推，每级间距固定为 0.5N
    - 当根最高价触及该级触发价且 CCI > cci_signal 时，目标仓位升至
      level * trading_size；空头对称（ENTRYDOWN - N * (level-1) * 0.5，
      最低价触及且 CCI < -cci_signal）。
    - 每次加仓都把「最近开仓/加仓价」抬高，因此下方的 2N 止损基准也随之阶梯上移。

【止损/离场】（持仓期间，双重保护取更紧者）
    离场价不是单一固定止损，而是「2N 固定止损」与「出场通道移动止损」
    两者取更紧（离当前价更近）的一个，构成复合离场：

    1) 2N 固定止损（阶梯式，非连续移动）：
       多头止损 = 最近开仓/加仓价 - 2N；空头止损 = 最近开仓/加仓价 + 2N。
       N 冻结不变，止损基准只在「加仓」时阶梯上移（每次 +0.5N），
       其余时间不随价格移动 —— 这是海龟经典的风控底线。
    2) 出场通道移动止损（跟踪止损，连续移动）：
       EXITDOWN = LLV(LOW, exit_window)（出场通道下轨），随价格上涨、
       最近 exit_window 日的最低点抬高而持续上移；空头用
       EXITUP = HHV(HIGH, exit_window) 持续下移。这部分就是「移动止损」。
    3) 触发规则（多头）：当根最低价 <= max(最近开仓/加仓价 - 2N, EXITDOWN) 即全部平仓；
       空头对称：当根最高价 >= min(最近开仓/加仓价 + 2N, EXITUP) 即全部平仓。
       即：无论先跌破「出场通道」还是「2N 底线」，哪个先到就先离场。
       （趋势强时出场通道通常在 2N 之上，由移动止损主导离场；剧烈回撤时
         2N 底线兜底，防止单笔亏损失控。）

【关键约束】
    - 持仓期间入场通道与 N 值冻结，仅无持仓时随新日K更新（保持建仓基准）；
      而出场通道（移动止损）则始终随新日K更新。
    - 每标的每日最多交易一次（traded 标记：当日 target 发生一次变化后，
      本交易日内不再动作），开/加/平在同日不会重复触发。
    - 目标仓位 = 级别 * unit，下单执行「目标-实际」仓差，先全平再重建；
      即海龟平仓总是回到空仓，再从空仓按目标级别重新建仓。

========================================================================
三、信号实现架构
========================================================================
    - 通达信公式引擎（tdx_engine.py）：只算指标（通道/N/CCI），公式可复用、
      与通达信公式管理器语法兼容；
    - TurtleStateMachine 状态机：实现有状态信号（突破开仓、0.5N 加仓、
      2N 止损、冻结语义），盘中分钟粒度触发（on_bar 每根分钟 bar 调 step）；
    - 本策略（组合层）：tick -> 分钟 -> 日K 两级合成（PortfolioBarGenerator）、
      unit 计算、目标仓位与实际仓位差的下单执行。

========================================================================
四、改造要点（相对原期货版）
========================================================================
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

import json
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, TickData

from vnpy_portfoliostrategy import (
    StrategyEngine,
    StrategyTemplate,
)
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator


# ----------------------------------------------------------------------
# 工具函数：定位并导入 tdx_engine（与本策略文件解耦，便于随目录迁移）
# ----------------------------------------------------------------------
def _import_tdx_engine(extra_hints: list[str] | None = None):
    """按优先级定位 tdx_engine 模块并返回 (TdxEngine, TurtleStateMachine)

    查找顺序：
        1. 当前 Python 环境（若 examples/tdx_formula 已被加入 sys.path）
        2. 向上遍历目录，定位 <项目根>/examples/tdx_formula
        3. extra_hints 提示路径（如公式目录所在目录，跨盘放置时兜底）
    """
    try:
        from tdx_engine import TdxEngine, TurtleStateMachine
        return TdxEngine, TurtleStateMachine
    except ImportError:
        pass

    here = Path(__file__).resolve().parent
    for parent in here.parents:
        pkg_dir = parent / "examples" / "tdx_formula"
        if pkg_dir.exists() and (pkg_dir / "tdx_engine.py").exists():
            sys.path.insert(0, str(pkg_dir))
            from tdx_engine import TdxEngine, TurtleStateMachine
            return TdxEngine, TurtleStateMachine

    for hint in (extra_hints or []):
        hp = Path(hint)
        if (hp / "tdx_engine.py").exists():
            sys.path.insert(0, str(hp))
            from tdx_engine import TdxEngine, TurtleStateMachine
            return TdxEngine, TurtleStateMachine

    raise ImportError(
        "无法定位 tdx_engine.py，请将 examples/tdx_formula 加入 sys.path，"
        "或把本策略文件与 tdx_engine.py 放在同一目录。"
    )


def _resolve_formula_path(raw: str) -> str:
    """把 json_path 参数归一化为绝对路径

    - 绝对路径 / ~ 家目录：直接规范化
    - 相对路径：依次尝试「当前工作目录 / 本策略文件目录及各级父目录
      （直至项目根）」为基准，因此无论从哪个常见目录填相对路径都能命中
    - 全部未命中时回退为「cwd/raw」的绝对形式（由 TdxEngine 抛错提示）
    """
    if not raw:
        raise ValueError("json_path 不能为空")

    p = Path(raw).expanduser()
    if p.is_absolute():
        return str(p)

    here = Path(__file__).resolve().parent
    candidates = [Path.cwd()]
    for base in [here, *here.parents]:
        candidates.append(base)
        # 已找到项目根（含 examples/tdx_formula 的目录），无需继续向上
        if (base / "examples" / "tdx_formula").exists():
            break

    # 去重后按序尝试（cwd 优先，其次策略文件所在目录逐级向上）
    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        cand = base / raw
        if cand.exists():
            return str(cand.resolve())

    return str((Path.cwd() / raw).resolve())


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
    capital: int = 10_000_000       # 风险预算基准资金（仅用于计算 unit 的名义基准，非实时净值）
    risk_level: float = 0.002       # 单笔单位仓位风险比例：1 unit 波动 1N 的亏损占 capital 的比例；
                                    # 配合 2N 止损，单 unit 最大亏损 = 2 * capital * risk_level（默认 0.2%）
    risk_levels: dict = {}          # vt_symbol -> risk_level 分档覆盖（未命中回退到 risk_level；趋势/成长股 0.004、低波动价值股 0.001）
    json_path: str = "G:/vnpy-4.4.0/examples/tdx_formula/formulas"  # 通达信公式目录
    formula_name: str = "海龟交易系统"   # 公式名（.tdx 文件名）
    formula_params: str = ""             # 公式参数覆盖，JSON 串（空=用策略参数自动映射）
    max_bars: int = 250                  # 日K缓存上限
    min_bars: int = 100                  # 预热根数（与原版 ArrayManager size=100 一致）

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
        "risk_level",
        "risk_levels",
        "json_path",
        "formula_name",
        "formula_params",
        "max_bars",
        "min_bars"
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

        # 加载通达信海龟公式（日K指标层）
        formula_dir = Path(_resolve_formula_path(self.json_path))
        TdxEngine, TurtleStateMachine = _import_tdx_engine([str(formula_dir.parent)])
        self.formula = TdxEngine(str(formula_dir)).get(self.formula_name)

        # 策略参数 -> 公式参数 自动映射（改策略参数无需手动同步公式）
        formula_params_dict = {
            "ENTRY": self.entry_window,
            "EXIT": self.exit_window,
            "NPERIOD": self.n_window,
            "CCIPERIOD": self.cci_window,
        }
        formula_params_dict.update(json.loads(self.formula_params) if self.formula_params else {})

        # 初始化信号字典
        self.signals: Dict[str, TurtleSignal] = {}
        for vt_symbol in vt_symbols:
            # 合约乘数（股票为1）
            contract_size: int = self.get_size(vt_symbol)
            # 每手股数（缺省按 1 手 = 1 股）
            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1))

            # 信号状态机（信号逻辑在引擎侧，公式只出指标）
            machine = TurtleStateMachine(
                cci_signal=self.cci_signal,
                unit_limit=self.unit_limit,
                trading_size=self.trading_size,
                allow_short=self.allow_short,
                formula=self.formula,
                formula_params=formula_params_dict,
                max_bars=self.max_bars,
                min_bars=self.min_bars,
            )

            self.signals[vt_symbol] = TurtleSignal(
                vt_symbol,
                contract_size,
                lot_size,
                self.capital,
                self.risk_levels.get(vt_symbol, self.risk_level),
                machine
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
    """海龟信号（信号逻辑由引擎 TurtleStateMachine 状态机实现）"""

    def __init__(
        self,
        vt_symbol: str,
        contract_size: int,
        lot_size: int,
        capital: int,
        risk_level: float,
        machine: "TurtleStateMachine"
    ) -> None:
        """构造函数"""
        # 参数
        self.vt_symbol: str = vt_symbol
        self.contract_size: int = contract_size
        self.lot_size: int = lot_size
        self.capital: int = capital
        self.risk_level: float = risk_level

        # 状态机（信号逻辑迁入引擎，公式只出指标）
        self.machine = machine

        # 变量
        self.target: int = 0
        self.unit: int = 0

    def on_bar(self, bar: BarData) -> None:
        """K线推送"""
        # 推送给状态机计算信号
        self.machine.step(bar)

        # 无仓位时更新unit
        if not self.target:
            if self.machine.n:
                unit: float = (self.capital * self.risk_level) / (
                    self.machine.n * self.contract_size
                )
            else:
                unit = 0.0

            # 按每手股数整手对齐，不足一手归0（美股 lot_size=1 时保底1手）
            unit = int(unit / self.lot_size) * self.lot_size
            if unit == 0 and self.lot_size == 1:
                unit = 1
            self.unit = unit

        # 获取状态机目标仓位
        self.target = int(self.machine.target * self.unit)

    def update_daily_bar(self, bar: BarData) -> None:
        """日K线推送"""
        self.machine.commit_daily(bar)

    def get_target(self) -> int:
        """获取信号"""
        return self.target
