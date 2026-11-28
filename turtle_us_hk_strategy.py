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

import csv
import json
import os
import shutil
import sys
import uuid
from collections import deque
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List

import numpy as np

from vnpy.trader.constant import Direction, Exchange, Interval, Offset, Status
from vnpy.trader.object import (
    AccountData,
    BarData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
)
from vnpy.trader.utility import ZoneInfo

from vnpy_portfoliostrategy import (
    StrategyEngine,
    StrategyTemplate,
)
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator


_TERMINAL_INTENT_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

_HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _hk_calendar():
    """港股交易日历（examples/tdx_formula/hk_calendar.py，与 tdx_engine 同目录）。

    tdx_engine 导入时该目录已加入 sys.path；不可用时返回 None，调用方回退为工作日。
    """
    try:
        import hk_calendar
    except ImportError:
        return None
    return hk_calendar


class PerSymbolPortfolioBarGenerator(PortfolioBarGenerator):
    """按标的独立收分钟 bar

    官方实现用全局 last_dt 判断换分钟：只要任意一个标的进入新分钟，
    就会把 bars 字典里所有标的的 bar 一起推出去。结果是同一分钟出现
    多次不完整的 bar（每次只带部分标的），策略会重复撤单/下单。

    这里改为每个标的维护自己的 last_tick_dt，只在该标的进入新分钟时
    推送它自己的上一根 bar；不同标的互不影响。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_tick_dt: Dict[str, datetime] = {}

    def update_tick(self, tick: TickData) -> None:
        if not tick.last_price:
            return

        vt_symbol: str = tick.vt_symbol
        tick_dt: datetime = tick.datetime
        last_dt: datetime | None = self._last_tick_dt.get(vt_symbol)

        # 忽略乱序/重复的旧 tick，避免把已收的 bar 又改回去
        if last_dt is not None and tick_dt < last_dt:
            return

        # 只在该标的自己的分钟变化时，推送它自己的上一根 bar
        if last_dt is not None and (
            tick_dt.replace(second=0, microsecond=0)
            != last_dt.replace(second=0, microsecond=0)
        ):
            finished: BarData | None = self.bars.pop(vt_symbol, None)
            if finished is not None:
                finished.datetime = finished.datetime.replace(
                    second=0, microsecond=0
                )
                self.on_bars({vt_symbol: finished})

        bar: BarData | None = self.bars.get(vt_symbol)
        if bar is None:
            bar = BarData(
                symbol=tick.symbol,
                exchange=tick.exchange,
                interval=Interval.MINUTE,
                datetime=tick_dt,
                gateway_name=tick.gateway_name,
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
                open_interest=tick.open_interest,
            )
            self.bars[vt_symbol] = bar
        else:
            bar.high_price = max(bar.high_price, tick.last_price)
            bar.low_price = min(bar.low_price, tick.last_price)
            bar.close_price = tick.last_price
            bar.open_interest = tick.open_interest
            bar.datetime = tick_dt

        last_tick: TickData | None = self.last_ticks.get(vt_symbol)
        if last_tick:
            bar.volume += max(tick.volume - last_tick.volume, 0)
            bar.turnover += max(tick.turnover - last_tick.turnover, 0)

        self.last_ticks[vt_symbol] = tick
        self._last_tick_dt[vt_symbol] = tick_dt


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


# 交易所后缀 -> 该市场的 tick 规则（策略方法名）
# 新增市场只需在这里加一行 + 在策略类里加对应的静态方法。
#   SEHK  港交所：11 档分段（见 _hk_tick）
#   SMART/NASDAQ/NYSE/AMEX/ARCA/BATS  美股：$1 以上 0.01，以下 0.0001（见 _us_tick）
_TICK_RULES: Dict[str, str] = {
    "SEHK": "_hk_tick",
    "SMART": "_us_tick",
    "NASDAQ": "_us_tick",
    "NYSE": "_us_tick",
    "AMEX": "_us_tick",
    "ARCA": "_us_tick",
    "BATS": "_us_tick",
}


class TurtleUsHkStrategy(StrategyTemplate):
    """美股/港股通用海龟策略"""

    author: str = "用Python的交易员"

    # 参数
    entry_window: int = 20          # 入场通道周期
    exit_window: int = 10           # 出场通道周期
    cci_window: int = 14            # CCI周期
    cci_signal: int = 20            # CCI信号阈值
    n_window: int = 20              # N值（ATR）周期
    use_corporate_actions: bool = False
    corporate_actions_file: str = ""
    corporate_actions_allow_partial: bool = False
    unit_limit: int = 4             # 最大开仓级别
    trading_size: int = 1           # 每级别交易手数
    price_add_ticks: int = 8        # 超价 = price_add_ticks * pricetick（price_add_pct=0 时生效）
    price_add_pct: float = 0.0      # 超价百分比（如 0.005 = 0.5%）；>0 时优先于 price_add_ticks
                                    # 为什么需要它：tick 占股价的比例随价位剧烈变化——
                                    #   港股 29.6 元，1 tick(0.05) 已是 0.17%；
                                    #   美股 $350，1 tick(0.01) 只有 0.0029%。
                                    # 固定 tick 数无法同时适配两个市场与不同价位。
    daily_end: str = "15:59"        # 日K合成收盘时间（交易所本地时间 HH:MM）
    allow_short: bool = True        # 是否允许做空
    lot_sizes: dict = {}            # vt_symbol -> 每手股数（港股如 {"700-HKD-STK.SEHK": 100}，缺省按 1）
    capital: int = 10_000_000       # 风险预算基准资金（仅用于计算 unit 的名义基准，非实时净值）
    auto_capital: bool = False      # 是否按 IB NetLiquidation 自动同步 capital（默认关闭）
    risk_level: float = 0.002       # 单笔单位仓位风险比例：1 unit 波动 1N 的亏损占 capital 的比例；
                                    # 配合 2N 止损，单 unit 最大亏损 = 2 * capital * risk_level（默认 0.2%）
    risk_levels: dict = {}          # vt_symbol -> risk_level 分档覆盖（未命中回退到 risk_level；趋势/成长股 0.004、低波动价值股 0.001）
    json_path: str = "G:/vnpy-4.4.0/examples/tdx_formula/formulas"  # 通达信公式目录
    formula_name: str = "海龟交易系统"   # 公式名（.tdx 文件名）
    formula_params: str = ""             # 公式参数覆盖，JSON 串（空=用策略参数自动映射）
    max_bars: int = 250                  # 日K缓存上限
    min_bars: int = 100                  # 预热根数（与原版 ArrayManager size=100 一致）

    # ---- 组合层风控参数（默认全部不启用，保证基线配置可逐字复现）----
    # P0-1 组合总敞口与单标权重
    max_gross_exposure: float = 0.0      # 总敞口上限 = Σ|持仓市值| / capital；0=不启用
    max_symbol_weight: float = 0.0       # 单标市值上限 = |单标的市值| / capital；0=不启用
    # P0-2 两级回撤熔断
    dd_level1: float = 0.0               # 一级回撤阈值（相对权益峰值，0.20=20%）；0=不启用
    dd_level1_scale: float = 0.5         # 一级回撤触发后，新开仓缩放系数
    dd_level2: float = 0.0               # 二级回撤阈值；0=不启用
    dd_level2_pause_days: int = 10       # 二级回撤触发后，暂停开新仓的交易日数
    # P1-1 波动率目标化
    vol_target: float = 0.0              # 目标年化波动率（0.15=15%）；0=不启用
    vol_lookback: int = 20               # 已实现波动率回看交易日数
    vol_scale_min: float = 0.3           # 波动率缩放系数下限
    vol_scale_max: float = 1.5           # 波动率缩放系数上限
    # P1-3 自适应选股：市场广度决定同时持仓数量上限（默认不启用）
    use_trend_filter: bool = False       # 是否启用「均线趋势过滤 + 动态持仓数量」
    trend_window: int = 60               # 趋势均线周期（日线，如 MA60）
    max_pos_strong: int = 10             # 广度强时最多同时持仓数（不超过股票池）
    max_pos_normal: int = 5              # 广度一般时最多持仓数
    max_pos_weak: int = 2                # 广度弱时最多持仓数
    breadth_strong: float = 0.7          # 广度 >= 该比例视为强（如 70% 标的在均线上）
    breadth_weak: float = 0.4            # 广度 < 该比例视为弱（如 40%）
    # P1-4 ADX 趋势强度过滤（默认不启用）：ADX 低于阈值说明无趋势，不追突破
    use_adx_filter: bool = False         # 是否启用 ADX 开仓过滤
    adx_window: int = 14                 # ADX 周期（Wilder 平滑）
    adx_threshold: float = 25.0          # ADX 阈值，低于此值不开新仓
    # P1-5 Efficiency Ratio 过滤（默认不启用）：净位移/总波动，同步衡量「趋势效率」
    use_er_filter: bool = False          # 是否启用 ER 开仓过滤
    er_window: int = 20                  # ER 回看交易日数
    er_threshold: float = 0.30           # ER 低于此值视为震荡，不开新仓
    # 条件放宽：广度 >= breadth_strong（强趋势）时改用更低的 ER 阈值；0=不启用
    # 需要 use_trend_filter=true 才有市场广度；用于「趋势市放宽、震荡市收紧」。
    er_threshold_strong: float = 0.0
    # P1-6 风险预算恒定（默认不启用）：持仓数越少、单只预算越大
    use_risk_budget: bool = False        # 是否启用动态单只仓位（基准数 / 当前持仓上限）
    risk_budget_base: int = 10           # 基准持仓数，通常等于股票池大小
    # P1-7 固定预算：每只标的建仓金额 = capital / 当前允许持仓数
    use_fixed_budget: bool = False       # 是否启用「按持仓数均分资金」的单只预算上限
    # P1-8 单笔加仓金额上限（默认不启用）：防止跳级时一次性买满
    max_addon_amount: float = 0.0        # 单笔加仓的最大金额（计价货币）；0=不启用
    # P1-9 相对强度过滤（默认不启用）：只买强于市场平均的标的
    use_rs_filter: bool = False          # 是否启用相对强度过滤
    rs_window: int = 60                  # 相对强度回看交易日数
    rs_threshold: float = 0.0            # 只买 RS 大于该阈值（0=强于市场平均；0.02=强 2%）
    # P1-10 多空分离名额（默认不启用）：弱市把名额倾斜给空头
    max_short_strong: int = 0            # 强市允许做空的标的数上限（0=不做空）
    max_short_normal: int = 0            # 中等市允许做空的标的数上限
    max_short_weak: int = 0              # 弱市允许做空的标的数上限
    max_long_weak: int = 0               # 弱市允许做多的标的数上限（0=沿用 max_pos_weak）
    max_short_addon_level: int = 0       # 做空单日加仓级数上限（0=不限制，1=每天最多加 1 级）
    # P1-12 分方向敞口上限（默认不启用）：多头与空头各用一份额度
    max_long_exposure: float = 0.0       # 多头敞口上限（capital 倍数），0=不启用分方向模式
    max_short_exposure: float = 0.0      # 空头敞口上限（capital 倍数），0=不启用分方向模式
    # P1-13 做空止损倍数（默认 2.0 = 海龟原版）：港股急跌后反弹 5~8%，2N 常被打掉
    short_stop_mult: float = 2.0         # 做空止损距离 = N × 该倍数
    # P1-14 空头「反弹过滤」（默认不启用）：不追急跌，等反弹接近均线再开空
    use_short_rebound_filter: bool = False
    short_rebound_ma: int = 5            # 反弹参考均线周期（日线）
    # P1-15 多空分开预算（默认不启用）：多头与空头各用一份预算池
    use_split_budget: bool = False       # 是否按多空分别计算单只预算
    long_budget_ratio: float = 0.5       # 多头预算占总资金比例（剩余归空头）
    # P1-16 敞口按持仓成本计算（默认不启用）：避免浮盈锁死加仓额度
    # 警告：与「无限制做空」叠加会失控。
    #   allow_short=True 且 max_short_* 全为 0 时，allowed_short 会退化为
    #   「所有跌破 MA60 的标的」（向后兼容），此时再开启本参数，空头会在下跌中
    #   不断加仓、反弹时死扛。实测（简单多空 + 成本敞口）：
    #       214.73% / -44.83% / Sharpe 0.42 / 915 笔（无成本敞口时为 289.40% / -25.76% / 1.05 / 190 笔）
    #   做空 + 成本敞口必须显式设置 max_short_* 并配合 MA 过滤，
    #   例如 2多8空+MA18：397.27% / -38.79% / 0.69 / 488 笔。
    use_cost_exposure: bool = False      # 用持仓成本替代市值计算敞口
    # P1-17 ER 分位数过滤（默认不启用）：用相对排名替代固定阈值
    use_er_percentile: bool = False      # 是否用 ER 历史分位替代固定阈值
    er_percentile: float = 0.5           # 只做 ER 高于该分位的标的（0.5=前 50%）
    er_percentile_window: int = 250      # ER 历史分位窗口（交易日）
    # P1-18 横截面分位（默认不启用）：当天所有标的中排名，替代逐标的历史分位
    er_cross_sectional: bool = False     # True=横截面（当天排名），False=时序（逐标的历史）
    # P0-3 状态持久化（默认不启用）：断线重启后恢复状态机，避免"失忆"错单
    # IB Gateway 每日 23:45 强制断开，重启后若不恢复 target/entry/n，会重复开仓或漏平仓
    use_state_persist: bool = False      # 是否在启动时恢复、每日收盘后保存状态
    state_dir: str = ""                  # 状态文件目录（空=当前工作目录下的 state/）

    # 实盘启动持仓校验（默认开启）
    # 目的：防止 state / portfolio_strategy_data 与券商实际持仓不一致时继续下单。
    #   check_positions_on_start=False -> 不校验（不推荐）
    #   sync_positions_from_broker=True -> 用券商持仓覆盖策略 pos_data 后继续
    #                                      适合确认过券商持仓、准备重新对齐时使用
    check_positions_on_start: bool = True
    sync_positions_from_broker: bool = False
    # 启动期条件自动同步：仅在「券商=0、策略≠0、无活动委托/隔离委托/未确认意图」
    # 时自动清理幽灵持仓；盘中不一致仍然锁定，需人工处理。
    auto_sync_positions_on_start: bool = False

    # 临时禁止新委托（默认关闭）：用于「同步持仓后先观察指标」的过渡期。
    # 打开时策略仍接收行情、更新净值/心跳，但不会发送任何新委托。
    block_new_orders: bool = False

    # 故障安全模式：只允许平仓/减仓，不允许开仓/加仓。
    # 应急平仓标记存在时会自动强制进入该模式（并且覆盖 block_new_orders）。
    close_only: bool = False

    # 账户级下单前资金保护（默认关闭，实盘配置必须显式开启）。
    # max_order_cash_ratio：本轮新增买单总额 / available 的上限。
    # min_margin_buffer：下单后要求保留的 available 比例，0.05=5%。
    max_order_cash_ratio: float = 0.0
    min_margin_buffer: float = 0.0
    max_daily_loss: float = 0.0            # 当日最大亏损比例，0=不启用

    # 多币种账户过滤：只接受匹配账户/币种的净值推送。
    account_id: str = ""
    base_currency: str = ""

    # ---- 阶段 2：行情质量 / 时段 / 券商对账 ----
    # 默认关闭，保证回测逐字复现；实盘配置由 validate_live_config 强制开启。
    quote_max_age_seconds: float = 0.0        # tick 最大允许延迟秒数，0=不检查
    price_max_jump_pct: float = 0.0           # 相对上一笔 tick 最大跳价比例，0=不检查
    enforce_trading_session: bool = False     # 是否只允许常规交易时段 tick
    outside_rth: bool = False                 # 是否允许盘前/盘后；同时控制 IB outsideRth
    outside_rth_symbols: list = []            # 可选：仅这些 IB 合约允许盘外成交
    order_tif: str = "DAY"                    # IB 订单有效期：DAY / GTC / IOC
    market_data_fallback_seconds: float = 45.0  # 实时行情无 tick 时自动降级等待秒数
    position_check_interval_minutes: float = 0.0  # 券商持仓周期对账间隔，0=不启用
    # 数据新鲜度闸门（默认关闭，保证回测逐字复现）：
    # 数据库缺交易日时，启动回放的历史日线会停在缺口之前，ENTRYUP 与 CCI
    # 都基于过期通道 —— 价格一旦越过旧通道就会被误判为「突破」。
    # 实盘建议 7（覆盖周末与短假）；滞后超过该天数时禁止新开仓。
    max_daily_gap_days: int = 0
    max_daily_gap_weekdays: int = 1           # 允许的交易日缺口：1 = 最后日线必须是上一交易日

    # ---- 实盘执行保护（只在实盘生效，回测逐字复现）----
    max_orders_per_symbol_day: int = 30       # 单标的每日开仓/加仓委托上限（平仓不计），0=不限
    max_orders_per_minute: int = 10           # 全局每分钟委托上限，0=不限
    max_rejects_per_symbol_day: int = 3       # 单标的当日拒单达到该次数即冻结该标的，0=不限
    max_price_deviation: float = 0.03         # 开仓限价相对最新买卖价的最大偏离，0=不检查
    max_quote_age_for_open: float = 60.0      # 开仓要求最新有效 tick 不超过该秒数，0=不检查
    quote_silence_seconds: float = 180.0      # 交易时段内单标的无有效 tick 超过该秒数即告警，0=不检查
    capital_mismatch_tolerance: float = 0.2   # auto_capital=false 时 capital 与账户净值最大相对偏差，0=不检查

    # 重置净值基准（默认关闭）：把 baseline/peak 清空，下次有效净值重新锚定。
    # 仅在确认历史 baseline 已被污染、且准备重新开始时临时设为 true。
    reset_equity_baseline: bool = False
    # 实盘运维（默认关闭，不影响回测）
    export_fills_daily: bool = False     # 每日收盘自动导出执行监控（覆盖写）
    fills_dir: str = ""                  # 执行监控输出目录（空=当前目录下的 output/）
    log_version_info: bool = True        # 启动时记录代码 commit 与配置指纹（版本锁定）
    # 初始化加载天数
    #   0（默认）= 自动按引擎类型选择：回测 30、实盘 400
    #   >0       = 强制使用该值（高级用法，一般不需要）
    # 说明：load_bars 在回测里是「预热期天数」（跳过前 N 天不计成绩），
    #       在实盘里是「从数据库加载的历史天数」（需覆盖 250 根日线）。
    init_load_days: int = 0
    # 实盘可观测性（默认关闭，不影响回测）
    heartbeat_enabled: bool = False      # 心跳：每分钟写一次状态文件，面板据此判断是否存活
    heartbeat_dir: str = ""              # 心跳目录（空=当前目录 state/）
    alert_drawdown: float = 0.0          # 回撤告警阈值（如 0.30 表示 30%），0=不启用
    emergency_flatten_file: str = ""     # 应急平仓标记文件：存在则清空目标仓位（只平仓不开新）
    # 换池过渡期：遗留标的只允许减仓/平仓，禁止加仓或开新仓
    # 用途：从旧标的池切换到新池时，旧池持仓平滑退出，避免在不合适点位被迫全平
    legacy_symbols: list = []
    # 时点股票池（研究用，默认关闭）：JSON 文件，periods=[{start,end,symbols}]。
    # 非成员只禁止新开仓（已持仓照常离场）；市场广度与做多排名只在成员内计算。
    # 由 scripts/pit_universe.py 生成，用于量化选股/幸存者偏差（S4-02）。
    universe_file: str = ""

    # ---- 离场质量（默认全部关闭，保证基线配置可逐字复现；仅多头）----
    # 盈利保护：持仓最高价达到「参考成本 + trigger×N」后，次日起止损上移到
    # 「参考成本 + offset×N」，只升不降；参考成本=各级触发价等权平均，不依赖券商成本。
    # 0=不启用。研究结论见 README_OPTIMIZE_PLAN.md（离场质量），上线前须完成回测与 Paper 对拍。
    profit_lock_trigger_n: float = 0.0
    profit_lock_offset_n: float = 1.0
    close_confirm_mode: str = "off"
    close_confirm_disaster_n: float = 1.0
    initial_stop_n: float = 0.0
    initial_stop_until_mfe_n: float = 1.0
    fail_breakout_days: int = 0
    fail_breakout_offset_n: float = 0.0
    time_stop_days: int = 0
    time_stop_min_mfe_n: float = 1.0
    # 开盘跳空穿越止损：off 不干预；log 只在离场记录里标注；
    # delay 延后 gap_exit_delay_minutes 分钟再离场，期间跌破「止损 − panic_n×N」仍立即离场。
    gap_exit_mode: str = "off"
    gap_exit_delay_minutes: int = 30
    gap_exit_panic_n: float = 1.5

    # 名称列表
    parameters = [
        "entry_window",
        "exit_window",
        "cci_window",
        "cci_signal",
        "n_window",
        "use_corporate_actions",
        "corporate_actions_file",
        "corporate_actions_allow_partial",
        "unit_limit",
        "trading_size",
        "price_add_ticks",
        "price_add_pct",
        "daily_end",
        "allow_short",
        "lot_sizes",
        "capital",
        "auto_capital",
        "risk_level",
        "risk_levels",
        "json_path",
        "formula_name",
        "formula_params",
        "max_bars",
        "min_bars",
        # 组合层风控（P0-1 / P0-2 / P1-1）
        "max_gross_exposure",
        "max_symbol_weight",
        "dd_level1",
        "dd_level1_scale",
        "dd_level2",
        "dd_level2_pause_days",
        "vol_target",
        "vol_lookback",
        "vol_scale_min",
        "vol_scale_max",
        # P1-3 自适应选股
        "use_trend_filter",
        "trend_window",
        "max_pos_strong",
        "max_pos_normal",
        "max_pos_weak",
        "breadth_strong",
        "breadth_weak",
        # P1-4 ADX 趋势强度过滤
        "use_adx_filter",
        "adx_window",
        "adx_threshold",
        # P1-5 Efficiency Ratio 过滤
        "use_er_filter",
        "er_window",
        "er_threshold",
        "er_threshold_strong",
        # P1-6 风险预算恒定
        "use_risk_budget",
        "risk_budget_base",
        # P1-7 固定预算（按持仓数均分资金）
        "use_fixed_budget",
        # P1-8 单笔加仓金额上限
        "max_addon_amount",
        # P1-9 相对强度过滤
        "use_rs_filter",
        "rs_window",
        "rs_threshold",
        # P1-10 多空分离名额
        "max_short_strong",
        "max_short_normal",
        "max_short_weak",
        "max_long_weak",
        # P1-11 做空单日加仓级数上限
        "max_short_addon_level",
        # P1-12 分方向敞口上限
        "max_long_exposure",
        "max_short_exposure",
        # P1-13 做空止损倍数
        "short_stop_mult",
        # P1-14 空头反弹过滤
        "use_short_rebound_filter",
        "short_rebound_ma",
        # P1-15 多空分开预算
        "use_split_budget",
        "long_budget_ratio",
        # P1-16 敞口按成本计算
        "use_cost_exposure",
        # P1-17 ER 分位数
        "use_er_percentile",
        "er_percentile",
        "er_percentile_window",
        # P1-18 横截面分位
        "er_cross_sectional",
        # P0-3 状态持久化
        "use_state_persist",
        "state_dir",
        # 实盘启动持仓校验
        "check_positions_on_start",
        "sync_positions_from_broker",
        "auto_sync_positions_on_start",
        "block_new_orders",
        "close_only",
        "max_order_cash_ratio",
        "min_margin_buffer",
        "max_daily_loss",
        "account_id",
        "base_currency",
        "quote_max_age_seconds",
        "price_max_jump_pct",
        "enforce_trading_session",
        "outside_rth",
        "outside_rth_symbols",
        "order_tif",
        "market_data_fallback_seconds",
        "position_check_interval_minutes",
        "max_daily_gap_days",
        "max_daily_gap_weekdays",
        "max_orders_per_symbol_day",
        "max_orders_per_minute",
        "max_rejects_per_symbol_day",
        "max_price_deviation",
        "max_quote_age_for_open",
        "quote_silence_seconds",
        "capital_mismatch_tolerance",
        "reset_equity_baseline",
        # 实盘运维
        "export_fills_daily",
        "fills_dir",
        "log_version_info",
        "init_load_days",
        # 实盘可观测性
        "heartbeat_enabled",
        "heartbeat_dir",
        "alert_drawdown",
        "emergency_flatten_file",
        "legacy_symbols",
        "universe_file",
        # 离场质量（默认关闭）
        "profit_lock_trigger_n",
        "profit_lock_offset_n",
        "close_confirm_mode",
        "close_confirm_disaster_n",
        "initial_stop_n",
        "initial_stop_until_mfe_n",
        "fail_breakout_days",
        "fail_breakout_offset_n",
        "time_stop_days",
        "time_stop_min_mfe_n",
        "gap_exit_mode",
        "gap_exit_delay_minutes",
        "gap_exit_panic_n",
    ]

    # 不只是显示：variables 会进入引擎的 portfolio_strategy_data.json。
    # 这些字段与自定义 state 文件一起保存，供重启恢复/对账使用。
    variables = [
        "equity",
        "peak_equity",
        "current_drawdown",
        "pause_until",
        "new_open_scale",
        "order_ledger",
        "account_balance",
        "account_available",
        "_baseline_equity",
        "_positions_verified",
        "_trading_blocked",
        "funds_blocked",
        "safety_mode",
    ]

    STATE_SCHEMA_VERSION: int = 2

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
            # 合约乘数（股票为1）；新池在 GUI 预加载时可能还没有合约缓存
            contract_size: int = int(self.get_size(vt_symbol) or 1)
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
                short_stop_mult=self.short_stop_mult,
                profit_lock_trigger_n=self.profit_lock_trigger_n,
                profit_lock_offset_n=self.profit_lock_offset_n,
                close_confirm_mode=self.close_confirm_mode,
                close_confirm_disaster_n=self.close_confirm_disaster_n,
                initial_stop_n=self.initial_stop_n,
                initial_stop_until_mfe_n=self.initial_stop_until_mfe_n,
                fail_breakout_days=self.fail_breakout_days,
                fail_breakout_offset_n=self.fail_breakout_offset_n,
                time_stop_days=self.time_stop_days,
                time_stop_min_mfe_n=self.time_stop_min_mfe_n,
                gap_exit_mode=self.gap_exit_mode,
                gap_exit_delay_minutes=self.gap_exit_delay_minutes,
                gap_exit_panic_n=self.gap_exit_panic_n,
            )

            self.signals[vt_symbol] = TurtleSignal(
                vt_symbol,
                contract_size,
                lot_size,
                self.capital,
                self.risk_levels.get(vt_symbol, self.risk_level),
                machine
            )
            self.signals[vt_symbol].corporate_action_unit_frozen = self.use_corporate_actions

        # 初始化目标字典
        self.targets: Dict[str, int] = {}

        # 离场事件日志：状态机每次决定离场时追加（回测与实盘共用，回测结束由 backtest_generic 导出）
        self.exit_log: List[dict] = []

        # 初始化组合K线生成器（tick -> 分钟截面 -> 盘中日K截面）
        # 使用按标的独立收 bar 的版本，避免一个标的换分钟把其他标的
        # 未完成的 bar 一起推出去（同一分钟出现多次残缺 bar）。
        self.pbg: PortfolioBarGenerator = PerSymbolPortfolioBarGenerator(
            self.on_bars,
            on_window_bars=self.on_daily_bars,
            interval=Interval.DAILY,
            daily_end=self.daily_end_time
        )

        # ---- 组合层风控状态（P0-1 / P0-2 / P1-1）----
        self.equity: float = float(self.capital)        # 组合净值（实盘优先用账户现金+持仓市值）
        self.peak_equity: float = float(self.capital)   # 净值历史峰值
        self.current_drawdown: float = 0.0              # 当前回撤（相对峰值）

        # ---- 账户资金（由 IB 通过 on_account 推送）----
        # 重要：vnpy_ib 把 IB 的 NetLiquidation / NetLiquidationByCurrency
        # 映射到 account.balance，因此 account.balance 是「净值」而不是现金。
        # 不要再把持仓市值加进去，否则会重复计算。
        self.account_balance: float = 0.0               # 账户净值（NetLiquidation）
        self.account_available: float = 0.0             # 可用资金
        self.account_cash: float = 0.0                  # 兼容旧字段（不再用于净值）

        # 策略基准净值：首次拿到账户数据时记录，之后持久化。
        # 用于算「策略自启动以来的盈亏」，与账户历史盈亏区分开。
        self._baseline_equity: float = 0.0

        # 净值锚点：只有确认持仓与券商一致后才初始化峰值/基准，
        # 避免用错误持仓算出的虚高净值污染回撤与策略盈亏。
        self._equity_anchor_ready: bool = False
        self._positions_verified: bool = False
        self._trading_blocked: bool = False
        self._block_logged: bool = False

        # ---- P0 订单账本 / 故障安全状态 ----
        # order_ledger: intent_id -> 交易意图与订单状态，是「幂等 + 预留仓位」
        # 的唯一来源。active_orderids 只表示本地看到的 id，不能当券商权威状态。
        self.order_ledger: Dict[str, dict] = {}
        self._orderid_intent: Dict[str, str] = {}
        self._symbol_active_intent: Dict[str, str] = {}
        self.quarantine_orderids: set[str] = set()
        self.funds_blocked: bool = False
        self.safety_mode: str = "normal"
        self._intent_seq: int = 0
        self._last_state_save: datetime | None = None
        self._emergency_logged: bool = False
        self._recovery_pending: bool = True
        self._recovery_logged: bool = False
        self._quarantine_logged: bool = False
        self._funds_logged: bool = False

        # ---- 阶段 2：行情质量 / 账户过滤 / 周期对账状态 ----
        self._broker_position_cache: Dict[str, int] = {}
        self._broker_feed_ready: bool = False
        self._last_position_check: datetime | None = None
        self._positions_mismatch_logged: bool = False
        self._target_reconciled: bool = True             # 目标/持仓反推是否已完成（需 N 就绪）
        self._reconcile_pending_logged: bool = False     # 「等待 N 就绪」是否已提示
        self._account_reject_logged: bool = False
        self._quote_reject_logged: set[str] = set()
        self._invalid_tick_count: int = 0
        self._last_quote_reject_reason: str = ""

        # ---- 数据新鲜度状态（防止历史数据缺口导致假突破）----
        self._last_daily_date: date | None = None       # 最近一根日K的交易日
        self._data_stale_logged: bool = False           # 陈旧告警是否已打印

        # ---- 心跳观测字段（供控制面板仪表盘展示）----
        self._daily_nav: float | None = None            # 上一交易日收盘净值（算今日盈亏）
        self._last_tick_time: str = ""                  # 最近 tick 时间（判断行情是否中断）
        self._last_bar_time: str = ""                   # 最近完成 bar 的时间
        self._tick_count: int = 0                       # 本次运行累计 tick 数（排查行情链路）
        self.prev_prices: Dict[str, float] = {}         # 上一根bar收盘价（用于市值递推）
        self.pause_until: datetime | None = None        # 二级熔断暂停开仓截止时间
        self.daily_closes: Dict[str, deque] = {}        # vt_symbol -> 日收盘价序列（波动率计算）
        self.realized_vol: float = 0.0                  # 组合已实现年化波动率
        self.new_open_scale: float = 1.0                # 本期新开仓综合缩放系数

        # P1-3 自适应选股状态
        self.allowed_symbols: set = set(vt_symbols)     # 允许新开仓的标的集合（多空并集）
        self.market_breadth: float = 1.0                # 市场广度（均线上方标的占比）
        self.max_positions: int = 0                     # 当前允许的同时持仓数上限

        # P1-10 多空分离状态
        self.allowed_long: set = set(vt_symbols)        # 允许开多的标的
        self.allowed_short: set = set()                 # 允许开空的标的
        self.deviation: Dict[str, float] = {}           # vt_symbol -> 相对 MA 偏离度（排名依据）

        # P1-15/P1-16/P1-17 状态
        self.long_cap: int = 0                          # 当前多头名额（P1-15 用）
        self.short_cap: int = 0                         # 当前空头名额（P1-15 用）
        self.cost_basis: Dict[str, float] = {}          # vt_symbol -> 持仓成本（买入为正，做空为负）
        self.er_history: Dict[str, deque] = {}          # vt_symbol -> ER 历史序列（P1-17 用）
        self.er_pass: set = set()                       # P1-18 横截面分位通过的标的

        # P0-2 订单执行监控：记录每笔的信号价、委托价、成交价与滑点
        # 目的是把「回测假设的 5bp 滑点」与「实盘真实滑点」对齐，评估成本侵蚀
        self.fill_records: List[dict] = []              # 成交记录（含滑点，单位 bps）
        self.signal_prices: Dict[str, float] = {}       # vt_symbol -> 最近一次下单的信号价
        self.order_intents: Dict[str, int] = {}         # vt_symbol -> 累计下单股数（算成交率）
        self.fill_volumes: Dict[str, int] = {}          # vt_symbol -> 累计成交股数

        # 实盘/回测标识（on_init 里按引擎类型设置）
        # True = 实盘（做导出/心跳/持久化）；False = 回测（全部跳过，避免污染 output）
        # 默认 False：on_init 之前（例如 bootstrap 的订单恢复回调先到）不得被当成
        # 实盘去写状态文件——2026-11-20 22:01 的状态清零就是这样发生的。
        self.live_mode: bool = False

        # ---- 状态持久化保护：on_start 加载完状态之前禁止写盘 ----
        self._state_ready: bool = False
        self._state_guard_logged: bool = False
        self._state_write_errors: int = 0
        self._state_write_failed: bool = False
        self._state_backup_at: datetime | None = None

        # ---- 券商断线 / 活动委托恢复 ----
        self._disconnect_block: bool = False
        self._pre_disconnect: dict = {}
        self._recovery_completed_at: datetime | None = None

        # ---- 持有保护 / 执行保护 / 行情静默 ----
        # 持有保护：对账发现持仓超级别、非整 unit、方向异常时进入——
        # 禁止加仓、不自动减仓，状态机给出止损/离场（target=0）时照常平仓。
        self._hold_symbols: Dict[str, str] = {}
        self._order_counters: dict = {}               # 按交易日：下单数 / 拒单数 / 冻结标的
        self._order_times: deque = deque()            # 最近 60 秒的发单时间
        self._jump_pending: Dict[str, list] = {}      # 跳价待确认 [价格, 连续笔数]
        self._last_valid_tick_wall: Dict[str, datetime] = {}
        self._silent_symbols: set[str] = set()
        self._capital_mismatch: bool = False
        self._last_daily_dates: Dict[str, date] = {}  # 逐标的最近日K日期
        self._throttle_log_at: Dict[str, datetime] = {}
        self._eod_checked_date: str = ""
        self._daily_finalized_date: str = ""
        self._suppress_orders: bool = False
        self._emergency_since: datetime | None = None
        self._global_cancel_sent: bool = False
        self._started_wall: datetime | None = None
        # 外部告警钩子：bootstrap 注入 alert.send_alert(level, key, msg)
        self.alert_hook = None

        # 实盘可观测性状态
        self._last_heartbeat_key: str = ""              # 上次心跳的分钟键（限频，避免每根 bar 写文件）
        self._alert_sent_dd: bool = False               # 回撤告警是否已发送（避免刷屏）
        self._heartbeat_write_errors: int = 0           # 心跳写盘失败累计（阶段 3：失败可见）

        # 注：不做撤单去重（见 _prune_ghost_orders 的说明）。
        # 撤单风暴的根因是「价格未对齐 pricetick 被 IB 拒单 → id 残留」，
        # 已在 _round_price + _prune_ghost_orders 中修复。

        # P1-4 ADX 状态
        self.daily_hlc: Dict[str, deque] = {}           # vt_symbol -> (high, low, close) 序列
        self.adx_values: Dict[str, float] = {}          # vt_symbol -> 当前 ADX 值

        # P1-9 相对强度状态
        self.rs_values: Dict[str, float] = {}           # vt_symbol -> N 日收益减市场平均

        # P1-5 ER 状态
        self.er_values: Dict[str, float] = {}           # vt_symbol -> 当前 Efficiency Ratio

    def on_init(self):
        """初始化"""
        self.write_log("策略初始化")

        # 重启后用于确认加载的是“按标的独立收 bar”的新实现
        self.write_log(
            f"[bar] 分钟bar生成器={type(self.pbg).__name__}（按标的独立）"
        )

        # 加载历史数据天数：按引擎类型自动选择，避免维护两份配置。
        # 回测引擎：days 是「预热期天数」，保持 30 以复现基线（改大会平移回测区间）。
        # 实盘引擎：days 是「从数据库加载的历史天数」，必须覆盖 250 根日线（约 400 自然日），
        #           否则 machine.inited 一直为 False，策略不会开仓。
        from vnpy_portfoliostrategy.backtesting import BacktestingEngine

        # 实盘/回测自动识别：实盘才做导出、心跳、状态持久化。
        # 这样回测和实盘可以共用同一份配置——回测不会往 output/ 里写实盘产物，
        # 也不用再维护 production.json / production_live.json 两份几乎相同的文件。
        is_backtest: bool = isinstance(self.strategy_engine, BacktestingEngine)
        self.live_mode = not is_backtest
        self._load_universe()
        if self.use_corporate_actions:
            from corporate_actions import DEFAULT_ACTION_FILE, ActionBook

            self._ca_book = ActionBook(self.corporate_actions_file or DEFAULT_ACTION_FILE)
            if self.live_mode:
                today = datetime.now(_HK_TZ).date()
                errors = self._ca_book.coverage_errors(self.vt_symbols, today - timedelta(days=400), today)
                if self.corporate_actions_allow_partial or errors or self._ca_book.data_basis.get("status") != "complete":
                    raise ValueError("实盘公司行动/行情口径未完整核验，禁止启用时点复权")
            elif not getattr(self.strategy_engine, "ca_raw_prices", False):
                raise ValueError("时点复权回测必须使用 CorporateActionBacktestingEngine 的原价输入")

        if self.init_load_days > 0:
            days: int = self.init_load_days
        elif is_backtest:
            days = 30
        else:
            days = 400

        self.write_log(f"初始化加载 {days} 天历史数据")
        self.load_bars(days)

        # 兜底预热：若「分钟线 → 日线」合成链路没有触发（pbg 只按
        # daily_end 精确匹配推送），状态机会一直是 inited=False、N=0，
        # calculate_unit() 返回 0 —— 策略既不能开仓，也无法反推持仓级别。
        # 这里直接从数据库分钟线合成日线补齐，幂等（已预热则跳过）。
        self._warmup_machine_from_db(days)

    def _load_universe(self) -> None:
        """读取时点股票池（universe_file）；未配置时不限制。"""
        self._universe_periods: List[tuple] = []
        if not self.universe_file:
            return
        import json
        from pathlib import Path

        data = json.loads(Path(self.universe_file).read_text(encoding="utf-8"))
        for period in data.get("periods", []):
            self._universe_periods.append((
                date.fromisoformat(period["start"]),
                date.fromisoformat(period["end"]),
                frozenset(period["symbols"]),
            ))
        self._universe_periods.sort(key=lambda p: p[0])
        self.write_log(
            f"[股票池] 已加载时点股票池 {len(self._universe_periods)} 期：{self.universe_file}"
        )

    def _universe_members(self, day: date | None) -> frozenset | None:
        """day 当天的成员；未配置股票池返回 None（不限制），不在任何区间返回空集。"""
        periods: List[tuple] = getattr(self, "_universe_periods", None) or []
        if not periods or day is None:
            return None
        for start, end, symbols in periods:
            if start <= day < end:
                return symbols
        return frozenset() if day >= periods[0][0] else periods[0][2]

    def on_start(self):
        """启动"""
        self.write_log("策略启动")

        # 版本锁定：记录代码 commit 与配置指纹，便于事后归因
        if self.log_version_info:
            self._log_version_info()

        # P0-3：优先恢复上次退出时的状态机状态（防断线重启后"失忆"）
        if self.use_state_persist and (self.live_mode or not self.use_corporate_actions):
            self._load_state()

        # 实盘：非持有标的的状态机回到空仓 + 最新通道（回放的模拟持仓不算数）
        if self.live_mode:
            self._align_unheld_machines()

        # 状态已加载（或未启用持久化）：从此刻起才允许写盘
        self._state_ready = True
        self._started_wall = datetime.now(_HK_TZ)

        # 一次性重置被污染的净值基准（baseline/peak）
        if self.live_mode and self.reset_equity_baseline:
            self._baseline_equity = 0.0
            self.peak_equity = 0.0
            self._equity_anchor_ready = False
            self.write_log("[state] reset_equity_baseline=true，已清空净值基准与峰值")

        # 实盘启动持仓校验：不一致就禁止下单，避免按错误持仓继续交易。
        # 目标/持仓反推必须等日线回放完成、N 有效之后再做（见
        # _maybe_reconcile_target_state）：on_start 阶段 machine.n 还是 0，
        # calculate_unit() 返回 0，此时反推会被判成「无法计算 unit」。
        if self.live_mode:
            self._verify_positions_on_start()
            self._target_reconciled = False
        else:
            self._recovery_pending = False
            self._target_reconciled = True

    @staticmethod
    def _aggregate_daily_bars(
        vt_symbol: str, bars: List[BarData], keep: int = 250
    ) -> List[BarData]:
        """把分钟线按交易日合成日线（只保留最近 keep 根）。"""
        groups: Dict[Any, list] = {}
        for bar in bars:
            day = bar.datetime.date()
            agg = groups.get(day)
            if agg is None:
                groups[day] = [
                    bar.open_price,
                    bar.high_price,
                    bar.low_price,
                    bar.close_price,
                    bar.volume,
                    bar.turnover,
                    bar.open_interest,
                ]
                continue
            agg[1] = max(agg[1], bar.high_price)
            agg[2] = min(agg[2], bar.low_price)
            agg[3] = bar.close_price
            agg[4] += bar.volume
            agg[5] += bar.turnover
            agg[6] = bar.open_interest

        result: List[BarData] = []
        symbol, exchange = bars[0].symbol, bars[0].exchange
        gateway_name = bars[0].gateway_name
        for day in sorted(groups):
            agg = groups[day]
            result.append(
                BarData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=datetime(day.year, day.month, day.day),
                    interval=Interval.DAILY,
                    volume=agg[4],
                    turnover=agg[5],
                    open_interest=agg[6],
                    open_price=agg[0],
                    high_price=agg[1],
                    low_price=agg[2],
                    close_price=agg[3],
                    gateway_name=gateway_name,
                )
            )
        return result[-keep:]

    def _warmup_machine_from_db(self, days: int) -> None:
        """用数据库分钟线合成日线，补齐状态机预热（幂等）。

        只处理尚未 inited 的标的：正常环境由 pbg 合成日线完成预热，
        这里仅作为兜底，避免 N=0 导致策略无法交易。
        """
        if not self.live_mode:
            return

        pending = [
            (vt_symbol, signal)
            for vt_symbol, signal in self.signals.items()
            if not signal.machine.inited
        ]
        if not pending:
            return

        self.write_log(
            f"[预热] {len(pending)} 个标的状态机未预热，用分钟线合成日线补齐"
        )
        for vt_symbol, signal in pending:
            try:
                bars = self.load_bar(vt_symbol, days, Interval.MINUTE)
            except Exception as exc:
                self.write_log(f"[预热] {vt_symbol} 加载分钟线失败: {exc}")
                continue
            if not bars:
                continue

            if self.use_corporate_actions:
                from corporate_actions import map_raw_bar

                bars = [map_raw_bar(item, self._ca_book) for item in bars]
                self._state_dict("_ca_dates").pop(vt_symbol, None)
                self._state_dict("_ca_raw_closes").pop(vt_symbol, None)
                self._state_dict("_last_daily_dates").pop(vt_symbol, None)
                for event in self._ca_book.actions:
                    if event.vt_symbol == vt_symbol:
                        self._state_dict("_ca_factors").pop(event.id, None)
                for name in ("opens", "highs", "lows", "closes", "volumes"):
                    getattr(signal.machine.buf, name).clear()
                signal.machine.target = 0
                signal.machine.n = 0.0
                signal.target = signal.unit = 0
                self.daily_closes.pop(vt_symbol, None)
                self.daily_hlc.pop(vt_symbol, None)
            daily = self._aggregate_daily_bars(vt_symbol, bars)
            if self.use_corporate_actions:
                daily = [item for item in daily if item.datetime.date() < self._hk_now().date()]
            for dbar in daily:
                if self.use_corporate_actions:
                    self._prepare_corporate_actions({vt_symbol: dbar})
                    self.on_daily_bars({vt_symbol: dbar})
                else:
                    signal.update_daily_bar(
                        dbar, bool(getattr(self, "pos_data", {}).get(vt_symbol, 0))
                    )

            if daily:
                last_day = daily[-1].datetime.date()
                if self._last_daily_date is None or last_day > self._last_daily_date:
                    self._last_daily_date = last_day
                self._state_dict("_last_daily_dates")[vt_symbol] = last_day
                if self.use_corporate_actions:
                    for name in ("daily_bars", "bars", "last_ticks"):
                        values = getattr(self.pbg, name, {})
                        old = values.get(vt_symbol)
                        if old is not None and old.datetime.date() <= last_day:
                            values.pop(vt_symbol, None)
                    last_ticks = getattr(self.pbg, "_last_tick_dt", {})
                    if vt_symbol in last_ticks and last_ticks[vt_symbol].date() <= last_day:
                        last_ticks.pop(vt_symbol)

            self.write_log(
                f"[预热] {vt_symbol} 日线 {len(daily)} 根，"
                f"inited={signal.machine.inited} "
                f"N={signal.machine.n:.4f}"
            )

    def _log_version_info(self) -> None:
        """启动时记录代码版本与配置指纹（版本锁定）

        目的：三个月后回看「当时为什么亏」，能精确知道跑的是哪个 commit、哪份配置。
        git 命令失败（不在仓库里）不影响策略启动。
        """
        import hashlib
        import subprocess

        commit: str = "unknown"
        try:
            commit = subprocess.check_output(
                ["git", "-C", r"C:\Users\sandytaoli\strategies", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode().strip()
        except Exception:
            pass

        try:
            payload: str = json.dumps(
                {k: getattr(self, k) for k in self.parameters if hasattr(self, k)},
                sort_keys=True,
                default=str,
            )
        except Exception:
            payload = "{}"

        config_hash: str = hashlib.md5(payload.encode()).hexdigest()[:8]
        self.write_log(
            f"[version] strategy={self.strategy_name} commit={commit} config_hash={config_hash}"
        )
        self.write_log(f"[version] params={payload}")

    # ------------------------------------------------------------------
    # 通用小工具：惰性状态容器 / 限频日志 / 告警 / 时钟
    # ------------------------------------------------------------------
    def _state_dict(self, name: str) -> dict:
        """取实例上的 dict 状态，不存在时创建（兼容 object.__new__ 构造的测试对象）。"""
        value = self.__dict__.get(name)
        if value is None:
            value = {}
            self.__dict__[name] = value
        return value

    def _state_set(self, name: str) -> set:
        value = self.__dict__.get(name)
        if value is None:
            value = set()
            self.__dict__[name] = value
        return value

    def _log_throttled(self, key: str, msg: str, seconds: float = 600.0) -> bool:
        """同一 key 在 seconds 内只写一次日志；返回本次是否写出。"""
        stamps: dict = self._state_dict("_throttle_log_at")
        now: datetime = datetime.now()
        last: datetime | None = stamps.get(key)
        if last is not None and (now - last).total_seconds() < seconds:
            return False
        stamps[key] = now
        self.write_log(msg)
        return True

    def _er_threshold_now(self) -> float:
        """当前生效的 ER 阈值：强广度时放宽到 er_threshold_strong。

        需要 use_trend_filter=true 才有市场广度；关闭趋势过滤时该参数不生效。
        """
        if (
            self.er_threshold_strong > 0
            and self.use_trend_filter
            and self.market_breadth >= self.breadth_strong
        ):
            return self.er_threshold_strong
        return self.er_threshold

    def _record_signal_blocked(
        self,
        vt_symbol: str,
        day: date,
        price: float,
        desired: int,
        target: int,
        pos: int,
        reason: str,
    ) -> None:
        """P0 诊断：有信号却最终没下单时，按「标的×交易日」记录一次原因。

        只写诊断（日志 + CSV），不改变任何交易行为。同一标的同一天只记第一条，
        因此看到的是当天最先生效的原因。
        """
        try:
            key = f"{day.isoformat()}|{vt_symbol}"
            seen = self._state_set("_blocked_probe_seen")
            if key in seen:
                return
            seen.add(key)
        except Exception:
            key = ""
        lot: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
        signal = getattr(self, "signals", {}).get(vt_symbol)
        level: int = int(getattr(getattr(signal, "machine", None), "target", 0) or 0)
        if not self.fills_dir:
            self._log_throttled(
                f"no_open_{vt_symbol}_{reason}",
                f"[信号] {vt_symbol} 目标 {desired} 股、每手 {lot} 股，未下单：{reason}（级别 {level}）",
                3600,
            )
            return
        try:
            base = Path(self.fills_dir)
            base.mkdir(parents=True, exist_ok=True)
            path = base / "signal_blocked.csv"
            new: bool = not path.exists()
            with path.open("a", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                if new:
                    writer.writerow(["date", "vt_symbol", "desired", "target", "pos", "price",
                                     "lot", "lot_value", "reason", "level"])
                writer.writerow([day.isoformat(), vt_symbol, desired, target, pos,
                                 f"{price:.4f}", lot, f"{price * lot:.0f}", reason, level])
        except OSError as exc:
            self._log_throttled("blocked_csv", f"[诊断] 写 signal_blocked.csv 失败: {exc}", 3600)
        self._log_throttled(
            f"no_open_{vt_symbol}_{reason}",
            f"[信号] {vt_symbol} 目标 {desired} 股、每手 {lot} 股（一手约 {price * lot:,.0f}），"
            f"未下单：{reason}（级别 {level}）",
            3600,
        )

    def _record_plan_blocked(self, plan: dict, reason: str) -> None:
        """阶段二门禁的薄封装：计划里可能缺诊断字段，缺少标的就跳过。"""
        vt_symbol = plan.get("vt_symbol")
        if not vt_symbol:
            return
        self._record_signal_blocked(
            str(vt_symbol),
            plan.get("day") or self._today(),
            float(plan.get("price", 0.0) or 0.0),
            int(plan.get("desired", 0) or 0),
            int(plan.get("target", 0) or 0),
            int(plan.get("pos", 0) or 0),
            reason,
        )

    def _alert(self, level: str, key: str, msg: str) -> None:
        """写日志并推送外部告警（level: INFO / WARN / CRIT；去重由告警模块负责）。"""
        self.write_log(msg)
        hook = getattr(self, "alert_hook", None)
        if not callable(hook):
            return
        try:
            hook(level, f"{getattr(self, 'strategy_name', '')}:{key}", msg)
        except Exception as exc:
            self.write_log(f"[告警] 推送失败: {exc}")

    @staticmethod
    def _hk_now() -> datetime:
        return datetime.now(_HK_TZ)

    def _today(self) -> date:
        """港股交易所本地日期（测试可覆盖）。"""
        return self._hk_now().date()

    def _check_capital_consistency(self) -> None:
        """auto_capital=false 时 capital 必须与账户净值同量级。

        2026-11-20：回测配置（capital=150 万）被计划任务带进约 26 万的账户，
        首建仓即形成 1.66 倍敞口。偏差超限时禁止新开仓（平仓不受影响）。
        """
        tolerance: float = float(getattr(self, "capital_mismatch_tolerance", 0.0) or 0.0)
        if (
            not getattr(self, "live_mode", False)
            or getattr(self, "auto_capital", False)
            or tolerance <= 0
            or self.account_balance <= 0
        ):
            self._capital_mismatch = False
            return

        deviation: float = float(self.capital) / float(self.account_balance) - 1.0
        mismatch: bool = abs(deviation) > tolerance
        previous: bool = bool(getattr(self, "_capital_mismatch", False))
        if mismatch and not previous:
            self._alert(
                "CRIT",
                "capital_mismatch",
                f"[资金] capital={float(self.capital):,.0f} 与账户净值 "
                f"{self.account_balance:,.0f} 偏差 {deviation:+.0%}（容忍 {tolerance:.0%}），"
                "已禁止新开仓；请开启 auto_capital 或修正 capital",
            )
        elif previous and not mismatch:
            self.write_log("[资金] capital 与账户净值已恢复一致，解除开仓限制")
        self._capital_mismatch = mismatch

    def on_account(self, account: AccountData) -> None:
        """账户资金更新（IB 推送，每秒一次）

        重要：vnpy_ib 的字段映射为
            NetLiquidation / NetLiquidationByCurrency -> balance
        因此 account.balance 是「账户净值」，不是现金。
        净值直接使用该字段，不能再加持仓市值。
        """
        if not self._account_matches(account):
            return

        self.account_balance = account.balance
        self.account_available = account.available

        self._sync_capital_from_account()
        self._check_capital_consistency()

    def _account_matches(self, account: AccountData) -> bool:
        """过滤多币种/多子账户推送，避免最后一次推送覆盖主账户净值。"""
        gateway_name: str = getattr(account, "gateway_name", "") or ""
        accountid: str = getattr(account, "accountid", "") or ""

        matched: bool = True
        if gateway_name and gateway_name != "IB":
            matched = False
        elif self.account_id and self.account_id not in accountid:
            matched = False
        elif self.base_currency and not accountid.upper().endswith(
            f".{self.base_currency.upper()}"
        ):
            matched = False

        if not matched and not self._account_reject_logged:
            self._account_reject_logged = True
            self.write_log(
                f"[account] 忽略非目标账户推送: gateway={gateway_name}, "
                f"accountid={accountid}, 期望={self.account_id}/{self.base_currency}"
            )
        return matched

    def _sync_capital_from_account(self) -> None:
        """按 IB 账户净值自动同步风险预算 capital

        仅影响之后的新开仓/加仓：
          · TurtleSignal 持仓期间 unit 已冻结，不会被立即改写
          · 空仓时下一根 bar 用新的 capital 重算 unit
          · 已有持仓不会因为账户净值变化而被强制加减仓

        为避免每秒账户推送导致 unit 抖动，只有变化超过 1%
        （且至少 1,000）才更新。
        """
        if not self.auto_capital:
            return
        if self.account_balance <= 0:
            return
        if not hasattr(self, "signals"):
            return

        new_capital: int = int(self.account_balance)
        if new_capital <= 0:
            return

        old_capital: int = int(self.capital)
        threshold: int = max(1_000, int(old_capital * 0.01))
        if old_capital > 0 and abs(new_capital - old_capital) < threshold:
            return

        self.capital = new_capital
        for signal in self.signals.values():
            signal.capital = new_capital

        self.write_log(
            f"[capital] auto_capital 已跟随 IB 净值: "
            f"{old_capital:,} -> {new_capital:,}"
        )

    def _broker_net_positions(self) -> Dict[str, int]:
        """读取券商净持仓（只保留本策略标的）。

        优先使用 EVENT_POSITION 推送维护的缓存；缓存未就绪时退回
        MainEngine.get_all_positions()，避免启动瞬间把「空仓」和
        「持仓尚未推送」混为一谈。
        """
        if self.live_mode and self._broker_feed_ready:
            return {
                s: int(self._broker_position_cache.get(s, 0))
                for s in self.vt_symbols
            }

        result: Dict[str, int] = {}
        try:
            positions = self.strategy_engine.main_engine.get_all_positions()
        except Exception as exc:
            self.write_log(f"[对账] 读取券商持仓失败: {exc}")
            return result

        for position in positions:
            vt_symbol = position.vt_symbol
            if vt_symbol not in self.vt_symbols:
                continue

            volume = int(position.volume)
            if position.direction == Direction.SHORT:
                volume = -abs(volume)
            elif position.direction == Direction.NET:
                # IB 的净持仓 volume 已带符号：正=多、负=空。
                volume = int(position.volume)

            result[vt_symbol] = result.get(vt_symbol, 0) + volume

        return {s: int(result.get(s, 0)) for s in self.vt_symbols}

    def on_position(self, position: PositionData) -> None:
        """券商持仓事件：维护周期对账用的权威净持仓缓存。"""
        if not self.live_mode:
            return
        if getattr(position, "gateway_name", "") not in ("", "IB"):
            return
        vt_symbol: str = position.vt_symbol
        if vt_symbol not in self.vt_symbols:
            return

        volume: int = int(position.volume)
        if position.direction == Direction.SHORT:
            volume = -abs(volume)
        elif position.direction == Direction.NET:
            volume = int(position.volume)

        self._broker_position_cache[vt_symbol] = volume
        self._state_dict("_broker_avg_cost_cache")[vt_symbol] = float(
            getattr(position, "price", 0.0) or 0.0
        )
        if not self._broker_feed_ready:
            self._broker_feed_ready = True
            self.write_log("[对账] 已收到券商持仓事件，周期对账缓存启用")

    def on_broker_disconnect(self) -> None:
        """IB 断线：清空持仓缓存，阻断新增风险，仅允许减仓/平仓。

        断线前的 close_only / 拦截状态会被记住，重连且核对一致后自动还原
        （见 _try_clear_disconnect_block），不再需要人工重启。
        """
        if not self.live_mode:
            return
        if not getattr(self, "_disconnect_block", False):
            self._pre_disconnect = {
                "close_only": bool(self.close_only),
                "trading_blocked": bool(self._trading_blocked),
            }
            self._disconnect_block = True
            self._alert(
                "WARN",
                "ib_disconnect",
                "[对账] IB 断线，已清空持仓缓存并进入只平仓模式；"
                "重连、活动委托恢复且持仓核对一致后自动恢复",
            )
        self._broker_feed_ready = False
        self._broker_position_cache.clear()
        self._positions_verified = False
        self._trading_blocked = True
        self.close_only = True
        self.safety_mode = "reconciliation_blocked"
        # 断线期间券商侧委托可能已变化：重连后必须重新恢复活动委托再发单
        self._recovery_pending = True
        self._recovery_logged = False

    def _try_clear_disconnect_block(self) -> None:
        """重连 + 活动委托恢复完成 + 持仓核对一致后，自动解除断线保护。"""
        if not (getattr(self, "live_mode", False) and getattr(self, "_disconnect_block", False)):
            return
        if self._recovery_pending:
            return
        done_at: datetime | None = getattr(self, "_recovery_completed_at", None)
        if done_at is None:
            return
        # 空仓账户不会收到持仓推送：持仓事件未到时，恢复完成 90 秒后改用主引擎缓存核对
        if not self._broker_feed_ready and (datetime.now() - done_at).total_seconds() < 90:
            return

        broker: Dict[str, int] = self._broker_net_positions()
        mismatches: List[tuple[str, int, int]] = []
        for vt_symbol in self.vt_symbols:
            local_pos: int = int(self.get_pos(vt_symbol))
            broker_pos: int = int(broker.get(vt_symbol, 0))
            if local_pos != broker_pos:
                mismatches.append((vt_symbol, local_pos, broker_pos))

        self._disconnect_block = False
        if mismatches:
            detail: str = "；".join(f"{s}: 策略={lp}, 券商={bp}" for s, lp, bp in mismatches[:5])
            self._alert(
                "CRIT",
                "reconnect_mismatch",
                f"[对账] IB 重连后持仓不一致（{detail}），保持只平仓模式，请人工核对后重启",
            )
            return

        previous: dict = getattr(self, "_pre_disconnect", {}) or {}
        self.close_only = bool(previous.get("close_only", False))
        self._trading_blocked = bool(previous.get("trading_blocked", False))
        self._positions_verified = True
        self._positions_mismatch_logged = False
        self._alert(
            "INFO",
            "ib_reconnect",
            "[对账] IB 已重连，活动委托恢复完成且持仓一致，已自动解除断线只平仓模式",
        )

    def _periodic_position_reconcile(self, dt: datetime) -> None:
        """按配置间隔核对券商净持仓与策略 pos_data。

        发现不一致时不自动覆盖本地状态，也不自动补仓/砍仓；
        直接阻断新增风险并切换 close_only，等待人工确认。
        """
        if (
            not self.live_mode
            or not self.trading
            or self._recovery_pending
            or self.position_check_interval_minutes <= 0
            or getattr(self, "_disconnect_block", False)
        ):
            return

        now: datetime = (
            datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        )
        if self._last_position_check is not None:
            elapsed: float = (now - self._last_position_check).total_seconds()
            if elapsed < self.position_check_interval_minutes * 60:
                return
        self._last_position_check = now

        # 启动对账未确认、且尚未收到任何持仓推送时，不做「空仓」判定。
        if not self._broker_feed_ready and not self._positions_verified:
            if not self._positions_mismatch_logged:
                self._positions_mismatch_logged = True
                self.write_log("[对账] 尚未收到券商持仓推送，周期对账暂缓")
            return

        broker: Dict[str, int] = self._broker_net_positions()
        mismatches: List[tuple[str, int, int]] = []
        for vt_symbol in self.vt_symbols:
            # 有在途委托的标的：成交回报与持仓推送存在时间差，留到下一轮再比
            if self._active_intent_for_symbol(vt_symbol) is not None:
                continue
            local_pos: int = int(self.get_pos(vt_symbol))
            broker_pos: int = int(broker.get(vt_symbol, 0))
            if local_pos != broker_pos:
                mismatches.append((vt_symbol, local_pos, broker_pos))

        if not mismatches:
            return

        self._positions_verified = False
        self._trading_blocked = True
        self.close_only = True
        self.safety_mode = "reconciliation_blocked"

        if not self._positions_mismatch_logged:
            self._positions_mismatch_logged = True
            self._alert(
                "CRIT",
                "position_mismatch",
                "[对账] 券商持仓与策略本地持仓不一致，已禁止新增风险（止损/离场照常）",
            )
            for vt_symbol, local_pos, broker_pos in mismatches[:10]:
                self.write_log(
                    f"[对账]   {vt_symbol}: 策略={local_pos}, 券商={broker_pos}"
                )
            self.write_log("[对账] 已切换 close_only；请人工核对后重启，不自动补仓")
            self._save_state_throttled(force=True)

    def _ib_api_ready_for_start_sync(self) -> bool:
        """启动同步前确认 IB 会话可用；测试环境无 gateway 时跳过该检查。"""
        engine = getattr(self, "strategy_engine", None)
        main_engine = getattr(engine, "main_engine", None)
        get_gateway = getattr(main_engine, "get_gateway", None)
        if get_gateway is None:
            return True
        try:
            gateway = get_gateway("IB")
        except Exception:
            return False
        if gateway is None:
            return False
        api = getattr(gateway, "api", None)
        if api is None:
            return False
        return bool(getattr(api, "status", False))

    def _has_pending_start_orders(self) -> bool:
        if getattr(self, "active_orderids", None):
            return True
        if getattr(self, "quarantine_orderids", None):
            return True
        for intent in (getattr(self, "order_ledger", None) or {}).values():
            if (
                intent.get("status") not in _TERMINAL_INTENT_STATUSES
                and int(intent.get("remaining", 0) or 0) > 0
            ):
                return True
        return False

    def _can_auto_sync_ghost_positions(
        self, mismatches: List[tuple[str, int, int]]
    ) -> bool:
        """只允许自动清理「券商=0、策略≠0」的幽灵持仓。

        有活动委托/隔离委托/未确认意图、断线恢复中、IB 未连接时一律拒绝；
        盘中不一致不走这里，仍由周期对账锁定。
        """
        if not getattr(self, "auto_sync_positions_on_start", False):
            return False
        if not mismatches:
            return False
        if getattr(self, "_disconnect_block", False):
            return False
        if getattr(self, "_recovery_pending", False):
            return False
        if self._has_pending_start_orders():
            return False
        if not self._ib_api_ready_for_start_sync():
            return False
        return all(
            broker_volume == 0 and strategy_volume != 0
            for _, strategy_volume, broker_volume in mismatches
        )

    def _apply_broker_positions_on_start(
        self,
        broker: Dict[str, int],
        broker_count: int,
        *,
        auto: bool,
    ) -> None:
        auto_details = ", ".join(
            f"{symbol}: {int(self.get_pos(symbol))}→0"
            for symbol in self.vt_symbols
            if int(self.get_pos(symbol)) != 0 and broker.get(symbol, 0) == 0
        )
        self.cost_basis.clear()
        for vt_symbol in self.vt_symbols:
            volume = broker.get(vt_symbol, 0)
            self.pos_data[vt_symbol] = volume
            self.targets[vt_symbol] = self.targets.get(vt_symbol, 0)

        self._positions_verified = True
        self._trading_blocked = False
        self._positions_mismatch_logged = False
        if auto:
            message = (
                f"[对账] 已自动清理幽灵持仓（{auto_details or '无'}）；"
                f"券商非零标的 {broker_count} 个"
            )
            self.write_log(message)
            self._alert("WARN", "auto_sync_positions", message)
        else:
            self.write_log(
                f"[对账] 已用券商持仓覆盖策略持仓（{broker_count} 个非零标的）"
            )

    def _verify_positions_on_start(self) -> None:
        """启动时比对策略持仓与券商持仓。

        顺序：先算差异 → 显式 sync → 条件自动同步幽灵持仓 → 一致放行/不一致锁定。
        """
        if not self.check_positions_on_start:
            self._positions_verified = True
            self.write_log("[对账] 已跳过启动持仓校验（check_positions_on_start=False）")
            return

        broker: Dict[str, int] = self._broker_net_positions()
        broker_count: int = sum(1 for v in broker.values() if v)

        mismatches: List[tuple[str, int, int]] = []
        for vt_symbol in self.vt_symbols:
            strategy_volume = int(self.get_pos(vt_symbol))
            broker_volume = broker.get(vt_symbol, 0)
            if strategy_volume != broker_volume:
                mismatches.append((vt_symbol, strategy_volume, broker_volume))

        if self.sync_positions_from_broker:
            self._apply_broker_positions_on_start(broker, broker_count, auto=False)
            return

        if not mismatches:
            self._positions_verified = True
            self._trading_blocked = False
            self.write_log(f"[对账] 策略持仓与券商一致（{broker_count} 个非零标的）")
            return

        if self._can_auto_sync_ghost_positions(mismatches):
            self._apply_broker_positions_on_start(broker, broker_count, auto=True)
            return

        self._positions_verified = False
        self._trading_blocked = True
        self._alert(
            "CRIT",
            "start_mismatch",
            f"[对账] 启动持仓不一致（{len(mismatches)} 个标的），已禁止开仓/加仓，"
            "止损/离场按券商持仓照常执行",
        )
        for vt_symbol, strategy_volume, broker_volume in mismatches[:10]:
            self.write_log(
                f"[对账]   {vt_symbol}: 策略={strategy_volume}，券商={broker_volume}"
            )
        self.write_log(
            "[对账] 处理方式：核对券商持仓后，开启 "
            "sync_positions_from_broker 重启一次，或启用启动期 "
            "auto_sync_positions_on_start 自动清理幽灵持仓"
        )

    def _maybe_reconcile_target_state(self) -> None:
        """等所有持仓标的的 N 就绪后，再做一次目标/持仓反推。

        on_start 阶段日线尚未回放，machine.n = 0，calculate_unit() 返回 0；
        若此时就反推会直接 _trading_blocked = True，导致整个交易日（含止损
        平仓）都发不出委托。这里改为「N 有效后只做一次」。
        """
        if not self.live_mode or self._target_reconciled:
            return

        positions: Dict[str, int] = self._reconcile_positions()
        for vt_symbol, signal in self.signals.items():
            if positions.get(vt_symbol, 0) and signal.machine.n <= 0:
                return

        self._target_reconciled = True
        self._reconcile_target_state()

    def _reconcile_positions(self) -> Dict[str, int]:
        """反推级别用的持仓：实盘以券商为准（柜台是唯一真相源），回测用本地。

        券商数据未确认（既没有持仓推送、启动对账也没通过）时不能用：
        主引擎缓存此时可能还是空的，空仓会被误判，进而清零真实持仓的级别。
        """
        if getattr(self, "live_mode", False) and (
            getattr(self, "_broker_feed_ready", False)
            or getattr(self, "_positions_verified", False)
        ):
            broker: Dict[str, int] = self._authoritative_positions()
            if broker:
                return {s: int(broker.get(s, 0)) for s in self.signals}
        return {s: int(self.get_pos(s)) for s in self.signals}

    def _block_pending_reconcile_liquidation(
        self, vt_symbol: str, pos: int, target: int
    ) -> bool:
        """目标反推未完成时，禁止把 target=0 当成清仓指令。

        重启后状态文件可能丢失 target（恢复为 0），而券商仍有持仓。
        在 N 就绪、反推出真实级别之前，若按 target=0 下单会误平全部持仓。
        返回 True 表示本标的本轮应跳过。
        """
        if not (
            self.live_mode
            and not self._target_reconciled
            and pos != 0
            and target == 0
        ):
            return False

        if not self._reconcile_pending_logged:
            self._reconcile_pending_logged = True
            self.write_log(
                f"[对账] {vt_symbol} 目标反推未完成（等待 N 就绪），"
                "暂不按 target=0 清仓"
            )
        return True

    def _reconcile_target_state(self) -> None:
        """启动时修正「状态目标」与「券商持仓」的矛盾

        典型事故：
          · 状态文件恢复 target=4，但券商实际为空仓
            -> 下一根 bar 会一次性买入 4 个 unit（不是逐级加仓）
          · 券商是空头，状态却是 +4
            -> 下一根 bar 会巨额买入，既平空又反手做多

        处理：
          · 空仓但状态有目标 -> 目标清零，等待新的突破信号
          · 整 unit 且在上限内 -> 按持仓反推级别
          · 超上限 / 非整 unit / 方向与状态相反 / 禁空却持空头
              -> 持有保护：级别夹到 [1, unit_limit] 供止损计算，禁止加仓、
                 不自动减仓；状态机止损/离场（target=0）时照常平仓。
                 旧逻辑是 _trading_blocked 全冻结，连止损都发不出（E-3）。
        """
        positions: Dict[str, int] = self._reconcile_positions()
        hold: Dict[str, str] = self._state_dict("_hold_symbols")

        for vt_symbol, signal in self.signals.items():
            pos: int = int(positions.get(vt_symbol, 0))
            level: int = int(signal.machine.target)

            if pos == 0 and level != 0:
                signal.machine.target = 0
                # 仅清零还不够：如果当前价已经高于第 4 级触发价，
                # 下一根 bar 仍会从 0 直接跳到 4。暂停当日开仓，
                # 等下一个日 K 重新计算 entry_up / N 后再允许新信号。
                signal.machine.traded = True
                self.write_log(
                    f"[对账] {vt_symbol} 券商空仓但状态目标={level}，"
                    "已清零并暂停当日开仓，等待下一交易日重建通道"
                )
                continue

            # 注意：这里不能因为 level == 0 就跳过。
            # 状态文件丢失时会出现「券商有持仓、状态 target=0」，若直接
            # continue，下一根 bar 会把 target=0 当作「应该空仓」而卖出真实
            # 持仓。必须走到下面的「按持仓反推级别」。
            if pos == 0:
                continue

            # 按实际持仓 / 单 unit 反推持有级别，避免状态 target 与实际不符时
            # 下一根 bar 一次性补买/补卖多个 unit。
            unit: int = signal.calculate_unit()
            if unit <= 0:
                # N 未算出（日线未回放完）：本轮跳过，等下一根 bar 重试。
                # 这里绝不能 _trading_blocked = True —— 那会连带阻止止损平仓。
                self._target_reconciled = False
                self.write_log(
                    f"[对账] {vt_symbol} 暂无法计算 unit（N={signal.machine.n}），"
                    "本轮跳过目标反推，等待 N 就绪"
                )
                continue

            raw_level: float = abs(pos) / unit
            actual_level: int = int(round(raw_level))
            limit: int = int(signal.machine.unit_limit)

            # 以下情形不能直接采信：进入持有保护（不再全冻结，止损照常）
            reason: str = ""
            if pos < 0 and not self.allow_short:
                reason = f"为空头 {pos}，但 allow_short=False"
            elif level != 0 and (pos > 0) != (level > 0):
                reason = f"持仓方向={pos} 与状态目标={level} 相反"
            elif raw_level > limit + 0.25:
                # 超上限不能把 target 设为 actual（会绕过 unit_limit）
                reason = f"实际约 {raw_level:.2f} 个 unit，超过上限 {limit}"
            elif actual_level <= 0 or abs(raw_level - actual_level) > 0.25:
                # 非整 unit（含不足 1 个 unit）：不自动平掉零碎持仓
                reason = f"持仓 {pos} 不是整 unit（约 {raw_level:.2f} 个）"

            if reason:
                hold_level: int = max(1, min(actual_level, limit))
                hold_target: int = hold_level if pos > 0 else -hold_level
                if signal.machine.target != hold_target:
                    self._set_machine_level(signal.machine, pos, hold_level)
                    # 反推公式把开仓价设在「当前入场通道 + 级数×0.5N」，对异常持仓
                    # （如假突破买入）会远高于真实成本，立即触发止损清仓。持有保护
                    # 改用券商平均成本作为开仓价；拿不到时让止损退回离场通道。
                    self._anchor_hold_entry(
                        signal.machine, pos, self._broker_avg_cost(vt_symbol)
                    )
                signal.machine.rebase_next_add = False
                hold[vt_symbol] = reason
                self._alert(
                    "WARN",
                    f"hold_{vt_symbol}",
                    f"[对账] {vt_symbol} {reason}：进入持有保护（禁止加仓、不自动减仓，"
                    f"按 {hold_level} 级计算止损，止损/离场照常），请人工核对",
                )
                continue

            if actual_level != abs(level):
                actual_level = min(actual_level, limit)
                new_target: int = actual_level if pos > 0 else -actual_level
                self._set_machine_level(signal.machine, pos, actual_level)

                # 下一级触发价重设到当前价 ± 0.5N，禁止按历史触发价补作业
                signal.machine.rebase_next_add = True
                if level == 0:
                    self.write_log(
                        f"[对账] {vt_symbol} 状态目标=0 但券商持有 {pos}"
                        "（状态文件可能丢失），已按持仓反推级别，避免误平仓"
                    )
                self.write_log(
                    f"[对账] {vt_symbol} 按持仓反推级别={actual_level}"
                    f"（unit={unit}，持仓={pos}），状态目标 {level}→{new_target}，"
                    "下一级须从当前价再推进 0.5N"
                )

    @staticmethod
    def _anchor_hold_entry(machine: Any, pos: int, avg_cost: float) -> None:
        """持有保护的开仓价：券商平均成本；没有时取「离场通道 ± 止损距离」，止损即离场通道。"""
        if pos > 0:
            machine.long_entry = avg_cost if avg_cost > 0 else machine.exit_down + 2 * machine.n
        else:
            mult: float = float(getattr(machine, "short_stop_mult", 2.0) or 2.0)
            machine.short_entry = avg_cost if avg_cost > 0 else machine.exit_up - mult * machine.n

    def _broker_avg_cost(self, vt_symbol: str) -> float:
        """券商持仓平均成本（IB averageCost，股票即每股成本）；取不到返回 0。"""
        cached: float = float(self._state_dict("_broker_avg_cost_cache").get(vt_symbol, 0.0) or 0.0)
        if cached > 0:
            return cached
        engine = getattr(self, "strategy_engine", None)
        main_engine = getattr(engine, "main_engine", None)
        if main_engine is None:
            return 0.0
        try:
            for position in main_engine.get_all_positions():
                if position.vt_symbol == vt_symbol and float(position.volume or 0):
                    return float(position.price or 0.0)
        except Exception:
            return 0.0
        return 0.0

    @staticmethod
    def _set_machine_level(machine: Any, pos: int, level: int) -> None:
        """把状态机设为 pos 方向的 level 级，并同步最近开仓价（止损按该级计算）。"""
        machine.target = level if pos > 0 else -level
        level_index: int = max(1, level // machine.trading_size)
        if pos > 0:
            machine.long_entry = machine.entry_up + machine.n * (level_index - 1) * 0.5
        else:
            machine.short_entry = machine.entry_down - machine.n * (level_index - 1) * 0.5

    def _compute_equity(self) -> float:
        """组合净值：直接使用 account.balance（IB NetLiquidation）

        之前误以为 account.balance 是现金，又加上持仓市值，导致净值重复计算。
        vnpy_ib 的映射明确是 NetLiquidation -> balance，因此这里是净值本身。

        回测中没有账户推送，自动回退到自建递推的 equity。
        """
        if self.account_balance > 0:
            return self.account_balance
        return self.equity

    def _write_heartbeat(self, dt) -> None:
        """写心跳文件（控制面板据此判断策略是否存活）

        每分钟最多写一次，避免 IO 过多；内容含持仓与广度，面板可直接展示。
        """
        key: str = dt.strftime("%Y%m%d%H%M")
        if key == self._last_heartbeat_key:
            return
        self._last_heartbeat_key = key

        base = Path(self.heartbeat_dir) if self.heartbeat_dir else Path.cwd() / "state"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._heartbeat_write_errors += 1
            self.write_log(
                f"[heartbeat] 创建心跳目录失败"
                f"（累计 {self._heartbeat_write_errors} 次）: {exc}"
            )
            return

        # 持仓市值与敞口（用上一根 bar 收盘价估算，与权益递推口径一致）
        holdings: Dict[str, int] = {
            s: self.get_pos(s) for s in self.vt_symbols if self.get_pos(s)
        }
        gross_market_value: float = 0.0     # Σ|pos|×price，敞口用
        net_market_value: float = 0.0       # Σpos×price，估算现金用
        for s, pos in holdings.items():
            price: float = self.prev_prices.get(s, 0.0)
            gross_market_value += abs(pos) * price
            net_market_value += pos * price

        # 敞口口径：持仓市值 / 组合净值（真实杠杆倍数）
        # 注意不要用 capital（风险预算）当分母，那会把敞口放大 6 倍以上
        equity: float = self.equity
        gross: float = (gross_market_value / equity) if equity > 0 else 0.0

        # 今日盈亏（相对上一交易日收盘净值）
        today_pnl: float = 0.0
        if self._daily_nav is not None:
            today_pnl = self.equity - self._daily_nav

        # 成交质量
        slips: List[float] = [r["slippage_bps"] for r in self.fill_records]
        avg_slip: float = (sum(slips) / len(slips)) if slips else 0.0

        # 阶段 3：wall_time 取写入瞬间墙钟时间，与 bar 时间 time 区分，
        # 供面板按新鲜度判活；字段契约见 scripts/runtime_state.py。
        wall_time: str = datetime.now().astimezone().isoformat(timespec="seconds")

        payload: dict = {
            "time": dt.isoformat(),
            "strategy": self.strategy_name,
            "breadth": round(self.market_breadth, 4),
            "max_positions": self.max_positions,
            "trend_filter_enabled": bool(self.use_trend_filter),
            "positions_verified": bool(self._positions_verified),
            "trading_blocked": bool(self._trading_blocked),
            "orders_blocked": bool(self.block_new_orders),
            "safety_mode": self.safety_mode,
            "close_only": bool(self.close_only),
            "funds_blocked": bool(self.funds_blocked),
            "active_intents": sum(
                1
                for i in self.order_ledger.values()
                if i.get("status") not in _TERMINAL_INTENT_STATUSES
                and int(i.get("remaining", 0)) > 0
            ),
            "quarantine_orders": len(self.quarantine_orderids),
            "positions_feed_ready": bool(self._broker_feed_ready),
            "invalid_ticks": int(self._invalid_tick_count),
            "last_quote_reject_reason": self._last_quote_reject_reason,
            "positions": holdings,
            "fill_count": len(self.fill_records),
            # 账户
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown": round(self.current_drawdown, 4),
            "market_value": round(gross_market_value, 2),
            "net_market_value": round(net_market_value, 2),
            # 现金 ≈ 净值 − 净市值（净值来自 IB NetLiquidation）
            "cash": round(self.equity - net_market_value, 2),
            "available_cash": round(self.account_available, 2),
            "gross_exposure": round(gross, 4),
            "available": round(max(0.0, 1.0 - gross), 4),
            "today_pnl": round(today_pnl, 2),
            "daily_nav": round(self._daily_nav, 2) if self._daily_nav else None,
            # 策略累计盈亏（相对首次启动时的基准净值，不含账户历史盈亏）
            "baseline_equity": (
                round(self._baseline_equity, 2) if self._baseline_equity > 0 else None
            ),
            "strategy_pnl": (
                round(self.equity - self._baseline_equity, 2)
                if self._baseline_equity > 0
                else None
            ),
            "strategy_pnl_pct": (
                round(self.equity / self._baseline_equity - 1.0, 4)
                if self._baseline_equity > 0
                else None
            ),
            # 执行质量
            "avg_slippage_bps": round(avg_slip, 2),
            "active_orders": len(self.active_orderids),
            # 存活与状态
            "last_tick_time": self._last_tick_time,
            "last_bar_time": self._last_bar_time,
            "tick_count": self._tick_count,
            "inited": bool(self.inited),
            "trading": bool(self.trading),
            # 阶段 3：统一状态文件 schema（与 runtime_state.decorate 契约一致）
            "schema_version": 2,
            "writer": "strategy",
            "pid": os.getpid(),
            "wall_time": wall_time,
            "event_time": dt.isoformat(),
            "write_errors": int(self._heartbeat_write_errors),
            # 数据新鲜度（供面板与健康快照判断是否因缺口而禁止开仓）
            "last_daily_date": (
                self._last_daily_date.isoformat() if self._last_daily_date else None
            ),
            "data_stale": bool(self._check_data_stale()),
            # 保护状态：持有保护 / 当日冻结 / 行情静默 / 断线 / 资金口径 / 状态写盘
            "hold_symbols": dict(self._state_dict("_hold_symbols")),
            "frozen_symbols": dict(self._day_counters().get("frozen", {})),
            "silent_symbols": sorted(self._state_set("_silent_symbols")),
            "disconnect_block": bool(getattr(self, "_disconnect_block", False)),
            "capital_mismatch": bool(getattr(self, "_capital_mismatch", False)),
            "state_write_failed": bool(getattr(self, "_state_write_failed", False)),
        }

        path = base / f"heartbeat_{self.strategy_name}.json"
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            self._heartbeat_write_errors += 1
            if (
                self._heartbeat_write_errors == 1
                or self._heartbeat_write_errors % 60 == 0
            ):
                self.write_log(
                    f"[heartbeat] 写心跳失败"
                    f"（累计 {self._heartbeat_write_errors} 次）: {exc}"
                )

    def diagnose(self) -> str:
        """输出「为什么现在不开仓」的诊断信息（面板/日志可调用）"""
        lines: List[str] = []
        lines.append(f"市场广度      : {self.market_breadth:.1%}")
        lines.append(f"持仓名额上限  : {self.max_positions}")
        lines.append(f"允许做多      : {sorted(self.allowed_long) or '（无）'}")

        holdings = {s: self.get_pos(s) for s in self.vt_symbols if self.get_pos(s)}
        lines.append(f"当前持仓      : {holdings or '（空仓）'}")

        if self.use_cost_exposure:
            gross = sum(abs(v) for v in self.cost_basis.values())
            cap = self.capital * self.max_gross_exposure
            lines.append(f"敞口（按成本）: {gross:,.0f} / {cap:,.0f}")
            if gross >= cap:
                lines.append("  -> 敞口已满，这是不开新仓的直接原因")

        lines.append("")
        lines.append("各标的状态（ER / 是否允许 / 持仓）：")
        for vt_symbol in self.vt_symbols:
            er = self.er_values.get(vt_symbol, 0.0)
            allowed = vt_symbol in self.allowed_long
            pos = self.get_pos(vt_symbol)
            lines.append(
                f"  {vt_symbol:22s} ER={er:.2f} 允许={'是' if allowed else '否'} 持仓={pos}"
            )

        return "\n".join(lines)

    def _auto_export_fills(self) -> None:
        """每日收盘自动导出执行监控（P0-2），无需人工干预"""
        if not self.fill_records:
            return

        from pathlib import Path

        try:
            base = Path(self.fills_dir) if self.fills_dir else Path.cwd() / "output"
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"fills_live_{self.strategy_name}.csv"
            self.export_fill_report(str(path))

            # 同时导出持仓快照，供日终对账（reconcile.py）使用
            pos_path = base / f"positions_live_{self.strategy_name}.csv"
            self.export_positions(str(pos_path))
        except Exception as exc:                # 导出失败不阻断交易
            self.write_log(f"[fills] 自动导出失败: {exc}")

    def export_positions(self, csv_path: str) -> str:
        """导出当前持仓快照（日终对账用）

        只记录策略「认为」的持仓；与券商实际持仓的差异由 reconcile.py 比对。
        """
        import csv

        rows: List[dict] = []
        for vt_symbol in self.signals.keys():
            pos: int = self.get_pos(vt_symbol)
            if not pos:
                continue

            machine = self.signals[vt_symbol].machine
            cost: float = self.cost_basis.get(vt_symbol, 0.0)
            rows.append(
                {
                    "vt_symbol": vt_symbol,
                    "pos": pos,
                    "target_level": int(machine.target),
                    "cost_basis": round(cost, 2),
                    # 本地成本由成交累加，重启/状态清零后会丢失，只供分析
                    "avg_cost": round(abs(cost / pos), 4) if pos else 0.0,
                    # 成本以 IB 平均成本为准（S4-08）
                    "broker_avg_cost": round(self._broker_avg_cost(vt_symbol), 4),
                    "date": self._today().isoformat(),
                }
            )

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "vt_symbol",
                    "pos",
                    "target_level",
                    "cost_basis",
                    "avg_cost",
                    "broker_avg_cost",
                    "date",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        return csv_path

    def on_stop(self):
        """停止"""
        self.write_log("策略停止")

        # P0-3：退出前落盘，保证下次启动可恢复
        if self.use_state_persist:
            self._save_state()

    def _state_file(self):
        """状态文件路径（P0-3）"""
        from pathlib import Path

        base = Path(self.state_dir) if self.state_dir else Path.cwd() / "state"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{self.strategy_name}.json"

    def _migrate_state_v1(
        self,
        path: Path,
        data: Dict[str, Any],
        meta: dict,
    ) -> None:
        """把 v1 状态文件备份并升级为 schema v2。"""
        try:
            backup = path.with_name(
                f"{path.name}.v1.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
            )
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

            migrated: Dict[str, Any] = dict(data)
            migrated["_meta"] = {
                "schema_version": self.STATE_SCHEMA_VERSION,
                "strategy_name": meta.get("strategy_name", self.strategy_name),
                "trade_date": datetime.now().date().isoformat(),
                "saved_at": datetime.now().isoformat(),
                "baseline_equity": float(meta.get("baseline_equity", 0.0) or 0.0),
            }
            migrated["_runtime"] = {
                "order_ledger": {},
                "orderid_intent": {},
                "symbol_active_intent": {},
                "active_orderids": [],
                "quarantine_orderids": [],
                "intent_seq": 0,
            }

            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(migrated, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            tmp.replace(path)
            self.write_log(f"[state] 已备份并迁移旧状态文件: {backup.name}")
        except Exception as exc:
            self._trading_blocked = True
            self.write_log(f"[state] 迁移旧状态文件失败，已禁止下单: {exc}")

    def _state_snapshot_invalid_reason(self) -> str:
        """明显错误的快照（持仓存在但状态机未预热、N=0、无级别）拒绝写盘。"""
        for vt_symbol, signal in self.signals.items():
            try:
                pos: int = int(self.get_pos(vt_symbol))
            except Exception:
                continue
            machine = signal.machine
            if (
                pos
                and not machine.inited
                and float(machine.n) <= 0
                and int(machine.target) == 0
            ):
                return f"{vt_symbol} 持仓 {pos} 但状态机未预热（N=0、级别=0）"
        return ""

    def _backup_state_file(self, path: Path) -> None:
        """写盘前滚动备份上一份状态（30 分钟最多一份，保留最近 60 份，约 4 个交易日）。

        live_replay.py 按快照内的 saved_at 找「某日开盘前」的状态做当日回放对拍。
        """
        if not path.exists():
            return
        now: datetime = datetime.now()
        last: datetime | None = getattr(self, "_state_backup_at", None)
        if last is not None and (now - last).total_seconds() < 1800:
            return
        try:
            backup_dir: Path = path.parent / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_dir / f"{path.stem}.{now:%Y%m%d_%H%M%S}.json")
            self._state_backup_at = now
            backups: List[Path] = sorted(backup_dir.glob(f"{path.stem}.*.json"))
            for old in backups[:-60]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            self.write_log(f"[state] 备份状态文件失败（不影响本次写入）: {exc}")

    def _save_state(self) -> None:
        """持久化状态机、订单账本、交易任务与风控锚点（schema v2）。"""
        if getattr(self, "_ca_preparing", False) or getattr(self, "_ca_prepare_error", None) is not None:
            return
        # on_start 加载完状态之前不写盘：初始化回放期间的快照是「历史中途」状态，
        # 写下去会覆盖上次的有效状态（E-2）。测试用的裸对象没有该字段，按已就绪处理。
        if not getattr(self, "_state_ready", True):
            if not getattr(self, "_state_guard_logged", False):
                self._state_guard_logged = True
                self.write_log(
                    "[state] 状态尚未加载完成（初始化/回放中），跳过写盘，"
                    "防止覆盖上次的有效状态"
                )
            return

        invalid: str = self._state_snapshot_invalid_reason()
        if invalid:
            self._log_throttled(
                "state_invalid",
                f"[state] 拒绝写入可疑快照：{invalid}；保留上一份状态文件",
                1800,
            )
            return

        data: Dict[str, Any] = {}
        for vt_symbol, signal in self.signals.items():
            machine = signal.machine
            data[vt_symbol] = {
                "target": int(machine.target),
                "long_entry": float(machine.long_entry),
                "short_entry": float(machine.short_entry),
                "n": float(machine.n),
                "entry_up": float(machine.entry_up),
                "entry_down": float(machine.entry_down),
                "exit_up": float(machine.exit_up),
                "exit_down": float(machine.exit_down),
                "inited": bool(machine.inited),
                "traded": bool(machine.traded),
                "rebase_next_add": bool(getattr(machine, "rebase_next_add", False)),
            }
            if getattr(machine, "profit_lock_trigger_n", 0.0) > 0:
                data[vt_symbol]["hold_high"] = float(machine.hold_high)
                data[vt_symbol]["lock_stop"] = float(machine.lock_stop)
            if self.use_corporate_actions:
                anchor = self._state_dict("_ca_dates").get(vt_symbol)
                data[vt_symbol]["price_anchor"] = anchor.isoformat() if anchor else None
                data[vt_symbol]["unit"] = signal.unit

        data["_meta"] = {
            "schema_version": self.STATE_SCHEMA_VERSION,
            "strategy_name": self.strategy_name,
            "trade_date": datetime.now().date().isoformat(),
            "saved_at": datetime.now().isoformat(),
            "baseline_equity": float(self._baseline_equity),
        }
        if self.use_corporate_actions:
            data["_meta"]["corporate_actions"] = {
                "basis": "asof_raw_v1", "sha256": self._ca_book.sha256,
            }

        data["_runtime"] = {
            "order_ledger": self.order_ledger,
            "orderid_intent": self._orderid_intent,
            "symbol_active_intent": self._symbol_active_intent,
            "active_orderids": sorted(self.active_orderids),
            "quarantine_orderids": sorted(self.quarantine_orderids),
            "intent_seq": self._intent_seq,
            "equity": float(self.equity),
            "peak_equity": float(self.peak_equity),
            "current_drawdown": float(self.current_drawdown),
            "pause_until": self.pause_until.isoformat() if self.pause_until else None,
            "new_open_scale": float(self.new_open_scale),
            "account_balance": float(self.account_balance),
            "account_available": float(self.account_available),
            "daily_nav": self._daily_nav,
            "cost_basis": self.cost_basis,
            "signal_prices": self.signal_prices,
            "order_intents": self.order_intents,
            "fill_volumes": self.fill_volumes,
            "fill_records": self.fill_records,
            "prev_prices": self.prev_prices,
            "safety_mode": self.safety_mode,
            "funds_blocked": bool(self.funds_blocked),
            "invalid_tick_count": int(self._invalid_tick_count),
            "last_quote_reject_reason": self._last_quote_reject_reason,
            "broker_feed_ready": bool(self._broker_feed_ready),
            "positions_mismatch_logged": bool(self._positions_mismatch_logged),
            "order_counters": dict(self.__dict__.get("_order_counters") or {}),
            "hold_symbols": dict(self.__dict__.get("_hold_symbols") or {}),
        }

        try:
            path = self._state_file()
            self._backup_state_file(path)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            tmp.replace(path)
        except Exception as exc:
            # 偶发失败不阻断交易；连续 3 次失败视为致命：禁止新开仓并告警
            errors: int = int(getattr(self, "_state_write_errors", 0)) + 1
            self._state_write_errors = errors
            self.write_log(f"[state] 保存失败（连续 {errors} 次）: {exc}")
            if errors >= 3 and not getattr(self, "_state_write_failed", False):
                self._state_write_failed = True
                self._alert(
                    "CRIT",
                    "state_write_failed",
                    f"[state] 状态文件连续 {errors} 次写入失败，已禁止新开仓（平仓照常）："
                    f"{exc}",
                )
            return

        self._state_write_errors = 0
        if getattr(self, "_state_write_failed", False):
            self._state_write_failed = False
            self.write_log("[state] 状态文件写入已恢复，解除开仓限制")

    def _load_state(self) -> None:
        """恢复状态机、订单账本与当日交易状态（schema v2）。"""
        try:
            path = self._state_file()
        except Exception as exc:
            self.write_log(f"[state] 目录创建失败: {exc}")
            return

        if not path.exists():
            self.write_log(f"[state] 无历史状态，按全新状态运行: {path}")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except Exception as exc:
            self.write_log(f"[state] 读取失败，按全新状态运行: {exc}")
            return

        meta: dict = data.pop("_meta", {}) or {}
        runtime: dict = data.pop("_runtime", {}) or {}
        if meta.get("corporate_actions") and not self.use_corporate_actions:
            raise ValueError("快照为 as-of 口径，当前关闭复权；必须人工迁移后再启动")
        schema_version: int = int(meta.get("schema_version", 1) or 1)
        legacy: bool = schema_version < self.STATE_SCHEMA_VERSION

        if schema_version > self.STATE_SCHEMA_VERSION:
            self._trading_blocked = True
            self.write_log(
                f"[state] 状态文件版本 {schema_version} 高于当前支持版本，"
                "已禁止下单，请人工迁移"
            )
            return

        if legacy:
            self.write_log(
                "[state] 检测到旧版状态文件：备份并迁移到 schema v2；"
                "不恢复订单账本，同一交易日按“已交易”处理，防止重启重复开仓"
            )
            self._migrate_state_v1(path, data, meta)

        saved_baseline: float = float(meta.get("baseline_equity", 0.0) or 0.0)
        if saved_baseline > 0:
            self._baseline_equity = saved_baseline

        # v1 无法知道保存日期，按当日已交易处理，避免重启补单。
        same_day: bool = (
            meta.get("trade_date") == datetime.now().date().isoformat()
            or legacy
        )

        restored: int = 0
        skipped: List[str] = []
        for vt_symbol, fields in data.items():
            signal = self.signals.get(vt_symbol)
            if signal is None or not isinstance(fields, dict):
                continue

            machine = signal.machine
            saved_target: int = int(fields.get("target", 0) or 0)
            saved_n: float = float(fields.get("n", 0.0) or 0.0)

            # 只有「持有中」的标的才需要恢复冻结的入场通道 / N / 开仓价：
            # 空仓标的用初始化回放刚算出的最新通道，不能被旧快照覆盖（E-2b）。
            # 持有但 N<=0 的记录是坏快照，交给按券商持仓反推级别处理。
            if saved_target != 0 and saved_n > 0:
                machine.target = saved_target
                machine.long_entry = float(fields.get("long_entry", 0.0))
                machine.short_entry = float(fields.get("short_entry", 0.0))
                machine.n = saved_n
                machine.entry_up = float(fields.get("entry_up", 0.0))
                machine.entry_down = float(fields.get("entry_down", 0.0))
                machine.exit_up = float(fields.get("exit_up", 0.0))
                machine.exit_down = float(fields.get("exit_down", 0.0))
                if getattr(machine, "profit_lock_trigger_n", 0.0) > 0:
                    machine.hold_high = float(fields.get("hold_high", 0.0) or 0.0)
                    machine.lock_stop = float(fields.get("lock_stop", 0.0) or 0.0)
                self._restore_corporate_action_state(vt_symbol, fields, meta)
                self._state_set("_restored_held").add(vt_symbol)
            elif saved_target != 0:
                skipped.append(vt_symbol)
                continue
            # inited 以回放结果为准，绝不用快照把已预热的状态机改回未预热
            machine.traded = bool(
                same_day and (legacy or bool(fields.get("traded", False)))
            )
            machine.rebase_next_add = bool(
                same_day and bool(fields.get("rebase_next_add", False))
            )
            restored += 1

        if not legacy:
            self.order_ledger = dict(runtime.get("order_ledger", {}) or {})
            self._orderid_intent = dict(runtime.get("orderid_intent", {}) or {})
            self._symbol_active_intent = dict(
                runtime.get("symbol_active_intent", {}) or {}
            )
            self._intent_seq = int(runtime.get("intent_seq", 0) or 0)
            self.equity = float(runtime.get("equity", self.equity) or self.equity)
            self.peak_equity = float(
                runtime.get("peak_equity", self.peak_equity) or self.peak_equity
            )
            self.current_drawdown = float(
                runtime.get("current_drawdown", self.current_drawdown) or 0.0
            )
            self.new_open_scale = float(
                runtime.get("new_open_scale", self.new_open_scale) or 1.0
            )
            self.account_balance = float(runtime.get("account_balance", 0.0) or 0.0)
            self.account_available = float(runtime.get("account_available", 0.0) or 0.0)
            self._daily_nav = runtime.get("daily_nav", None)
            self.cost_basis = dict(runtime.get("cost_basis", {}) or {})
            self.signal_prices = dict(runtime.get("signal_prices", {}) or {})
            self.order_intents = dict(runtime.get("order_intents", {}) or {})
            self.fill_volumes = dict(runtime.get("fill_volumes", {}) or {})
            self.fill_records = list(runtime.get("fill_records", []) or [])
            self.prev_prices = dict(runtime.get("prev_prices", {}) or {})
            if self.use_corporate_actions:
                self.prev_prices.update(self._state_dict("_ca_raw_closes"))
            self.safety_mode = str(runtime.get("safety_mode", "normal"))
            self.funds_blocked = bool(runtime.get("funds_blocked", False))
            self._invalid_tick_count = int(
                runtime.get("invalid_tick_count", 0) or 0
            )
            self._last_quote_reject_reason = str(
                runtime.get("last_quote_reject_reason", "") or ""
            )
            # broker_feed_ready 只代表「本进程已收到持仓推送」，不能跨重启恢复：
            # 恢复成 True 而缓存为空时，启动对账会把真实持仓误读成 0。
            self._positions_mismatch_logged = bool(
                runtime.get("positions_mismatch_logged", False)
            )
            counters: dict = dict(runtime.get("order_counters", {}) or {})
            if counters.get("date") == self._today().isoformat():
                self._order_counters = counters

            pause_until: str | None = runtime.get("pause_until")
            if pause_until:
                try:
                    self.pause_until = datetime.fromisoformat(pause_until)
                except ValueError:
                    self.pause_until = None

            # 账本里的 orderids 必须重建映射，供重启后 openOrder 回调路由。
            for intent_id, intent in self.order_ledger.items():
                for vt_orderid in intent.get("orderids", []):
                    self._orderid_intent[vt_orderid] = intent_id

        if skipped:
            self._alert(
                "WARN",
                "state_bad_records",
                f"[state] {skipped} 的状态记录有级别但 N<=0（坏快照），已忽略，"
                "将按券商持仓反推级别",
            )
        self.write_log(
            f"[state] 已恢复 {restored} 个标的的状态、"
            f"{len(self.order_ledger)} 个订单意图: {path}"
        )

    def _align_unheld_machines(self) -> None:
        """实盘：状态文件里不是持有中的标的，状态机回到空仓并用最新通道。

        初始化回放会按历史数据模拟开平仓，回放结束时状态机可能「以为」自己
        还持有某只股票（入场通道与 N 冻结在历史开仓点）。真实持仓以状态文件
        + 券商持仓为准，其余一律复位；券商有而状态没有的，由 _reconcile_target_state
        按持仓反推级别。
        """
        held: set = self._state_set("_restored_held")
        for vt_symbol, signal in self.signals.items():
            if vt_symbol in held:
                continue
            machine = signal.machine
            reset = getattr(machine, "reset_flat", None)
            try:
                if callable(reset):
                    reset()
                else:
                    machine.target = 0
            except Exception as exc:
                machine.target = 0
                self.write_log(f"[state] {vt_symbol} 重算最新通道失败: {exc}")

    def _reject_tick(self, tick: TickData, reason: str) -> None:
        """记录被行情质量闸门丢弃的 tick，同原因只打一条日志避免刷屏。"""
        self._invalid_tick_count += 1
        self._last_quote_reject_reason = reason
        if reason not in self._quote_reject_logged:
            self._quote_reject_logged.add(reason)
            self.write_log(
                f"[行情] 丢弃异常 tick {tick.vt_symbol}: {reason}, "
                f"price={tick.last_price}, time={tick.datetime}"
            )

    def _in_regular_session(self, tick: TickData) -> bool:
        """按交易所本地时间判断常规交易时段（不含盘前/盘后/午休）。"""
        dt: datetime = tick.datetime
        is_hk: bool = (
            tick.exchange == Exchange.SEHK or tick.vt_symbol.endswith(".SEHK")
        )

        try:
            tz = ZoneInfo("Asia/Hong_Kong" if is_hk else "America/New_York")
            local_dt = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            local_dt = dt

        if local_dt.weekday() >= 5:
            return False

        local_time = local_dt.time().replace(tzinfo=None)
        if is_hk:
            return (
                time(9, 30) <= local_time < time(12, 0)
                or time(13, 0) <= local_time < time(16, 0)
            )
        return time(9, 30) <= local_time < time(16, 0)

    def _is_tick_valid(self, tick: TickData) -> bool:
        """实盘 tick 质量闸门；回测与未启用时不拦截。"""
        if not self.live_mode:
            return True

        price: float = float(tick.last_price or 0.0)
        if price <= 0:
            self._reject_tick(tick, "non_positive_price")
            return False

        dt: datetime = tick.datetime
        if self.quote_max_age_seconds > 0:
            now: datetime = (
                datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            )
            age: float = (now - dt).total_seconds()
            if age > self.quote_max_age_seconds:
                self._reject_tick(tick, "stale_tick")
                return False
            if age < -30:
                self._reject_tick(tick, "future_timestamp")
                return False

        bid: float = float(tick.bid_price_1 or 0.0)
        ask: float = float(tick.ask_price_1 or 0.0)
        if bid > 0 and ask > 0 and bid > ask:
            self._reject_tick(tick, "bid_ask_crossed")
            return False

        prev_tick: TickData | None = self.pbg.last_ticks.get(tick.vt_symbol)
        if prev_tick and prev_tick.last_price > 0 and self.price_max_jump_pct > 0:
            reference = prev_tick.last_price
            if self.use_corporate_actions:
                from corporate_actions import trading_date

                for event in self._ca_book.between(tick.vt_symbol, trading_date(prev_tick.datetime), trading_date(dt)):
                    reference *= event.factor(self._state_dict("_ca_raw_closes")[tick.vt_symbol])
            jump: float = abs(price - reference) / reference
            pending: Dict[str, list] = self._state_dict("_jump_pending")
            if jump > self.price_max_jump_pct:
                # 参考价只随被接受的 tick 更新：真实跳空（复牌/除权/暴跌）后若一直拒收，
                # 该标的会被永久屏蔽、止损失效。连续 3 笔彼此一致的跳变价视为新价位。
                if not self._confirm_price_jump(tick.vt_symbol, price):
                    self._reject_tick(tick, "price_jump")
                    return False
                self._alert(
                    "WARN",
                    f"jump_{tick.vt_symbol}",
                    f"[行情] {tick.vt_symbol} 价格跳变已连续确认"
                    f"（{prev_tick.last_price} → {price}），重设跳价基准",
                )
            else:
                pending.pop(tick.vt_symbol, None)

        if (
            self.enforce_trading_session
            and not self.outside_rth
            and not self._in_regular_session(tick)
        ):
            self._reject_tick(tick, "outside_regular_session")
            return False

        return True

    def _confirm_price_jump(self, vt_symbol: str, price: float) -> bool:
        """跳变价与上一笔待确认价相差 1% 以内则累计，满 3 笔返回 True。"""
        pending: Dict[str, list] = self._state_dict("_jump_pending")
        candidate: list | None = pending.get(vt_symbol)
        if candidate and candidate[0] > 0 and abs(price - candidate[0]) / candidate[0] <= 0.01:
            candidate[0] = price
            candidate[1] += 1
        else:
            candidate = [price, 1]
            pending[vt_symbol] = candidate
        if candidate[1] >= 3:
            pending.pop(vt_symbol, None)
            return True
        return False

    def on_tick(self, tick: TickData):
        """Tick推送：先过质量闸门，再合成分钟 bar。"""
        if not self._is_tick_valid(tick):
            return

        self._tick_count += 1
        self._last_tick_time = tick.datetime.isoformat()
        self._state_dict("_last_valid_tick_wall")[tick.vt_symbol] = self._hk_now()
        silent: set = self._state_set("_silent_symbols")
        if tick.vt_symbol in silent:
            silent.discard(tick.vt_symbol)
            self.write_log(f"[行情] {tick.vt_symbol} 行情已恢复")

        # 首笔有效 tick 落一条日志：区分「IB 根本没推行情」与「bar 合成异常」
        if self._tick_count == 1:
            self.write_log(
                f"[行情] 收到首笔有效 tick: {tick.vt_symbol} "
                f"price={tick.last_price} time={tick.datetime}"
            )

        # 心跳仍由 on_bars 写：心跳语义 = 「策略完整处理完一根 bar」。
        self.pbg.update_tick(tick)

    @staticmethod
    def _hk_tick(price: float) -> float:
        """港交所最小变动价位（按价格区间分段）

        配置里的 pricetick 是固定值 0.01，但港交所实际是分段的：
          0.01~0.25 -> 0.001   0.25~0.50 -> 0.005   0.50~10 -> 0.01
          10~20     -> 0.02    20~100    -> 0.05    100~200 -> 0.10
          200~500   -> 0.20    500~1000  -> 0.50    1000~2000 -> 1.00
          2000~5000 -> 2.00    5000+     -> 5.00

        对 29.6 的股票，合法价格必须是 0.05 的倍数（29.60/29.65/29.70）；
        按 0.01 对齐得的 29.63 会被 IB 以 110 拒单。
        """
        p: float = abs(price)

        if p < 0.25:
            return 0.001
        if p < 0.50:
            return 0.005
        if p < 10.0:
            return 0.01
        if p < 20.0:
            return 0.02
        if p < 100.0:
            return 0.05
        if p < 200.0:
            return 0.10
        if p < 500.0:
            return 0.20
        if p < 1000.0:
            return 0.50
        if p < 2000.0:
            return 1.00
        if p < 5000.0:
            return 2.00
        return 5.00

    def _price_add(self, ref_price: float, pricetick: float) -> float:
        """计算超价金额

        优先用 price_add_pct（按信号价的比例）：
          港股 29.6 元，0.005 → 0.148 元（约 3 个 tick）
         美股 $350，0.002  → $0.70（70 个 tick）

        未配置百分比时回退到 price_add_ticks（固定 tick 数）：
          保持历史配置与回测结果不变。
        """
        if self.price_add_pct > 0:
            return ref_price * self.price_add_pct
        return self.price_add_ticks * pricetick

    @staticmethod
    def _us_tick(price: float) -> float:
        """美股最小变动价位（SEC Rule 612 Sub-Penny Rule）

        $1.00 及以上：0.01
        $1.00 以下：  0.0001

        美股不像港股分 11 档，只有这两档。如果沿用固定 0.01，
        买 $1 以下的低价股会落在非法价位，被 IB 以 110 拒单。
        """
        if abs(price) >= 1.0:
            return 0.01
        return 0.0001

    def _round_price(self, vt_symbol: str, price: float, tick: float) -> float:
        """把价格对齐到最小变动价位的整数倍

        为什么要对齐：
          IB 的分钟线 close_price 常带多位小数（如港股的 29.528，美股的 350.234）。
          加 N 个 tick 超价后可能落在非法价位，IB 会以错误码 110
          「价格不符合最小价格变动要求」拒单。

          被拒的委托 id 会残留在 active_orderids 里变成「幽灵委托」，
          导致后续每根 bar 都在撤销一批 IB 侧根本不存在的订单
          （错误码 10147：「未找到需要取消的OrderId」）。

        规则来源：
          按交易所后缀查 _TICK_RULES 表，取该市场的真实 tick。
          表中没有的交易所（如期货、日本市场）回退到配置的 pricetick。
        """
        exchange: str = vt_symbol.rsplit(".", 1)[-1] if "." in vt_symbol else ""
        rule_name: str | None = _TICK_RULES.get(exchange)

        if rule_name:
            rule = getattr(self, rule_name, None)
            if callable(rule):
                tick = rule(price)

        if tick <= 0:
            return price
        return round(round(price / tick) * tick, 10)

    def _cancel_orders_for_bars(self, bars: Dict[str, BarData]) -> None:
        """只撤销本次 bar 涉及标的的活动委托，并在账本标记取消中。"""
        if not self.active_orderids:
            return

        for vt_orderid in list(self.active_orderids):
            order = self.orders.get(vt_orderid)
            vt_symbol: str = order.vt_symbol if order is not None else ""

            if not vt_symbol:
                intent_id = self._orderid_intent.get(vt_orderid)
                intent = self.order_ledger.get(intent_id or "", {})
                vt_symbol = intent.get("vt_symbol", "")

            if vt_symbol not in bars:
                continue

            if order is not None and not order.is_active():
                continue

            intent_id = self._orderid_intent.get(vt_orderid)
            intent = self.order_ledger.get(intent_id or "", None)
            if intent is not None:
                intent["cancel_requested"] = True
                intent["status"] = "CANCELLING"
                intent["updated_at"] = datetime.now().isoformat()

            self.cancel_order(vt_orderid)

    def _prune_ghost_orders(self) -> None:
        """把无法确认券商状态的委托隔离，不再直接丢弃本地 id。

        旧逻辑把「orders 字典里查不到」或「SUBMITTING 超过 30 秒」
        直接 discard / 本地标记 REJECTED，但券商侧订单可能仍然有效。
        现在改为 quarantine：保留 active_orderids，暂停新意图，
        等 update_order 或人工查询券商确认终态后才解除。
        """
        stuck_seconds: float = 30.0

        for oid in list(self.active_orderids):
            order = self.orders.get(oid)

            if order is None:
                self.quarantine_orderids.add(oid)
                continue

            if order.is_active() is False and oid in self.quarantine_orderids:
                self.quarantine_orderids.discard(oid)
                continue

            try:
                order_dt = order.datetime
                now = (
                    datetime.now(order_dt.tzinfo) if order_dt.tzinfo else datetime.now()
                )
                age: float = (now - order_dt).total_seconds()
            except Exception:
                age = 0.0

            if order.status == Status.SUBMITTING and age > stuck_seconds:
                self.quarantine_orderids.add(oid)

        if self.quarantine_orderids and not getattr(self, "_quarantine_logged", False):
            self._alert(
                "WARN",
                "quarantine",
                f"[订单] {len(self.quarantine_orderids)} 个委托状态未确认，"
                "已隔离并暂停相关标的的新委托；请在 IB 端核对活动委托",
            )
            self._quarantine_logged = True
        elif not self.quarantine_orderids:
            self._quarantine_logged = False

    # ------------------------------------------------------------------
    # P0 订单账本：唯一意图 ID / 幂等 / 部分成交 / 撤单确认 / 仓位预留
    # ------------------------------------------------------------------
    def _new_intent_id(self) -> str:
        self._intent_seq += 1
        return (
            f"{datetime.now().strftime('%Y%m%d%H%M%S')}-"
            f"{self._intent_seq:04d}-{uuid.uuid4().hex[:8]}"
        )

    def _active_intent_for_symbol(self, vt_symbol: str) -> dict | None:
        for intent in self.order_ledger.values():
            if (
                intent.get("vt_symbol") == vt_symbol
                and intent.get("status") not in _TERMINAL_INTENT_STATUSES
                and int(intent.get("remaining", 0)) > 0
            ):
                return intent
        return None

    def _pending_order_qty(self, vt_symbol: str) -> int:
        """未成交订单占用的净股数（买为正、卖为负）。"""
        qty: float = 0.0
        for intent in self.order_ledger.values():
            if (
                intent.get("vt_symbol") != vt_symbol
                or intent.get("status") in _TERMINAL_INTENT_STATUSES
            ):
                continue
            remaining = float(intent.get("remaining", 0))
            if intent.get("side") == "BUY":
                qty += remaining
            else:
                qty -= remaining
        return int(qty)

    def _register_intent(
        self,
        plan: dict,
        side: str,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        reason: str,
    ) -> dict:
        intent_id: str = self._new_intent_id()
        now: str = datetime.now().isoformat()
        intent: dict = {
            "intent_id": intent_id,
            "vt_symbol": plan["vt_symbol"],
            "side": side,
            "direction": direction.value,
            "offset": offset.value,
            "qty": int(volume),
            "filled": 0,
            "order_filled": 0,       # 订单回报的累计成交（券商口径）
            "trade_filled": 0,       # 去重后的成交回报合计
            "remaining": int(volume),
            "status": "CREATED",
            "price": float(price),
            "signal_price": float(plan.get("price", price)),
            "pos": int(plan.get("pos", 0)),
            "target": int(plan.get("target", 0)),
            "reason": reason,
            "created_at": now,
            "updated_at": now,
            "orderids": [],
            "tradeids": [],
            "cancel_requested": False,
            "seen_broker": False,
        }
        if plan.get("exit"):
            intent["exit"] = dict(plan["exit"])
        self.order_ledger[intent_id] = intent
        self._symbol_active_intent[plan["vt_symbol"]] = intent_id
        return intent

    def _submit_order_intent(
        self,
        plan: dict,
        side: str,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        reason: str,
    ) -> list[str]:
        vt_symbol: str = plan["vt_symbol"]
        if volume <= 0 or not self.trading:
            return []

        if self._active_intent_for_symbol(vt_symbol) is not None:
            self.write_log(f"[订单] {vt_symbol} 已有未完成交易意图，跳过重复提交")
            return []

        intent: dict = self._register_intent(
            plan, side, direction, offset, price, volume, reason
        )
        self._record_order_intent(vt_symbol, float(plan.get("price", price)), int(volume))
        if self.live_mode:
            self._journal_order(intent, plan)

        vt_orderids: list[str] = self.send_order(
            vt_symbol,
            direction,
            offset,
            float(price),
            float(volume),
            net=True,
        )

        if self.live_mode:
            self._note_order_sent(vt_symbol, bool(plan.get("is_open", False)))

        if not vt_orderids:
            intent["status"] = "REJECTED"
            intent["remaining"] = 0
            intent["updated_at"] = datetime.now().isoformat()
            self._symbol_active_intent.pop(vt_symbol, None)
            self.write_log(
                f"[订单] {vt_symbol} 本地发单失败（风控拦截或网关拒绝），"
                f"意图 {intent['intent_id']} 标记 REJECTED"
            )
            if self.live_mode:
                self._note_reject(vt_symbol)
            self._save_state_throttled(force=True)
            return []

        intent["orderids"] = list(vt_orderids)
        intent["status"] = "SUBMITTED"
        intent["updated_at"] = datetime.now().isoformat()
        for vt_orderid in vt_orderids:
            self._orderid_intent[vt_orderid] = intent["intent_id"]

        self._save_state_throttled(force=True)
        return vt_orderids

    def _journal_order(self, intent: dict, plan: dict) -> None:
        """每笔委托的决策快照（信号、通道、仓位口径、风控状态），事后可完整复盘。"""
        signal = self.signals.get(intent["vt_symbol"]) if hasattr(self, "signals") else None
        machine = getattr(signal, "machine", None)
        record: dict = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "intent_id": intent["intent_id"],
            "vt_symbol": intent["vt_symbol"],
            "side": intent["side"],
            "qty": intent["qty"],
            "price": intent["price"],
            "reason": intent["reason"],
            "plan": {k: plan.get(k) for k in ("pos", "target", "price", "diff", "is_open", "rank")},
            "machine": {
                k: getattr(machine, k, None)
                for k in ("target", "n", "entry_up", "entry_down", "long_entry",
                          "short_entry", "exit_up", "exit_down", "cci", "traded",
                          "hold_high", "lock_stop")
            } if machine is not None else {},
            "exit": intent.get("exit"),
            "unit": getattr(signal, "unit", None),
            "context": {
                "account_balance": self.account_balance,
                "account_available": self.account_available,
                "risk_equity": self._risk_equity(),
                "capital": self.capital,
                "breadth": getattr(self, "market_breadth", None),
                "er": getattr(self, "er_values", {}).get(intent["vt_symbol"]),
                "safety_mode": self.safety_mode,
                "close_only": bool(self.close_only),
                "trading_blocked": bool(self._trading_blocked),
                "hold": self._state_dict("_hold_symbols").get(intent["vt_symbol"]),
                "last_daily_date": str(
                    self._state_dict("_last_daily_dates").get(intent["vt_symbol"])
                    or self._last_daily_date
                ),
            },
        }
        base = Path(self.state_dir) if self.state_dir else Path.cwd() / "state"
        try:
            base.mkdir(parents=True, exist_ok=True)
            with open(base / f"orders_{datetime.now():%Y%m%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            self._log_throttled("order_journal", f"[订单] 写决策快照失败: {exc}", 600)

    # ------------------------------------------------------------------
    # 实盘执行保护：按交易日计数 / 拒单熔断 / 频率上限 / 价格偏离
    # ------------------------------------------------------------------
    def _day_counters(self) -> dict:
        """按港股交易日重置的计数器：orders（开仓委托数）/ rejects / frozen。"""
        today: str = self._today().isoformat()
        counters: dict = self.__dict__.get("_order_counters") or {}
        if counters.get("date") != today:
            counters = {"date": today, "orders": {}, "rejects": {}, "frozen": {}}
            self._order_counters = counters
        for key in ("orders", "rejects", "frozen"):
            counters.setdefault(key, {})
        return counters

    def _note_order_sent(self, vt_symbol: str, is_open: bool) -> None:
        times: deque = self.__dict__.get("_order_times")
        if times is None:
            times = deque()
            self._order_times = times
        times.append(datetime.now())
        if is_open:
            orders: dict = self._day_counters()["orders"]
            orders[vt_symbol] = int(orders.get(vt_symbol, 0)) + 1

    def _note_reject(self, vt_symbol: str) -> None:
        counters: dict = self._day_counters()
        rejects: dict = counters["rejects"]
        count: int = int(rejects.get(vt_symbol, 0)) + 1
        rejects[vt_symbol] = count
        limit: int = int(getattr(self, "max_rejects_per_symbol_day", 0) or 0)
        if limit > 0 and count >= limit and vt_symbol not in counters["frozen"]:
            counters["frozen"][vt_symbol] = f"当日拒单 {count} 次"
            self._alert(
                "CRIT",
                f"reject_freeze_{vt_symbol}",
                f"[订单] {vt_symbol} 当日已被拒单 {count} 次，冻结该标的至下一交易日；"
                "请查 IB 拒单原因（价位/手数/权限/资金）",
            )

    def _pretrade_order_check(
        self, vt_symbol: str, side: str, price: float, is_open: bool
    ) -> str:
        """实盘发单前检查，返回拒绝原因（空串 = 放行）。平仓只受频率与冻结约束。"""
        frozen: str = self._day_counters()["frozen"].get(vt_symbol, "")
        if frozen:
            return f"标的已冻结（{frozen}）"

        per_minute: int = int(getattr(self, "max_orders_per_minute", 0) or 0)
        if per_minute > 0:
            times: deque = self.__dict__.get("_order_times") or deque()
            cutoff: datetime = datetime.now() - timedelta(seconds=60)
            while times and times[0] < cutoff:
                times.popleft()
            if len(times) >= per_minute:
                return f"全局委托频率超限（每分钟 {per_minute} 笔）"

        if not is_open:
            return ""

        per_day: int = int(getattr(self, "max_orders_per_symbol_day", 0) or 0)
        sent: int = int(self._day_counters()["orders"].get(vt_symbol, 0))
        if per_day > 0 and sent >= per_day:
            return f"当日开仓委托已达上限 {per_day} 笔"

        max_age: float = float(getattr(self, "max_quote_age_for_open", 0.0) or 0.0)
        max_dev: float = float(getattr(self, "max_price_deviation", 0.0) or 0.0)
        if max_age <= 0 and max_dev <= 0:
            return ""

        tick: TickData | None = self.pbg.last_ticks.get(vt_symbol)
        if tick is None or not tick.last_price:
            return "无最新有效行情"
        if max_age > 0:
            seen: datetime | None = self._state_dict("_last_valid_tick_wall").get(vt_symbol)
            if seen is None or (self._hk_now() - seen).total_seconds() > max_age:
                return f"最新有效行情超过 {max_age:.0f} 秒"
        if max_dev > 0:
            if side == "BUY":
                ref: float = float(tick.ask_price_1 or 0.0) or float(tick.last_price)
            else:
                ref = float(tick.bid_price_1 or 0.0) or float(tick.last_price)
            if ref > 0 and abs(price / ref - 1.0) > max_dev:
                return f"限价 {price} 偏离最新{'卖一' if side == 'BUY' else '买一'} {ref} 超过 {max_dev:.1%}"
        return ""

    def _broker_sell_cap(self, vt_symbol: str) -> int | None:
        """禁空时卖出数量上限 = 券商多头持仓；券商数据未就绪返回 None。"""
        if not (self._broker_feed_ready or self._positions_verified):
            return None
        broker: Dict[str, int] = self._broker_net_positions()
        if not broker:
            return None
        return max(0, int(broker.get(vt_symbol, 0)))

    def _quarantine_symbols(self) -> tuple[set, int]:
        """隔离委托涉及的标的；无法归属标的的委托数单独返回（只能全局暂停）。"""
        symbols: set = set()
        unknown: int = 0
        orders: dict = getattr(self, "orders", {}) or {}
        for vt_orderid in self.quarantine_orderids:
            order = orders.get(vt_orderid)
            vt_symbol: str = getattr(order, "vt_symbol", "") if order is not None else ""
            if not vt_symbol:
                intent_id = self._orderid_intent.get(vt_orderid)
                vt_symbol = self.order_ledger.get(intent_id or "", {}).get("vt_symbol", "")
            if vt_symbol:
                symbols.add(vt_symbol)
            else:
                unknown += 1
        return symbols, unknown

    def _submit_plans(self, plans: List[dict], reason: str) -> None:
        """所有发单统一入口：先幂等检查，再按计划生成唯一意图。"""
        if not self.trading:
            return

        if self.live_mode and self._recovery_pending:
            if not self._recovery_logged:
                self.write_log("[订单] 等待券商活动委托恢复对账完成，暂不发送新委托")
                self._recovery_logged = True
            return

        if getattr(self, "_suppress_orders", False):
            # 收盘定时日切时补处理最后一根分钟 bar：只更新状态，不在收市后发单
            return

        # 隔离委托只暂停其所属标的；无法归属标的时才全局暂停
        quarantined, unknown = self._quarantine_symbols()
        if unknown:
            if not getattr(self, "_quarantine_logged", False):
                self.write_log(
                    f"[订单] {unknown} 个隔离委托无法确认所属标的，暂停全部新发单"
                )
                self._quarantine_logged = True
            return

        for plan in plans:
            vt_symbol: str = plan["vt_symbol"]
            diff: int = int(plan.get("diff", 0))
            if not diff:
                continue

            if vt_symbol in quarantined:
                self._log_throttled(
                    f"quarantine_{vt_symbol}",
                    f"[订单] {vt_symbol} 存在状态未确认的隔离委托，暂停该标的发单",
                )
                continue

            pos: int = int(plan.get("pos", 0))
            active: dict | None = self._active_intent_for_symbol(vt_symbol)
            if active is not None:
                self._log_throttled(
                    f"intent_{active.get('intent_id', vt_symbol)}",
                    f"[订单] {vt_symbol} 已有未完成意图 {active.get('intent_id', '')}"
                    f"（{active.get('status', '')}），跳过本轮委托",
                    3600,
                )
                continue

            pricetick: float = self.get_pricetick(vt_symbol)
            base_price: float = float(plan.get("price", 0.0))

            if diff > 0:
                order_price: float = self._round_price(
                    vt_symbol,
                    base_price + self._price_add(base_price, pricetick),
                    pricetick,
                )
                side: str = "BUY"
                direction: Direction = Direction.LONG
                offset: Offset = Offset.OPEN if pos >= 0 else Offset.CLOSE
            else:
                if not self.allow_short and pos <= 0:
                    continue
                order_price = self._round_price(
                    vt_symbol,
                    base_price - self._price_add(base_price, pricetick),
                    pricetick,
                )
                side = "SELL"
                direction = Direction.SHORT
                offset = Offset.CLOSE if pos > 0 else Offset.OPEN

            volume: int = abs(diff)
            if self.live_mode:
                # 禁空：卖出量不超过券商实际多头持仓，本地 pos 偏大时也不会卖成空头
                if side == "SELL" and not self.allow_short:
                    cap: int | None = self._broker_sell_cap(vt_symbol)
                    if cap is not None and cap < volume:
                        self._log_throttled(
                            f"sell_cap_{vt_symbol}",
                            f"[订单] {vt_symbol} 卖出 {volume} 超过券商持仓 {cap}，按 {cap} 下单",
                        )
                        volume = cap
                    if volume <= 0:
                        continue

                refuse: str = self._pretrade_order_check(
                    vt_symbol, side, order_price, bool(plan.get("is_open", False))
                )
                if refuse:
                    self._log_throttled(
                        f"refuse_{vt_symbol}_{refuse[:12]}",
                        f"[订单] {vt_symbol} {side} {volume}@{order_price} 未发送：{refuse}",
                    )
                    continue

            self._submit_order_intent(
                plan,
                side,
                direction,
                offset,
                order_price,
                volume,
                reason,
            )

    def update_order(self, order: OrderData) -> None:
        """组合引擎回调：先更新模板状态，再同步订单账本。"""
        super().update_order(order)
        self._on_order_update(order)

    def on_order(self, order: OrderData) -> None:
        """供自研事件转发器使用；引擎默认调用的是 update_order。"""
        self._on_order_update(order)

    def _on_order_update(self, order: OrderData) -> None:
        vt_orderid: str = order.vt_orderid
        intent_id: str | None = self._orderid_intent.get(vt_orderid)

        if intent_id is None:
            for iid, rec in self.order_ledger.items():
                if vt_orderid in rec.get("orderids", []):
                    intent_id = iid
                    self._orderid_intent[vt_orderid] = iid
                    break

        if intent_id is None:
            if order.is_active():
                self.quarantine_orderids.add(vt_orderid)
            else:
                self.quarantine_orderids.discard(vt_orderid)
                self.active_orderids.discard(vt_orderid)
            return

        intent: dict | None = self.order_ledger.get(intent_id)
        if intent is None:
            if order.is_active():
                self.quarantine_orderids.add(vt_orderid)
            else:
                self.quarantine_orderids.discard(vt_orderid)
                self.active_orderids.discard(vt_orderid)
            return

        qty: int = int(intent.get("qty", 0))
        traded: float = float(getattr(order, "traded", 0.0) or 0.0)
        # 旧状态文件恢复的意图没有分开的计数器：首次收到回报时用旧 filled 迁移一次
        intent.setdefault("trade_filled", int(intent.get("filled", 0) or 0))
        # 订单回报的 traded 是该委托的累计成交；成交回报另行累计到 trade_filled。
        # 两者取大，不能相加：IB 的订单状态与成交明细先后顺序不固定，旧实现在
        # 「先订单后成交」时把同一笔部分成交算两次，意图被提前判为 FILLED 并释放，
        # 下一根 bar 会对剩余量重复下单（券商端原委托仍在工作）。
        intent["order_filled"] = max(int(intent.get("order_filled", 0)), int(traded))
        filled: int = min(
            qty, max(int(intent["order_filled"]), int(intent.get("trade_filled", 0)))
        )
        status_name: str = getattr(order.status, "name", str(order.status)).upper()

        previous_status: str = str(intent.get("status", ""))
        intent["seen_broker"] = True
        intent["filled"] = filled
        intent["remaining"] = max(0, qty - filled)
        intent["updated_at"] = datetime.now().isoformat()

        if status_name in {"ALLTRADED", "FILLED"} or intent["remaining"] <= 0:
            intent["status"] = "FILLED"
            intent["filled"] = qty
            intent["remaining"] = 0
        elif status_name in {"CANCELLED", "REJECTED"} and intent.get("status") != "FILLED":
            intent["status"] = status_name
            intent["remaining"] = 0
        elif status_name == "PARTTRADED":
            intent["status"] = "PARTTRADED"
        else:
            intent["status"] = "ACTIVE"

        # 已归属到账本意图且券商已受理：不再是「来历不明」的隔离委托
        if status_name != "SUBMITTING":
            self.quarantine_orderids.discard(vt_orderid)

        if intent["status"] in _TERMINAL_INTENT_STATUSES:
            self._symbol_active_intent.pop(intent["vt_symbol"], None)
            self.quarantine_orderids.discard(vt_orderid)
            if previous_status != intent["status"]:
                self.write_log(
                    f"[订单] {intent['vt_symbol']} 意图 {intent_id} 终态="
                    f"{intent['status']}，成交 {intent['filled']}/{qty}"
                )
                if intent["status"] == "REJECTED" and getattr(self, "live_mode", False):
                    self._note_reject(intent["vt_symbol"])
        elif not order.is_active():
            intent["status"] = "CANCELLED"
            intent["remaining"] = 0
            self._symbol_active_intent.pop(intent["vt_symbol"], None)
            self.quarantine_orderids.discard(vt_orderid)

        self._save_state_throttled()

    def _on_trade_update(self, trade: TradeData) -> None:
        """成交事件同步账本；用 vt_tradeid 去重，避免部分成交重复累计。"""
        vt_orderid: str = trade.vt_orderid
        intent_id: str | None = self._orderid_intent.get(vt_orderid)
        if intent_id is None:
            for iid, rec in self.order_ledger.items():
                if vt_orderid in rec.get("orderids", []):
                    intent_id = iid
                    self._orderid_intent[vt_orderid] = iid
                    break
        if intent_id is None:
            return

        intent: dict | None = self.order_ledger.get(intent_id)
        if intent is None:
            return

        tradeid: str = (
            getattr(trade, "vt_tradeid", "")
            or f"{trade.orderid}:{getattr(trade, 'tradeid', '')}"
        )
        tradeids: list[str] = list(intent.get("tradeids", []))
        if tradeid and tradeid in tradeids:
            return
        if tradeid:
            tradeids.append(tradeid)
            intent["tradeids"] = tradeids

        qty: int = int(intent.get("qty", 0))
        # 旧状态文件恢复的意图没有分开的计数器：首次收到回报时用旧 filled 迁移一次
        intent.setdefault("trade_filled", int(intent.get("filled", 0) or 0))
        intent["trade_filled"] = int(intent["trade_filled"]) + int(trade.volume)
        filled: int = min(
            qty, max(int(intent.get("order_filled", 0)), int(intent["trade_filled"]))
        )
        intent["filled"] = filled
        intent["remaining"] = max(0, qty - filled)
        intent["updated_at"] = datetime.now().isoformat()

        if intent["remaining"] <= 0:
            intent["status"] = "FILLED"
            self._symbol_active_intent.pop(intent["vt_symbol"], None)
        elif intent["status"] not in _TERMINAL_INTENT_STATUSES:
            intent["status"] = "PARTTRADED"

        self._save_state_throttled()

    def _cancel_all_active_orders(self, reason: str) -> None:
        for vt_orderid in list(self.active_orderids):
            intent_id = self._orderid_intent.get(vt_orderid)
            intent = self.order_ledger.get(intent_id or "", None)
            if intent is not None:
                intent["cancel_requested"] = True
                intent["status"] = "CANCELLING"
                intent["updated_at"] = datetime.now().isoformat()
            self.cancel_order(vt_orderid)
        self._log_throttled(
            f"cancel_all_{reason}", f"[订单] 已请求撤销全部活动委托（{reason}）", 300
        )

    def _has_pending_orders(self) -> bool:
        if self.active_orderids or self.quarantine_orderids:
            return True
        for intent in self.order_ledger.values():
            if (
                intent.get("status") not in _TERMINAL_INTENT_STATUSES
                and int(intent.get("remaining", 0)) > 0
            ):
                return True
        return False

    def _save_state_throttled(self, force: bool = False) -> None:
        if not (self.use_state_persist and self.live_mode):
            return
        now: datetime = datetime.now()
        if (
            not force
            and self._last_state_save is not None
            and (now - self._last_state_save).total_seconds() < 3.0
        ):
            return
        self._last_state_save = now
        self._save_state()

    def on_broker_recovery_complete(
        self,
        confirmed: bool = True,
        active_orderids: set | None = None,
    ) -> None:
        """bootstrap 完成活动委托恢复后调用：解除冻结并清理账本。

        confirmed=True：已收到 IB openOrderEnd，券商侧活动委托已全部回报。
            active_orderids 给出时，以它为准——账本里没有任何委托仍在券商侧
            活动的意图一律 EXPIRED（覆盖断线重连：断线前见过回报的委托也会被核实）；
            未给出时退回旧口径（本进程从未见过券商回报的意图 EXPIRED）。
        confirmed=False：重试用尽仍未确认。未确认意图保持非终态（继续冻结所属
            标的），其他标的恢复交易，并告警人工核对。
        """
        expired: int = 0
        unconfirmed: int = 0
        now: str = datetime.now().isoformat()
        for intent in self.order_ledger.values():
            if intent.get("status") in _TERMINAL_INTENT_STATUSES:
                continue
            if confirmed and active_orderids is not None:
                still_live: bool = any(
                    oid in active_orderids for oid in intent.get("orderids", [])
                )
            else:
                still_live = bool(intent.get("seen_broker", False))
            if still_live:
                continue
            if not confirmed:
                unconfirmed += 1
                continue
            intent["status"] = "EXPIRED"
            intent["remaining"] = 0
            intent["updated_at"] = now
            self._symbol_active_intent.pop(intent["vt_symbol"], None)
            expired += 1

        # 券商确认不在活动列表里的本地委托 id：移出活动/隔离集合，避免永久卡住
        dropped: int = 0
        if confirmed and active_orderids is not None:
            for vt_orderid in list(self.active_orderids | self.quarantine_orderids):
                if vt_orderid in active_orderids:
                    continue
                self.active_orderids.discard(vt_orderid)
                self.quarantine_orderids.discard(vt_orderid)
                dropped += 1

        self._recovery_pending = False
        self._recovery_logged = False
        self._recovery_completed_at = datetime.now()
        remaining_live: int = sum(
            1 for i in self.order_ledger.values()
            if i.get("status") not in _TERMINAL_INTENT_STATUSES
        )
        if confirmed:
            self.write_log(
                f"[订单] 券商活动委托恢复完成；清理未确认意图 {expired} 个、"
                f"失效委托 id {dropped} 个，保留活动意图 {remaining_live} 个"
            )
        else:
            self._alert(
                "WARN",
                "recovery_unconfirmed",
                f"[订单] 未收到 IB 活动委托回报结束信号（重试已用尽）；{unconfirmed} 个"
                "未确认意图继续冻结其所属标的，请在 IB 端核对活动委托",
            )
        self._save_state_throttled(force=True)

    # ------------------------------------------------------------------
    # P0 故障安全模式：close_only / 应急平仓
    # ------------------------------------------------------------------
    def _emergency_active(self) -> bool:
        if not self.emergency_flatten_file:
            return False
        return Path(self.emergency_flatten_file).exists()

    def _close_only_active(self) -> bool:
        return bool(self.close_only or self._emergency_active())

    def _authoritative_positions(self) -> Dict[str, int]:
        broker: Dict[str, int] = self._broker_net_positions() if self.live_mode else {}
        if broker:
            return broker
        return {s: int(self.get_pos(s)) for s in self.vt_symbols}

    def _price_for_symbol(self, vt_symbol: str, bars: Dict[str, BarData]) -> float:
        bar: BarData | None = bars.get(vt_symbol)
        if bar is not None and bar.close_price > 0:
            return bar.close_price

        price: float = float(self.prev_prices.get(vt_symbol, 0.0) or 0.0)
        if price > 0:
            return price

        try:
            tick = self.strategy_engine.main_engine.get_tick(vt_symbol)
            price = float(getattr(tick, "last_price", 0.0) or 0.0)
        except Exception:
            price = 0.0
        return price

    def _make_close_only_plans(self, bars: Dict[str, BarData]) -> List[dict]:
        positions: Dict[str, int] = self._authoritative_positions()
        plans: List[dict] = []

        for vt_symbol, pos in positions.items():
            pos = int(pos)
            if pos == 0:
                continue

            price: float = self._price_for_symbol(vt_symbol, bars)
            if price <= 0:
                self.write_log(f"[应急] {vt_symbol} 无可用价格，暂不平仓")
                continue

            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
            qty: int = abs(pos)
            aligned: int = int(qty / lot_size) * lot_size
            residual: int = qty - aligned

            close_diffs: List[int] = []
            if aligned:
                close_diffs.append(-aligned if pos > 0 else aligned)
            if residual:
                close_diffs.append(-residual if pos > 0 else residual)

            for diff in close_diffs:
                plans.append({
                    "vt_symbol": vt_symbol,
                    "pos": pos,
                    "target": 0,
                    "price": price,
                    "lot": lot_size,
                    "diff": diff,
                    "is_open": False,
                    "rank": 0.0,
                })
        return plans

    def _handle_emergency_flatten(self, bars: Dict[str, BarData]) -> None:
        now: datetime = datetime.now()
        if not self._emergency_logged:
            self._alert(
                "CRIT",
                "emergency_flatten",
                "[应急] 检测到 flatten 标记：进入强制清仓模式，禁止任何开仓/加仓",
            )
            self._emergency_logged = True
            self._emergency_since = now
            self._global_cancel_sent = False

        # 撤掉全部活动委托；有在途意图的标的由 _submit_plans 逐个跳过，
        # 其余标的立即平仓——不再因为某一笔委托未终结就全体等待。
        if self.active_orderids:
            self._cancel_all_active_orders("emergency_flatten")

        # 隔离委托撤不掉（本地查不到委托）超过 60 秒：直接请 IB 全局撤单
        since: datetime = getattr(self, "_emergency_since", None) or now
        if (
            self.quarantine_orderids
            and not getattr(self, "_global_cancel_sent", False)
            and (now - since).total_seconds() > 60
        ):
            self._request_global_cancel()

        plans: List[dict] = self._make_close_only_plans(bars)
        if not plans:
            self._log_throttled("emergency_flat", "[应急] 当前无可平仓持仓", 600)
            return

        self._submit_plans(plans, "emergency_flatten")

    def _commit_stale_daily_bars(self, bars: Dict[str, BarData]) -> None:
        """日K合成器里残留的「前一交易日」日K：在新交易日第一根 bar 之前提交。"""
        daily_bars: dict = getattr(self.pbg, "daily_bars", None) or {}
        committed: Dict[str, date] = self._state_dict("_last_daily_dates")
        finished: Dict[str, BarData] = {}
        for vt_symbol, bar in bars.items():
            daily_bar: BarData | None = daily_bars.get(vt_symbol)
            if daily_bar is None or daily_bar.datetime.date() >= bar.datetime.date():
                continue
            # 该日已在 daily_end 提交过：残留的是收市后（竞价）bar，保持旧行为并入次日，
            # 不能再提交成一根「额外的一天」（否则通道周期被压缩）
            last: date | None = committed.get(vt_symbol)
            if last is not None and daily_bar.datetime.date() <= last:
                if self.use_corporate_actions:
                    daily_bars[vt_symbol] = None
                continue
            daily_bar.datetime = daily_bar.datetime.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            finished[vt_symbol] = daily_bar
            daily_bars[vt_symbol] = None
        if finished:
            self.on_daily_bars(finished)

    def _prepare_corporate_actions(self, bars: Dict[str, BarData]) -> None:
        if not self.use_corporate_actions:
            return
        if getattr(self, "_ca_prepare_error", None) is not None:
            raise RuntimeError("公司行动准备失败，禁止在部分调整的状态上重试；须重新初始化") from self._ca_prepare_error
        preparing = getattr(self, "_ca_preparing", False)
        self._ca_preparing = True
        try:
            self._apply_corporate_actions(bars)
        except Exception as exc:
            self._ca_prepare_error = exc
            raise
        finally:
            self._ca_preparing = preparing

    def _apply_corporate_actions(self, bars: Dict[str, BarData]) -> None:
        from corporate_actions import trading_date

        dates = self._state_dict("_ca_dates")
        closes = self._state_dict("_ca_raw_closes")
        factors = self._state_dict("_ca_factors")
        for symbol, bar in bars.items():
            day = trading_date(bar.datetime)
            previous = dates.get(symbol)
            if previous and day < previous:
                raise ValueError(f"{symbol}: 公司行动输入时间倒退")
            if previous != day:
                self._ca_book.require_resolved(symbol, previous, day)
            events = self._ca_book.between(symbol, previous, day) if previous and day > previous else []
            for event in events:
                if event.ex_date != day:
                    raise ValueError(f"{symbol}: 缺失除权日 {event.ex_date} 数据，不能猜测分红前收")
                factor = event.factor(closes[symbol])
                signal = self.signals[symbol]
                new_unit = signal.unit * event.share_ratio
                if abs(new_unit - round(new_unit)) > 1e-8:
                    raise ValueError(f"{event.id}: 每级股数出现不足一股，需现金替代/碎股规则")
                signal.machine.apply_adjustment(factor, event.share_ratio)
                signal.unit = int(round(new_unit))
                signal.target = int(round(signal.target * event.share_ratio))
                for name in ("daily_closes",):
                    values = getattr(self, name, {}).get(symbol)
                    if values is not None:
                        updated = [value * factor for value in values]
                        values.clear()
                        values.extend(updated)
                values = getattr(self, "daily_hlc", {}).get(symbol)
                if values is not None:
                    updated = [tuple(value * factor for value in hlc) for hlc in values]
                    values.clear()
                    values.extend(updated)
                if symbol in self.prev_prices:
                    self.prev_prices[symbol] *= factor
                if event.new_lot_size:
                    self.lot_sizes = dict(self.lot_sizes)
                    self.lot_sizes[symbol] = event.new_lot_size
                    signal.lot_size = event.new_lot_size
                daily = getattr(self.pbg, "daily_bars", {})
                if daily.get(symbol) and trading_date(daily[symbol].datetime) < day:
                    daily[symbol] = None
                if self.live_mode:
                    # 持仓和现金由 IB 回报更新；不自行重复调整券商账本。
                    self._target_reconciled = False
                    if getattr(self, "active_orderids", None):
                        self._cancel_orders_for_bars({symbol: bar})
                factors[event.id] = [factor, event.share_ratio]
                self.write_log(f"[公司行动] {event.id} 价格因子={factor:.8f} 股数比例={event.share_ratio:g}")
            dates[symbol] = day
            closes[symbol] = bar.close_price

    def _restore_corporate_action_state(self, symbol: str, fields: dict, meta: dict) -> None:
        if not self.use_corporate_actions:
            return
        saved = meta.get("corporate_actions", {})
        if saved.get("basis") != "asof_raw_v1" or saved.get("sha256") != self._ca_book.sha256:
            raise ValueError("持仓快照缺少时点价格锚点，或行动表已变更；须人工迁移，不恢复旧坐标")
        anchor = fields.get("price_anchor")
        current = self._state_dict("_ca_dates").get(symbol)
        if not anchor or current is None or date.fromisoformat(anchor) > current:
            raise ValueError(f"{symbol}: 缺失/超前价格锚点，不能恢复冻结状态")
        signal = self.signals[symbol]
        unit = float(fields.get("unit", 0))
        if not np.isfinite(unit) or unit <= 0 or unit != round(unit):
            raise ValueError(f"{symbol}: 快照每级真实股数无效")
        factor_product, ratio_product = 1.0, 1.0
        known = self._state_dict("_ca_factors")
        for event in self._ca_book.between(symbol, date.fromisoformat(anchor), current):
            if event.id not in known:
                raise ValueError(f"{event.id}: 预热区间未提供迁移因子，须扩展历史或人工迁移")
            factor, ratio = known[event.id]
            unit *= ratio
            if not np.isfinite(unit) or abs(unit - round(unit)) > 1e-8:
                raise ValueError(f"{event.id}: 恢复后的每级股数不足一股，需人工迁移")
            factor_product *= factor
            ratio_product *= ratio
        signal.machine.apply_adjustment(factor_product, ratio_product, include_buffer=False)
        signal.unit = int(round(unit))
        signal.target = int(signal.machine.target * signal.unit)
        machine = signal.machine
        if machine.formula is not None and len(machine.buf) >= machine.min_bars:
            out = machine.formula.run(machine.buf.to_df(), **machine.formula_params)
            machine.exit_up = float(out["EXITUP"].iloc[-1])
            machine.exit_down = float(out["EXITDOWN"].iloc[-1])

    def _request_global_cancel(self) -> None:
        """应急兜底：让 IB 撤销本账户全部活动委托（不依赖本地委托记录）。"""
        self._global_cancel_sent = True
        try:
            from ibapi.order_cancel import OrderCancel

            gateway = self.strategy_engine.main_engine.get_gateway("IB")
            gateway.api.client.reqGlobalCancel(OrderCancel())
        except Exception as exc:
            self._alert("CRIT", "global_cancel_failed", f"[应急] IB 全局撤单失败: {exc}")
            return
        self._alert(
            "CRIT",
            "global_cancel",
            "[应急] 隔离委托 60 秒未能撤销，已向 IB 发送全局撤单（reqGlobalCancel）",
        )

    def on_bars(self, bars: Dict[str, BarData]):
        """原始K线推送"""
        if not bars:
            return
        if self.use_corporate_actions and self.live_mode and not self.trading:
            from corporate_actions import map_raw_bar

            bars = {symbol: map_raw_bar(item, self._ca_book) for symbol, item in bars.items()}

        bar: BarData = list(bars.values())[0]
        self._last_bar_time = bar.datetime.isoformat()

        # 无法确认券商状态的委托进入隔离区；不再本地直接丢弃。
        self._prune_ghost_orders()

        # 上一交易日没有 daily_end 那一分钟的 bar（无成交 / 半日市）：
        # 先提交它，再用新通道计算今天的第一根 bar，避免两天合成一根日K。
        if self.use_corporate_actions:
            if getattr(self, "_ca_prepare_error", None) is not None:
                raise RuntimeError("公司行动准备失败，须重新初始化") from self._ca_prepare_error
            previous_events = len(self._state_dict("_ca_factors"))
            self._ca_preparing = True
            try:
                self._commit_stale_daily_bars(bars)
                self._prepare_corporate_actions(bars)
            except Exception as exc:
                self._ca_prepare_error = exc
                raise
            finally:
                self._ca_preparing = False
            if len(self._state_dict("_ca_factors")) > previous_events and self.use_state_persist and self.live_mode and self.trading:
                self._save_state()
        else:
            self._commit_stale_daily_bars(bars)

        # 断线保护：重连 + 委托恢复 + 持仓一致后自动解除（定时器也会检查）
        if self.live_mode and self.trading:
            self._try_clear_disconnect_block()

        emergency: bool = self._emergency_active()

        # 周期持仓对账必须在计算目标/下单之前；应急清仓时跳过，优先允许减仓。
        if not emergency:
            self._periodic_position_reconcile(bar.datetime)
            # N 就绪后补做一次目标反推（重启后状态文件可能丢失 target）
            self._maybe_reconcile_target_state()

        if emergency:
            self.safety_mode = "emergency_flatten"
        elif self.block_new_orders:
            self.safety_mode = "frozen"
        elif self.close_only:
            self.safety_mode = "close_only"
        elif self._trading_blocked:
            self.safety_mode = "reconciliation_blocked"
        else:
            self.safety_mode = "normal"
            self._emergency_logged = False

        # 只撤当前 bar 涉及标的的旧委托；应急清仓会另行全撤。
        # 不能每根单标的 bar 都 cancel_all()：10 个标的会互相撤掉
        # 刚发出、还未成交的委托。
        if not emergency:
            self._cancel_orders_for_bars(bars)

        # 实盘心跳：记录最近行情时间与持仓，供控制面板判断策略是否存活
        # 必须等策略真正启动后再写：初始化阶段会回放历史 bar，
        # 否则会把 2026-05-20 这种历史日期写成“最新心跳”，面板误判。
        if self.heartbeat_enabled and self.live_mode and self.trading:
            self._write_heartbeat(bar.datetime)

        # 应急平仓是独立执行路径：跳过 calculate_targets/send_orders，
        # 否则 targets=0 会被信号状态机重新覆盖，导致一股都平不掉。
        if emergency:
            for vt_symbol in self.vt_symbols:
                self.targets[vt_symbol] = 0
            self._update_risk_state(bars)
            self._handle_emergency_flatten(bars)
            self.pbg.update_bars(bars)
            return

        # 组合层风控：更新权益/回撤/新开仓系数（必须在计算目标与下单之前）
        self._update_risk_state(bars)

        # 计算合约目标
        self.calculate_targets(bars)

        # 发送交易委托
        self.send_orders(bars)

        # 推进日K线合成
        self.pbg.update_bars(bars)

    def on_daily_bars(self, bars: Dict[str, BarData]):
        """日K线推送"""
        if self.use_corporate_actions:
            committed = self._state_dict("_last_daily_dates")
            bars = {symbol: item for symbol, item in bars.items()
                    if symbol not in committed or item.datetime.date() > committed[symbol]}
            if not bars:
                return
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.update_daily_bar(
                bar, bool(getattr(self, "pos_data", {}).get(vt_symbol, 0))
            )

            # 记录最近一根日K的交易日，供数据新鲜度闸门判断缺口（组合 + 逐标的）
            bar_date: date = bar.datetime.date()
            if self._last_daily_date is None or bar_date > self._last_daily_date:
                self._last_daily_date = bar_date
            per_symbol: Dict[str, date] = self._state_dict("_last_daily_dates")
            if per_symbol.get(vt_symbol) is None or bar_date > per_symbol[vt_symbol]:
                per_symbol[vt_symbol] = bar_date

            # 记录日收盘价（P1-1 波动率 / P1-3 趋势选股 / P1-9 相对强度共用）
            dq = self.daily_closes.get(vt_symbol)
            if dq is None:
                maxlen: int = max(
                    self.vol_lookback, self.trend_window, self.rs_window
                ) + 1
                dq = deque(maxlen=maxlen)
                self.daily_closes[vt_symbol] = dq
            dq.append(bar.close_price)

            # 记录日 HLC（P1-4 ADX 需要 high/low/close）
            hq = self.daily_hlc.get(vt_symbol)
            if hq is None:
                need: int = max(
                    self.vol_lookback, self.trend_window, self.adx_window * 2 + 2
                ) + 1
                hq = deque(maxlen=need)
                self.daily_hlc[vt_symbol] = hq
            hq.append((bar.high_price, bar.low_price, bar.close_price))

        # P1-1：日K更新后重算已实现波动率
        if self.vol_target > 0:
            self._update_realized_vol()

        # P1-3：更新市场广度与允许开仓集合
        if self.use_trend_filter:
            self._update_trend_selection()

        # P1-4：更新各标的 ADX
        if self.use_adx_filter:
            self._update_adx()

        # P1-9：更新各标的相对强度
        if self.use_rs_filter:
            self._update_rs_filter()

        # P1-5：更新各标的 Efficiency Ratio
        if self.use_er_filter:
            self._update_er()

        # 记录当日收盘净值（供心跳的「今日盈亏」与衰减监控使用）
        # 必须限定「实盘 + 已启动」：初始化时会回放 400 天历史日线，
        # 那时 equity 还是 capital，若写入会把「今日盈亏」基数变成初始资金。
        if self.live_mode and self.trading:
            self._daily_nav = self.equity

        # P0-3：每日收盘后落盘（防 IB 每日强制断线导致重启后失忆）
        # 同样只在已启动后保存，避免初始化阶段把 baseline_equity=0 写进文件。
        if self.use_state_persist and self.live_mode and self.trading:
            self._state_backup_at = None        # 每日收盘强制留一份备份快照
            self._save_state()

        # P0-2：每日收盘自动导出执行监控（无需人工干预）
        if self.export_fills_daily and self.live_mode and self.trading:
            self._auto_export_fills()

    # ------------------------------------------------------------------
    # 引擎定时器（bootstrap 每 5 秒调用一次，与行情无关）
    # ------------------------------------------------------------------
    def on_engine_timer(self) -> None:
        """不依赖 tick 的周期检查：断线恢复 / 收盘日切 / 行情静默 / 尾盘未成交。"""
        if not (self.live_mode and self.trading):
            return
        now: datetime = self._hk_now()
        self._try_clear_disconnect_block()
        self._finalize_daily_at_close(now)
        self._check_quote_silence(now)
        self._check_eod_open_intents(now)

    @staticmethod
    def _session_close_time(day: date) -> time:
        calendar = _hk_calendar()
        if calendar is not None:
            return calendar.session_close(day)
        return time(16, 0)

    @staticmethod
    def _is_hk_trading_day(day: date) -> bool:
        calendar = _hk_calendar()
        if calendar is not None:
            return calendar.is_trading_day(day)
        return day.weekday() < 5

    def _hk_symbols_only(self) -> bool:
        symbols: List[str] = list(getattr(self, "vt_symbols", []) or [])
        return bool(symbols) and all(s.endswith(".SEHK") for s in symbols)

    def _finalize_daily_at_close(self, now: datetime) -> None:
        """收市后 1 分钟完成当日日切（港股）。

        16:00 之后的 tick 被时段闸门丢弃，15:59 的分钟 bar 原本要等次日
        9:30 第一笔 tick 才推出，当日日K随之推迟；若某标的 15:59 没有成交，
        或遇半日市（12:00 收市），日K会与次日合并成一根。这里在收市后：
          1. 推出各标的未完成的最后一根分钟 bar（只更新状态，不发单）；
          2. 把尚未提交的当日日K强制提交给 on_daily_bars。
        """
        if not self._hk_symbols_only():
            return
        today: date = now.date()
        key: str = today.isoformat()
        if getattr(self, "_daily_finalized_date", "") == key:
            return
        if not self._is_hk_trading_day(today):
            return
        close_at: datetime = datetime.combine(
            today, self._session_close_time(today), tzinfo=_HK_TZ
        ) + timedelta(minutes=1)
        if now < close_at:
            return
        self._daily_finalized_date = key

        pending_bars: Dict[str, BarData] = {}
        for vt_symbol, bar in list(self.pbg.bars.items()):
            if bar is None or bar.datetime.astimezone(_HK_TZ).date() != today:
                continue
            self.pbg.bars.pop(vt_symbol, None)
            bar.datetime = bar.datetime.replace(second=0, microsecond=0)
            pending_bars[vt_symbol] = bar

        self._suppress_orders = True
        try:
            for vt_symbol, bar in pending_bars.items():
                self.on_bars({vt_symbol: bar})
        finally:
            self._suppress_orders = False

        finished: Dict[str, BarData] = {}
        for vt_symbol, daily_bar in list(self.pbg.daily_bars.items()):
            if daily_bar is None:
                continue
            if daily_bar.datetime.astimezone(_HK_TZ).date() != today:
                continue
            daily_bar.datetime = daily_bar.datetime.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            finished[vt_symbol] = daily_bar
            self.pbg.daily_bars[vt_symbol] = None
        if finished:
            self.on_daily_bars(finished)

        self.write_log(
            f"[日切] {key} 收市日切完成：补推分钟 bar {len(pending_bars)} 根，"
            f"补提交日K {len(finished)} 根（其余已在最后一分钟提交）"
        )

    def _check_quote_silence(self, now: datetime) -> None:
        """交易时段内某标的长时间无有效 tick：告警（止损依赖行情，静默即裸奔）。"""
        limit: float = float(getattr(self, "quote_silence_seconds", 0.0) or 0.0)
        if limit <= 0 or not self._hk_symbols_only():
            return
        today: date = now.date()
        if not self._is_hk_trading_day(today):
            return
        local: time = now.time().replace(tzinfo=None)
        close_t: time = self._session_close_time(today)
        if time(9, 30) <= local < time(12, 0):
            segment_start: datetime = datetime.combine(today, time(9, 30), tzinfo=_HK_TZ)
        elif time(13, 0) <= local < close_t:
            segment_start = datetime.combine(today, time(13, 0), tzinfo=_HK_TZ)
        else:
            return

        started: datetime | None = getattr(self, "_started_wall", None)
        walls: Dict[str, datetime] = self._state_dict("_last_valid_tick_wall")
        silent: set = self._state_set("_silent_symbols")
        for vt_symbol in self.vt_symbols:
            reference: datetime = max(
                d for d in (walls.get(vt_symbol), segment_start, started) if d is not None
            )
            if (now - reference).total_seconds() < limit or vt_symbol in silent:
                continue
            silent.add(vt_symbol)
            self._alert(
                "WARN",
                f"quote_silence_{vt_symbol}",
                f"[行情] {vt_symbol} 已 {int((now - reference).total_seconds())} 秒无有效行情"
                "（止损依赖行情），请检查 IB 行情订阅/权限",
            )

    def _check_eod_open_intents(self, now: datetime) -> None:
        """收市前 10 分钟仍有未完成委托：告警（DAY 单收市即失效，计划仓位可能未达成）。"""
        today: date = now.date()
        key: str = today.isoformat()
        if getattr(self, "_eod_checked_date", "") == key or not self._is_hk_trading_day(today):
            return
        close_at: datetime = datetime.combine(
            today, self._session_close_time(today), tzinfo=_HK_TZ
        )
        if not (close_at - timedelta(minutes=10) <= now < close_at):
            return
        self._eod_checked_date = key

        pending: List[str] = [
            f"{i.get('vt_symbol')} {i.get('side')} 剩余 {i.get('remaining')}"
            for i in self.order_ledger.values()
            if i.get("status") not in _TERMINAL_INTENT_STATUSES
            and int(i.get("remaining", 0)) > 0
        ]
        if pending:
            self._alert(
                "WARN",
                "eod_open_orders",
                f"[订单] 收市前 10 分钟仍有 {len(pending)} 个未完成委托："
                + "；".join(pending[:5]),
            )

    def calculate_targets(self, bars: Dict[str, BarData]) -> None:
        """计算每个合约的目标"""
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.on_bar(bar)
            self.targets[vt_symbol] = signal.get_target()
            if getattr(signal.machine, "exit_events", None):
                self._collect_exit_events(vt_symbol, signal.machine)

            # 换池过渡期：遗留标的只允许减仓/平仓，禁止加仓或开新仓
            if vt_symbol in self.legacy_symbols:
                pos = self.get_pos(vt_symbol)
                if abs(self.targets[vt_symbol]) > abs(pos):
                    self.targets[vt_symbol] = pos

    def _collect_exit_events(self, vt_symbol: str, machine: Any) -> None:
        """取走状态机的离场事件：补上标的与持仓后追加到 exit_log（纯记录，不影响交易）。"""
        events: list = list(machine.exit_events)
        machine.exit_events.clear()
        pos: int = int(self.get_pos(vt_symbol))
        for event in events:
            record: dict = {"vt_symbol": vt_symbol, "pos": pos, **event}
            self.exit_log.append(record)
            if self.live_mode:
                self.write_log(
                    f"[离场] {vt_symbol} 原因={record['reason']}"
                    f"{'（开盘跳空）' if record.get('gap') else ''} "
                    f"止损={record['stop']:.3f}（2N={record['stop_2n']:.3f} "
                    f"通道={record['channel']:.3f} 保护={record['lock']:.3f}）"
                    f" N={record['n']:.3f} 持仓最高={record['hold_high']:.3f}"
                )
        if len(self.exit_log) > 5000:
            del self.exit_log[:len(self.exit_log) - 5000]

    def _exit_info_for(self, vt_symbol: str, bar: BarData, pos: int, diff: int) -> dict | None:
        """本次减仓/平仓对应的状态机离场事件：仅当状态机已决定离场且事件发生在同一交易日。"""
        if diff >= 0 or pos <= 0:
            return None
        signal = (self.__dict__.get("signals") or {}).get(vt_symbol)
        machine = getattr(signal, "machine", None)
        event = getattr(machine, "last_exit", None)
        if not event or machine.target != 0:
            return None
        if str(event.get("time") or "")[:10] != bar.datetime.date().isoformat():
            return None
        return event
    # ------------------------------------------------------------------
    # 组合层风控（P0-1 / P0-2 / P1-1）
    # 原则：只削减「实际下单量」，不回写状态机 target；平仓/减仓永不受限
    # ------------------------------------------------------------------
    def _update_risk_state(self, bars: Dict[str, BarData]) -> None:
        """每根 bar 更新权益、回撤、暂停期与新开仓综合缩放系数

        引擎不提供账户净值接口，权益用逐 bar 标记市值递推：
            equity_t = equity_{t-1} + Σ pos_i × (price_t − price_{t-1})
        交易成本不在此扣除（占权益比例极小，不影响回撤阈值判定）。
        """
        # 1) 权益递推（回测 / 账户数据未到达时使用）
        for vt_symbol, bar in bars.items():
            prev: float | None = self.prev_prices.get(vt_symbol)
            pos: int = self.get_pos(vt_symbol)
            if prev and pos:
                self.equity += pos * (bar.close_price - prev)
            self.prev_prices[vt_symbol] = bar.close_price

        # 实盘：一旦拿到账户现金，就用「现金 + 持仓市值」覆盖自建递推值。
        # 自建递推从 capital（风险预算）起算，与账户真实净值数量级不同
        # （capital=150 万 vs 账户约 1000 万），会让净值/敞口/回撤全部失真。
        if self.account_balance > 0:
            self.equity = self._compute_equity()

            # 只有持仓与券商一致（或回测）时，才允许初始化净值锚点。
            # 否则错误持仓算出的虚高净值会污染 peak / baseline / 回撤。
            if not self._equity_anchor_ready and (
                not self.live_mode or self._positions_verified
            ):
                self.peak_equity = self.equity
                if self._baseline_equity <= 0:
                    self._baseline_equity = self.equity
                self._equity_anchor_ready = True
                self.write_log(
                    f"[基准] 净值锚点已初始化: equity={self.equity:,.2f} "
                    f"peak={self.peak_equity:,.2f} "
                    f"baseline={self._baseline_equity:,.2f}"
                )

        # 2) 峰值与当前回撤
        if self.live_mode and not self._positions_verified:
            # 持仓未对账：不更新峰值与回撤，避免用错误数据触发风控/告警
            self.current_drawdown = 0.0
        else:
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            if self.peak_equity > 0:
                self.current_drawdown = max(
                    0.0, (self.peak_equity - self.equity) / self.peak_equity
                )
            else:
                self.current_drawdown = 0.0

        # 回撤告警：触发一次，回落到阈值 80% 以下后重置，避免反复刷屏
        # 通知走 vnpy 的 send_notification（邮件需在全局配置里启用 SMTP）
        if self.alert_drawdown > 0:
            if self.current_drawdown >= self.alert_drawdown and not self._alert_sent_dd:
                self.send_notification(
                    f"回撤告警：当前回撤 {self.current_drawdown:.1%}，"
                    f"超过阈值 {self.alert_drawdown:.0%}"
                )
                self._alert_sent_dd = True
            elif self.current_drawdown < self.alert_drawdown * 0.8:
                self._alert_sent_dd = False

        bar: BarData = list(bars.values())[0]

        # 3) 二级熔断暂停期（按日历日近似交易日）
        if self.pause_until is not None and bar.datetime >= self.pause_until:
            self.write_log(f"回撤熔断暂停期结束（{self.pause_until}），恢复开仓")
            self.pause_until = None

        # 4) 综合新开仓系数：熔断与波动率取最小值，避免双重收缩
        scales: List[float] = [1.0]

        if self.dd_level1 > 0 and self.current_drawdown >= self.dd_level1:
            scales.append(self.dd_level1_scale)

        if self.dd_level2 > 0 and self.dd_level2 >= self.dd_level1:
            if self.current_drawdown >= self.dd_level2:
                if self.pause_until is None:
                    days: int = max(1, int(self.dd_level2_pause_days * 1.4))
                    self.pause_until = bar.datetime + timedelta(days=days)
                    self.write_log(
                        f"触发二级回撤熔断：当前回撤 {self.current_drawdown:.2%}，"
                        f"暂停开新仓至 {self.pause_until}"
                    )
                scales.append(0.0)

        if self.vol_target > 0 and self.realized_vol > 0:
            raw: float = self.vol_target / self.realized_vol
            scales.append(max(self.vol_scale_min, min(self.vol_scale_max, raw)))

        self.new_open_scale = min(scales)

    def _update_realized_vol(self) -> None:
        """等权组合日收益率的年化已实现波动率（P1-1）"""
        returns_list: List[np.ndarray] = []

        for dq in self.daily_closes.values():
            closes = list(dq)
            if len(closes) < self.vol_lookback + 1:
                continue
            arr = np.asarray(closes, dtype=float)
            returns_list.append(arr[1:] / arr[:-1] - 1.0)

        if not returns_list:
            self.realized_vol = 0.0
            return

        n: int = min(len(r) for r in returns_list)
        matrix = np.vstack([r[-n:] for r in returns_list])
        mean_rets = matrix.mean(axis=0)
        self.realized_vol = float(np.std(mean_rets) * np.sqrt(252))

    def _update_trend_selection(self) -> None:
        """按市场广度动态决定多空名额，并筛选允许开仓的标的（P1-3 + P1-10）

        广度 = 收盘价在 MA(trend_window) 上方的标的占比：
            广度 >= breadth_strong  -> 做多 max_pos_strong 只，做空 max_short_strong 只
            广度 >= breadth_weak    -> 做多 max_pos_normal 只，做空 max_short_normal 只
            广度 <  breadth_weak    -> 做多 max_long_weak（0 则用 max_pos_weak），
                                       做空 max_short_weak 只

        做多候选 = MA 上方的标的，按偏离度降序（越强越优先）
        做空候选 = MA 下方的标的，按偏离度升序（跌得最惨的优先）
        已持仓标的按方向保留，离场交给海龟自身止损。
        """
        above: List[tuple] = []
        below: List[tuple] = []
        total: int = 0
        deviation: Dict[str, float] = {}
        members: frozenset | None = self._universe_members(self._last_daily_date)

        for vt_symbol, dq in self.daily_closes.items():
            if members is not None and vt_symbol not in members:
                continue
            closes = list(dq)
            if len(closes) < self.trend_window + 1:
                continue

            total += 1
            arr = np.asarray(closes, dtype=float)
            ma: float = float(arr[-self.trend_window:].mean())
            if ma <= 0:
                continue

            dev: float = arr[-1] / ma - 1.0
            deviation[vt_symbol] = dev
            if dev > 0:
                above.append((vt_symbol, dev))
            else:
                below.append((vt_symbol, dev))

        # 数据不足（预热期）：不限制
        if total == 0:
            self.allowed_symbols = set(self.signals.keys())
            self.allowed_long = set(self.signals.keys())
            self.allowed_short = set()
            return

        self.market_breadth = len(above) / total

        # 按广度决定多空名额
        long_cap: int
        short_cap: int
        if self.market_breadth >= self.breadth_strong:
            long_cap = self.max_pos_strong
            short_cap = self.max_short_strong
        elif self.market_breadth >= self.breadth_weak:
            long_cap = self.max_pos_normal
            short_cap = self.max_short_normal
        else:
            long_cap = self.max_long_weak if self.max_long_weak > 0 else self.max_pos_weak
            short_cap = self.max_short_weak

        self.max_positions = long_cap + short_cap
        self.long_cap = long_cap
        self.short_cap = short_cap

        # 做多：偏离度降序（越强越优先）
        above.sort(key=lambda x: x[1], reverse=True)
        allowed_long: set = {s for s, _ in above[:long_cap]}

        # 做空：按「接近 MA60」排序（刚跌破的优先）
        # 刚跌破的下跌空间更大；跌得最惨的往往已接近超跌反弹区
        below.sort(key=lambda x: abs(x[1]))
        if (
            self.max_short_strong == 0
            and self.max_short_normal == 0
            and self.max_short_weak == 0
        ):
            # 三个做空名额都未配置：视为「不做方向限制」，保持旧行为
            # （空头机会由总敞口额度自然约束，即「简单多空」模式）
            allowed_short: set = {s for s, _ in below}
        else:
            allowed_short = {s for s, _ in below[:short_cap]}

        # 已持仓的标的按方向保留
        for vt_symbol in self.signals.keys():
            pos: int = self.get_pos(vt_symbol)
            if pos > 0:
                allowed_long.add(vt_symbol)
            elif pos < 0:
                allowed_short.add(vt_symbol)

        self.allowed_long = allowed_long
        self.allowed_short = allowed_short
        self.allowed_symbols = allowed_long | allowed_short
        self.deviation = deviation

    @staticmethod
    def _ewm(arr: np.ndarray, alpha: float) -> np.ndarray:
        """一阶指数平滑（Wilder 平滑即 alpha = 1/n，等价通达信 SMA(X, n, 1)）"""
        out = np.empty(len(arr), dtype=float)
        acc: float = float(arr[0])
        out[0] = acc
        for i in range(1, len(arr)):
            acc = acc + alpha * (float(arr[i]) - acc)
            out[i] = acc
        return out

    def _update_adx(self) -> None:
        """逐标的计算 ADX（Wilder 平滑），用于开仓的趋势强度过滤（P1-4）

        ADX 高 = 趋势明确，低 = 震荡；仅在 ADX >= adx_threshold 时允许开新仓，
        避免海龟在无趋势环境里被假突破反复消耗。
        """
        n: int = self.adx_window
        need: int = n * 2 + 1
        alpha: float = 1.0 / n

        for vt_symbol, dq in self.daily_hlc.items():
            bars = list(dq)
            if len(bars) < need:
                continue

            highs = np.array([b[0] for b in bars], dtype=float)
            lows = np.array([b[1] for b in bars], dtype=float)
            closes = np.array([b[2] for b in bars], dtype=float)

            prev_close = np.concatenate(([closes[0]], closes[:-1]))
            prev_high = np.concatenate(([highs[0]], highs[:-1]))
            prev_low = np.concatenate(([lows[0]], lows[:-1]))

            tr = np.maximum(
                highs - lows,
                np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)),
            )
            up = highs - prev_high
            down = prev_low - lows

            plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
            minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)

            # 第一根缺少前值，剔除
            tr = tr[1:]
            plus_dm = plus_dm[1:]
            minus_dm = minus_dm[1:]
            if len(tr) < n + 1:
                continue

            tr_s = self._ewm(tr, alpha)
            plus_s = self._ewm(plus_dm, alpha)
            minus_s = self._ewm(minus_dm, alpha)

            with np.errstate(divide="ignore", invalid="ignore"):
                plus_di = 100.0 * plus_s / tr_s
                minus_di = 100.0 * minus_s / tr_s
                di_sum = plus_di + minus_di
                dx = 100.0 * np.abs(plus_di - minus_di) / di_sum

            dx = np.nan_to_num(dx, nan=0.0, posinf=0.0, neginf=0.0)
            adx = self._ewm(dx, alpha)

            self.adx_values[vt_symbol] = float(adx[-1])

    def _update_er(self) -> None:
        """逐标的计算 Efficiency Ratio（P1-5）

        ER = |C_t − C_{t−n}| / Σ|C_i − C_{i−1}|
        - 接近 1：价格沿单一方向干净推进（强趋势）
        - 接近 0：来回震荡，净位移远小于总路径（假突破的温床）

        与 ADX 的区别：ER 是**同步**指标，当日即可判断，不需要等趋势「形成」，
        因此不会像 ADX 那样延后入场、错过趋势第一段。
        """
        for vt_symbol, dq in self.daily_closes.items():
            closes = list(dq)
            if len(closes) < self.er_window + 1:
                continue

            seg = np.asarray(closes[-(self.er_window + 1):], dtype=float)
            net: float = abs(float(seg[-1] - seg[0]))
            path: float = float(np.abs(np.diff(seg)).sum())

            er_val: float = net / path if path > 0 else 0.0
            self.er_values[vt_symbol] = er_val

            # P1-17：记录 ER 历史，供分位数过滤使用
            hist = self.er_history.get(vt_symbol)
            if hist is None:
                hist = deque(maxlen=self.er_percentile_window)
                self.er_history[vt_symbol] = hist
            hist.append(er_val)

        # P1-18 横截面分位：当天所有标的的 ER 排名（同一天、同一标准）
        # 与 P1-17 时序分位的区别：不看各自历史，只比今天的强弱
        if self.use_er_percentile and self.er_cross_sectional and self.er_values:
            symbols = list(self.er_values.keys())
            values = np.asarray(
                [self.er_values[s] for s in symbols], dtype=float
            )
            cut: float = float(
                np.percentile(values, self.er_percentile * 100.0)
            )
            self.er_pass = {s for s in symbols if self.er_values[s] >= cut}

    def _update_rs_filter(self) -> None:
        """计算各标的相对市场平均的强度（P1-9）

        RS = 标的过去 N 日收益 − 股票池平均 N 日收益

        RS > 0 表示跑赢市场平均（强者恒强），RS < 0 表示落后。
        只看「近期」相对强弱，不依赖全历史盈亏，因此是**前瞻性**筛选，
        与「剔除历史亏损标的」的后视镜做法有本质区别。
        """
        returns: Dict[str, float] = {}

        for vt_symbol, dq in self.daily_closes.items():
            closes = list(dq)
            if len(closes) < self.rs_window + 1:
                continue

            past: float = closes[-(self.rs_window + 1)]
            if past <= 0:
                continue

            returns[vt_symbol] = closes[-1] / past - 1.0

        if not returns:
            return

        avg: float = sum(returns.values()) / len(returns)
        for vt_symbol, ret in returns.items():
            self.rs_values[vt_symbol] = ret - avg

    def on_trade(self, trade: TradeData) -> None:
        """成交回报：维护持仓成本（P1-16 敞口按成本计算用）与执行监控（P0-2）

        买入累加成本、卖出递减、做空则为负值，平仓后自然归零。

        注意：StrategyTemplate 没有 on_trade 方法，只有 update_trade；
        因此不能调用 super().on_trade()。本回调由 bootstrap 的成交事件
        转发器调用（与 update_trade 相互独立）。
        """
        self._on_trade_update(trade)

        vt_symbol: str = trade.vt_symbol
        sign: float = 1.0 if trade.direction == Direction.LONG else -1.0
        self.cost_basis[vt_symbol] = (
            self.cost_basis.get(vt_symbol, 0.0) + sign * trade.price * trade.volume
        )

        # P0-2 订单执行监控：记录成交价与信号价，计算滑点
        signal_price: float = self.signal_prices.get(vt_symbol, 0.0)
        self.fill_volumes[vt_symbol] = (
            self.fill_volumes.get(vt_symbol, 0) + trade.volume
        )

        slippage_bps: float = 0.0
        if signal_price > 0:
            raw: float = (trade.price - signal_price) / signal_price
            # 买入成交价高于信号价 = 正滑点（成本）；卖出方向取反，使「吃亏」恒为正
            slippage_bps = raw * 10000.0 * sign

        # 交易日志（含决策上下文）：把当时的 ER、市场广度、目标层级一并记下，
        # 事后复盘能看出「这笔是在什么环境下开的」；note/emotion 留空供人工填写
        machine = (
            self.signals[vt_symbol].machine if vt_symbol in self.signals else None
        )
        exit_reason: str = ""
        if sign < 0:
            intent_id = (getattr(self, "_orderid_intent", None) or {}).get(trade.vt_orderid)
            exit_info = ((getattr(self, "order_ledger", None) or {}).get(intent_id) or {}).get("exit") or {}
            if exit_info:
                exit_reason = str(exit_info.get("reason", "")) + ("+gap" if exit_info.get("gap") else "")
        self.fill_records.append(
            {
                "datetime": trade.datetime,
                "vt_symbol": vt_symbol,
                "direction": trade.direction.value,
                "offset": trade.offset.value,
                "volume": trade.volume,
                "fill_price": trade.price,
                "signal_price": signal_price,
                "slippage_bps": slippage_bps,
                "er": round(self.er_values.get(vt_symbol, 0.0), 4),
                "breadth": round(self.market_breadth, 4),
                "target_level": int(machine.target) if machine else 0,
                "exit_reason": exit_reason,
                "note": "",
                "emotion": "",
            }
        )

    def export_fill_report(self, csv_path: str) -> str:
        """导出订单执行监控报告（P0-2）

        输出两个部分：
        1. 逐笔明细（成交价 / 信号价 / 滑点 bps）
        2. 按标的汇总（成交率、平均滑点、总成交金额）

        返回实际写出的文件路径。
        """
        if not self.fill_records:
            return ""

        import csv

        detail_path: str = csv_path
        with open(detail_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "datetime",
                    "vt_symbol",
                    "direction",
                    "offset",
                    "volume",
                    "fill_price",
                    "signal_price",
                    "slippage_bps",
                    "er",
                    "breadth",
                    "target_level",
                    "exit_reason",
                    "note",
                    "emotion",
                ],
            )
            writer.writeheader()
            writer.writerows(self.fill_records)

        # 按标的汇总
        summary_path: str = csv_path.replace(".csv", "_summary.csv")
        symbols: List[str] = sorted(
            {r["vt_symbol"] for r in self.fill_records}
        )
        with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "vt_symbol",
                    "fill_count",
                    "fill_volume",
                    "order_volume",
                    "fill_rate",
                    "avg_slippage_bps",
                    "max_slippage_bps",
                    "min_slippage_bps",
                    "turnover",
                ]
            )
            for vt_symbol in symbols:
                rows = [r for r in self.fill_records if r["vt_symbol"] == vt_symbol]
                slips = [r["slippage_bps"] for r in rows]
                fill_vol: int = self.fill_volumes.get(vt_symbol, 0)
                order_vol: int = self.order_intents.get(vt_symbol, 0)
                rate: float = (fill_vol / order_vol) if order_vol > 0 else 0.0
                writer.writerow(
                    [
                        vt_symbol,
                        len(rows),
                        fill_vol,
                        order_vol,
                        round(rate, 4),
                        round(sum(slips) / len(slips), 2) if slips else 0.0,
                        round(max(slips), 2) if slips else 0.0,
                        round(min(slips), 2) if slips else 0.0,
                        round(
                            sum(r["fill_price"] * r["volume"] for r in rows), 2
                        ),
                    ]
                )

        return summary_path

    def _record_order_intent(
        self, vt_symbol: str, price: float, volume: int
    ) -> None:
        """记录下单意图（P0-2 订单执行监控：用于计算成交率与滑点基准）"""
        self.signal_prices[vt_symbol] = price
        self.order_intents[vt_symbol] = (
            self.order_intents.get(vt_symbol, 0) + volume
        )

    def _risk_equity(self) -> float:
        """风控分母：实盘只用 IB 净值，回测保持 capital 以复现基线。

        实盘拿不到账户净值时返回 0（权重与敞口额度随之为 0，新开仓全部被拦），
        绝不回退到 capital 递推值——E-1 就是 account_balance=0 时回退到
        回测资金 150 万，按 26 万账户的 6 倍规模下了单。
        """
        if self.live_mode:
            return float(self.account_balance) if self.account_balance > 0 else 0.0
        return float(self.capital)

    def _cap_target_by_weight(
        self, vt_symbol: str, target: int, price: float, pos: int = 0
    ) -> int:
        """按单标权重上限裁剪目标持仓（P0-1），只削减新增敞口。

        首次建仓（pos=0）：目标裁到权重上限；
        加仓（pos≠0 且同向）：只限制增量，保证最终数量不超过上限，
        但绝不减少已有持仓 —— 否则会出现「涨超限卖出、跌了再买」的
        反复调仓（实测成交笔数放大 58 倍以上）。
        """
        if self.max_symbol_weight <= 0 or price <= 0 or target == 0:
            return target

        lot_size: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
        cap_shares: int = int(
            (self._risk_equity() * self.max_symbol_weight) / price / lot_size
        ) * lot_size
        cap_shares = max(0, cap_shares)

        if abs(target) <= cap_shares:
            return target

        sign: int = 1 if target > 0 else -1
        if pos and (pos > 0) == (target > 0):
            # 已有同向持仓：保留现有数量，只砍掉超出上限的增量
            capped: int = max(abs(pos), cap_shares)
        else:
            capped = cap_shares
        return sign * capped

    def _cap_new_open_by_exposure(self, plans: List[dict], bars: Dict[str, BarData]) -> None:
        """按账户总敞口约束新增敞口（P0-1）。

        修复点：
          1. 遍历全部 vt_symbols，不再只看当前单标的 bar；
          2. 分母用账户净值（实盘），不是静态 capital；
          3. 把账本里未成交订单的预留敞口也计入。
        """
        use_split: bool = self.max_long_exposure > 0 or self.max_short_exposure > 0
        if not use_split and self.max_gross_exposure <= 0:
            return

        risk_equity: float = self._risk_equity()
        long_gross: float = 0.0
        short_gross: float = 0.0
        total_gross: float = 0.0

        # 实盘以券商持仓为准，并取「成本 / 市值」中较大者，宁可少开不可超限
        live: bool = bool(getattr(self, "live_mode", False))
        positions: Dict[str, int] = self._authoritative_positions() if live else {}

        for vt_symbol in self.vt_symbols:
            pos: int = int(positions.get(vt_symbol, self.get_pos(vt_symbol)))
            price: float = self._price_for_symbol(vt_symbol, bars)
            if not pos or price <= 0:
                continue

            market_value: float = abs(pos) * price
            if self.use_cost_exposure:
                cost: float = abs(float(self.cost_basis.get(vt_symbol, 0.0)))
                if live:
                    # 实盘成本以 IB 平均成本为准；本地累加成本重启后可能丢失或失真
                    broker_cost: float = self._broker_avg_cost(vt_symbol)
                    if broker_cost > 0:
                        cost = broker_cost * abs(pos)
                # 旧写法（2026-11-22 按要求恢复）：只按 cost_basis 计，不回退市值。
                # 回测引擎不调用 on_trade，cost_basis 恒为 0，已有持仓敞口按 0 计；
                # 实盘下一行仍取与市值的较大者，不受影响。
                value: float = cost
                if live:
                    value = max(value, market_value)
            else:
                value = market_value

            total_gross += value
            if pos > 0:
                long_gross += value
            else:
                short_gross += value

        for intent in self.order_ledger.values():
            if intent.get("status") in _TERMINAL_INTENT_STATUSES:
                continue
            remaining: float = float(intent.get("remaining", 0))
            if remaining <= 0:
                continue
            price = self._price_for_symbol(intent["vt_symbol"], bars)
            if price <= 0:
                continue
            pending_value: float = remaining * price
            if intent.get("side") == "BUY":
                long_gross += pending_value
            else:
                short_gross += pending_value
            total_gross += pending_value

        if use_split:
            long_budget: float = risk_equity * self.max_long_exposure - long_gross
            short_budget: float = risk_equity * self.max_short_exposure - short_gross
            budget: float = 0.0
        else:
            budget = risk_equity * self.max_gross_exposure - total_gross
            long_budget = budget
            short_budget = budget

        for p in sorted(plans, key=lambda x: x.get("rank", 0.0), reverse=True):
            if not p["is_open"] or not p["diff"]:
                continue

            need: float = abs(p["diff"]) * p["price"]

            if use_split:
                if p["diff"] > 0:
                    if long_budget <= 0 or need > long_budget:
                        p["diff"] = 0
                    else:
                        long_budget -= need
                else:
                    if short_budget <= 0 or need > short_budget:
                        p["diff"] = 0
                    else:
                        short_budget -= need
            else:
                if budget <= 0 or need > budget:
                    p["diff"] = 0
                else:
                    budget -= need

        for p in plans:
            if p["is_open"] and not p["diff"] and p.get("desired"):
                self._record_plan_blocked(p, "exposure")

    @staticmethod
    def _count_weekdays(later: date, earlier: date, limit: int = 60) -> int:
        """统计 (earlier, later] 之间的工作日数，最多数 limit 个。"""
        count: int = 0
        cursor: date = later
        while cursor > earlier and count <= limit:
            if cursor.weekday() < 5:
                count += 1
            cursor -= timedelta(days=1)
        return count

    def _check_data_stale(self, vt_symbol: str | None = None) -> bool:
        """检查历史日线是否陈旧（防止数据缺口导致假突破）。

        数据库缺交易日时，启动回放的历史日线会停在缺口之前，ENTRYUP 与
        CCI 都基于过期通道 —— 价格越过旧通道就会被误判为突破。

        判据分两级：
          · 工作日缺口 > max_daily_gap_weekdays（默认 1，即最后日线必须是
            上一个工作日）—— 精确覆盖周末与单日假期；
          · 自然日缺口 > max_daily_gap_days —— 兜底超长缺口。
        max_daily_gap_days = 0 表示整体关闭检查。

        vt_symbol 给出时按该标的自己的最近日K判断（单只缺数不会被组合
        最大日期掩盖）；港股按交易日历计数（节假日后首日不误封）。
        初始化回放期间（trading=False）不判断：此时日期在逐日推进，必然「陈旧」。
        """
        if not self.live_mode or self.max_daily_gap_days <= 0:
            return False
        if not getattr(self, "trading", True):
            return False

        last: date | None = None
        if vt_symbol:
            last = self._state_dict("_last_daily_dates").get(vt_symbol)
        if last is None:
            last = self._last_daily_date
        if last is None:
            return False

        today: date = self._today()
        gap_days: int = (today - last).days
        stale: bool = False
        trading_gap: int = 0
        if gap_days >= 1:
            trading_gap = self._count_trading_gap(today, last, vt_symbol)
            stale = (
                trading_gap > self.max_daily_gap_weekdays
                or gap_days > self.max_daily_gap_days
            )

        logged: set = self._state_set("_data_stale_logged_symbols")
        key: str = vt_symbol or "*"
        if not stale:
            logged.discard(key)
            if key == "*":
                self._data_stale_logged = False
            return False

        if key not in logged and not (key == "*" and self._data_stale_logged):
            logged.add(key)
            if key == "*":
                self._data_stale_logged = True
            self._alert(
                "WARN",
                f"data_stale_{key}",
                f"[数据] {vt_symbol or '组合'} 历史日线停在 {last}"
                f"（缺 {trading_gap} 个交易日/工作日，自然日 {gap_days} 天），"
                "已禁止新开仓；请补齐缺失的分钟线后重启策略",
            )
        return True

    def _count_trading_gap(self, today: date, last: date, vt_symbol: str | None) -> int:
        """(last, today] 之间的交易日数：港股用交易日历，其他市场按工作日。"""
        is_hk: bool = (vt_symbol or "").endswith(".SEHK") or (
            not vt_symbol and self._hk_symbols_only()
        )
        calendar = _hk_calendar() if is_hk else None
        if calendar is not None and calendar.is_covered(today):
            return calendar.count_trading_days(today, last)
        return self._count_weekdays(today, last)

    def _block_new_open_if_data_stale(self, plans: List[dict]) -> None:
        """数据陈旧时把「新增敞口」计划清零（逐标的判断），平仓与减仓不受影响。"""
        for p in plans:
            if p["is_open"] and p["diff"] and self._check_data_stale(p.get("vt_symbol")):
                p["diff"] = 0
                self._record_plan_blocked(p, "data_stale")

    def _block_new_open_by_guards(self, plans: List[dict]) -> None:
        """实盘账户级前置条件不满足时禁止新开仓（平仓不受影响）。"""
        if not self.live_mode:
            return
        reasons: List[str] = []
        if self.account_balance <= 0:
            reasons.append("尚未收到目标账户净值")
        if getattr(self, "_capital_mismatch", False):
            reasons.append("capital 与账户净值不一致")
        if getattr(self, "_state_write_failed", False):
            reasons.append("状态文件连续写入失败")
        silent: set = self._state_set("_silent_symbols")
        blocked: bool = False
        for p in plans:
            if not (p["is_open"] and p["diff"]):
                continue
            if reasons or p.get("vt_symbol") in silent:
                p["diff"] = 0
                blocked = True
                self._record_plan_blocked(p, "account_guard")
        if blocked and reasons:
            self._log_throttled(
                "open_guard", "[保护] 已拦截新开仓：" + "；".join(reasons), 600
            )

    def _pretrade_cash_gate(self, plans: List[dict], bars: Dict[str, BarData]) -> None:
        """下单前账户可用资金 / 当日亏损保护。平仓、减仓永不拦截。"""
        self.funds_blocked = False
        if not self.live_mode:
            return

        for p in plans:
            if not p["is_open"] or not p["diff"]:
                continue

            if (
                self.max_daily_loss > 0
                and self._daily_nav is not None
                and self._daily_nav > 0
                and self.equity <= self._daily_nav * (1.0 - self.max_daily_loss)
            ):
                p["diff"] = 0
                self.funds_blocked = True

        if self.max_order_cash_ratio <= 0:
            return

        # 不使用融资：买单额度取「可用资金」与「现金 = 净值 − 多头市值」的较小者。
        # IB 的 AvailableFunds 含融资额度，E-1 正是靠它买成了 1.66 倍敞口。
        available: float = float(self.account_available or 0.0)
        if self.account_balance > 0:
            long_value: float = 0.0
            for vt_symbol, pos in self._authoritative_positions().items():
                if pos > 0:
                    long_value += pos * max(self._price_for_symbol(vt_symbol, bars), 0.0)
            available = min(available, max(0.0, self.account_balance - long_value))
        ratio: float = max(0.0, min(1.0, self.max_order_cash_ratio))
        if self.min_margin_buffer > 0:
            ratio = min(ratio, 1.0 - self.min_margin_buffer)
        limit: float = max(0.0, available * ratio)

        used: float = 0.0
        for intent in self.order_ledger.values():
            if intent.get("status") in _TERMINAL_INTENT_STATUSES:
                continue
            if intent.get("side") != "BUY":
                continue
            price = self._price_for_symbol(intent["vt_symbol"], bars)
            used += float(intent.get("remaining", 0)) * max(price, 0.0)

        for p in sorted(plans, key=lambda x: x.get("rank", 0.0), reverse=True):
            if not p["is_open"] or p["diff"] <= 0:
                continue
            need: float = abs(p["diff"]) * p["price"]
            if used + need > limit:
                p["diff"] = 0
                self.funds_blocked = True
                self._record_plan_blocked(p, "cash")
            else:
                used += need

        if self.funds_blocked:
            # 旧实现按「上一轮是否拦截」复位标记，不同标的 bar 交替时每分钟刷屏
            self._log_throttled(
                "funds_blocked",
                f"[资金] 可用资金/当日亏损限制触发：现金额度={available:,.2f}, "
                f"买单额度={limit:,.2f}，已拦截新增买单",
                600,
            )

    def send_orders(self, bars: Dict[str, BarData]) -> None:
        """发送委托：故障安全 → 信号裁剪 → 账户级风控 → 订单账本发单。"""
        close_only: bool = self.close_only
        blocked: bool = bool(self._trading_blocked)
        hold: Dict[str, str] = self._state_dict("_hold_symbols")

        # block_new_orders 是人工全冻结开关（含止损）；应急清仓由 on_bars 独立路径覆盖。
        if self.block_new_orders:
            if not self._block_logged:
                self.write_log(
                    "[保护] block_new_orders=true，暂停发送一切新委托（含止损）；"
                    "只想禁止开仓请改用 close_only"
                )
                self._block_logged = True
            return
        self._block_logged = False

        # 持仓校验未通过：不再全冻结（E-3：那会连止损都发不出），
        # 改为全部标的进入持有保护——禁止开仓/加仓，止损/离场照常。
        if blocked:
            self._log_throttled(
                "trading_blocked",
                "[对账] 持仓校验未通过：禁止开仓/加仓，止损/离场按券商持仓照常执行",
                1800,
            )

        # close_only / 持有保护时以券商净持仓为权威，避免本地状态错误继续发单。
        use_broker: bool = close_only or blocked or bool(hold)
        authoritative: Dict[str, int] = (
            self._authoritative_positions() if use_broker else {}
        )
        plans: List[dict] = []
        risk_equity: float = self._risk_equity() if self.live_mode else 0.0

        # 阶段一：收集计划 + 新开仓缩放 + 单标权重裁剪
        for vt_symbol, bar in bars.items():
            target: int = self.targets[vt_symbol]
            guarded: bool = blocked or vt_symbol in hold
            pos: int = (
                authoritative.get(vt_symbol, self.get_pos(vt_symbol))
                if (close_only or guarded)
                else self.get_pos(vt_symbol)
            )
            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
            price: float = bar.close_price

            # 反推未完成前不允许把 target=0 当清仓（见 _block_pending_...）
            if self._block_pending_reconcile_liquidation(vt_symbol, pos, target):
                continue

            if vt_symbol in hold and pos == 0 and target == 0:
                hold.pop(vt_symbol, None)
                self.write_log(f"[对账] {vt_symbol} 已清仓，解除持有保护")
                guarded = blocked

            veto: str = ""      # P0 诊断：记录本次目标被谁削减

            if guarded:
                # 持有保护：不加仓、也不按目标自动减仓；状态机给出止损/离场才全平
                target = 0 if target == 0 else pos
                if target == 0:
                    veto = "hold_guard"
            elif close_only:
                # 只允许减少 |pos|，不允许反向或新增敞口。
                if pos > 0:
                    target = max(0, min(target, pos))
                elif pos < 0:
                    target = min(0, max(target, pos))
                else:
                    target = 0
                veto = "close_only"

            # 方向冲突保护：持仓为多、目标为空（或反之）时不做任何操作。
            # 正常平仓目标为 0，不受影响。
            if pos and target and (pos > 0) != (target > 0):
                self.write_log(
                    f"[保护] {vt_symbol} 持仓方向({pos}) 与目标({target}) 冲突，跳过"
                )
                continue

            # 是否属于「新增敞口」方向（开仓或加仓）；平仓/减仓为 False
            if target > pos:
                is_open: bool = pos >= 0
            elif target < pos:
                is_open: bool = pos <= 0
            else:
                is_open = False

            if is_open and target != 0:
                # P1-6 风险预算恒定：持仓数越少，单只预算越大
                # （后续仍受单标权重上限与总敞口上限约束，不会无限放大）
                if pos == 0 and self.use_risk_budget and self.max_positions > 0:
                    budget_scale: float = self.risk_budget_base / self.max_positions
                    scaled: int = int(target * budget_scale / lot_size) * lot_size
                    if scaled != target:
                        veto = "risk_budget"
                    target = scaled

                # P1-7 固定预算 / P1-15 多空分开预算
                # 持仓上限越少（弱市），单只预算越大；仍受单标权重与总敞口上限约束
                if pos == 0 and self.use_fixed_budget and self.max_positions > 0:
                    budget: float
                    if (
                        self.use_split_budget
                        and self.long_cap > 0
                        and self.short_cap > 0
                    ):
                        # 多头与空头各用一份预算池，弱市不因「空头名额多」而稀释多头
                        if target > 0:
                            budget = (
                                self.capital * self.long_budget_ratio / self.long_cap
                            )
                        else:
                            budget = (
                                self.capital
                                * (1.0 - self.long_budget_ratio)
                                / self.short_cap
                            )
                    else:
                        budget = self.capital / self.max_positions

                    budget_shares: int = int(budget / price / lot_size) * lot_size
                    if abs(target) > budget_shares:
                        target = budget_shares if target > 0 else -budget_shares
                        veto = "fixed_budget"

                if self.new_open_scale <= 0:
                    # 二级熔断：连加仓也禁止，保持现有仓位
                    target = pos
                    veto = "dd_level2"
                elif pos == 0 and self.new_open_scale < 1.0:
                    # 一级熔断 / 高波动：仅缩减首次建仓
                    scaled = int(target * self.new_open_scale / lot_size) * lot_size
                    if scaled != target:
                        veto = "open_scale"
                    target = scaled

            # 时点股票池：非成员不新开仓（已持仓按原规则离场）
            if is_open and pos == 0 and target != 0:
                members = self._universe_members(bar.datetime.date())
                if members is not None and vt_symbol not in members:
                    target = pos
                    veto = "universe"

            # P1-3 + P1-10 自适应选股：按方向检查各自的允许集合
            if is_open and pos == 0 and target != 0 and self.use_trend_filter:
                if target > 0 and vt_symbol not in self.allowed_long:
                    target = pos
                    veto = "trend_filter"
                elif target < 0 and vt_symbol not in self.allowed_short:
                    target = pos
                    veto = "trend_filter"

            # P1-4 ADX 趋势强度过滤：ADX 不足说明无趋势，不开新仓
            if is_open and pos == 0 and target != 0 and self.use_adx_filter:
                if self.adx_values.get(vt_symbol, 0.0) < self.adx_threshold:
                    target = pos
                    veto = "adx_filter"

            # P1-5 / P1-17 Efficiency Ratio 过滤：
            # 固定阈值（原）或历史分位（自适应，消除牛熊市阈值松紧不一的问题）
            if is_open and pos == 0 and target != 0 and self.use_er_filter:
                er_now: float = self.er_values.get(vt_symbol, 0.0)
                if self.use_er_percentile and self.er_cross_sectional:
                    # P1-18 横截面：当天排名未进入前 (1-er_percentile) 的不开仓
                    if self.er_pass and vt_symbol not in self.er_pass:
                        target = pos
                        veto = "er_cross_section"
                elif self.use_er_percentile:
                    # P1-17 时序：与该标的自身历史分位比较
                    hist = self.er_history.get(vt_symbol)
                    if hist is not None and len(hist) >= 30:
                        threshold: float = float(
                            np.percentile(
                                np.asarray(hist, dtype=float),
                                self.er_percentile * 100.0,
                            )
                        )
                        if er_now < threshold:
                            target = pos
                            veto = "er_percentile"
                elif er_now < self._er_threshold_now():
                    target = pos
                    veto = "er_threshold"

            # P1-9 相对强度过滤：跑输市场平均的标的不开新仓
            if is_open and pos == 0 and target != 0 and self.use_rs_filter:
                if self.rs_values.get(vt_symbol, 0.0) < self.rs_threshold:
                    target = pos
                    veto = "rs_filter"

            # P1-14 空头「反弹过滤」：价格在均线下方（急跌中）不开空，
            # 只在价格站上均线（反弹确认）时开空，避免追在急跌末端被 2N 止损打掉。
            if (
                is_open
                and pos == 0
                and target < 0
                and self.use_short_rebound_filter
                and self.short_rebound_ma > 0
            ):
                closes = list(self.daily_closes.get(vt_symbol, []))
                if len(closes) >= self.short_rebound_ma:
                    ma: float = (
                        sum(closes[-self.short_rebound_ma:]) / self.short_rebound_ma
                    )
                    if price < ma:
                        # 价格仍在均线下方 = 急跌中，不追空
                        target = pos
                        veto = "short_rebound"

            # 权重裁剪仅作用于首次建仓（pos==0），加仓不受单标权重上限约束。
            # 2026-11-22 按要求恢复 aad4641 之前的旧写法（复现 419% 基线），
            # 影响见 README_OPTIMIZE_PLAN.md 附录 C.6。
            if is_open and pos == 0:
                capped: int = self._cap_target_by_weight(vt_symbol, target, price)
                if capped != target:
                    veto = "symbol_weight"
                target = capped

            # P1-8 单笔加仓金额上限：价格跳空触发多个加仓档位时，拆成多笔买入，
            # 避免在 1.5N 处一次性买满（海龟原版应在 0.5N / 1.0N / 1.5N 分批）
            if is_open and pos > 0 and self.max_addon_amount > 0:
                addon_cap: int = int(self.max_addon_amount / price / lot_size) * lot_size
                if target - pos > addon_cap:
                    target = pos + addon_cap
                    veto = "addon_cap"

            # P1-11 做空单日加仓级数上限：每天最多加 N 级，避免跳空时一天打满 4 级
            if (
                is_open
                and pos <= 0
                and target < pos
                and self.max_short_addon_level > 0
            ):
                signal: TurtleSignal = self.signals[vt_symbol]
                unit_shares: int = int(signal.unit / lot_size) * lot_size
                if unit_shares > 0:
                    max_add: int = unit_shares * self.max_short_addon_level
                    if pos - target > max_add:
                        target = pos - max_add
                        veto = "short_addon_cap"

            diff: int = int((target - pos) / lot_size) * lot_size

            # 已超出单票权重上限的持仓：权重裁剪会阻止继续加仓，这里只负责提示人工处理
            if (
                self.live_mode
                and pos
                and price > 0
                and risk_equity > 0
                and self.max_symbol_weight > 0
                and abs(pos) * price / risk_equity > self.max_symbol_weight * 1.2
            ):
                self._log_throttled(
                    f"overweight_{vt_symbol}",
                    f"[风控] {vt_symbol} 持仓权重 {abs(pos) * price / risk_equity:.0%}"
                    f" 超过上限 {self.max_symbol_weight:.0%}，已禁止加仓；不自动减仓，请人工处理",
                    3600,
                )

            # 有信号却买不进（不足一手 / 预算或资金不足）：状态机必须回退空仓。
            # 否则 machine.target 停在非零、commit_daily 不再刷新入场通道与 N，
            # 该标的会被永久冻结在「以为持有」的状态（回测尤其致命）。
            signal = getattr(self, "signals", {}).get(vt_symbol)
            machine = getattr(signal, "machine", None)
            desired: int = int(self.targets[vt_symbol])
            had_level: bool = bool(machine is not None and machine.target)

            # P0 诊断：先记录（此时级别仍在），再回退状态机（不改变交易行为）
            bar_dt = getattr(bar, "datetime", None)
            day: date = bar_dt.date() if isinstance(bar_dt, datetime) else self._today()
            if diff == 0 and pos == 0 and (desired or had_level):
                reason: str = veto or ("unit_zero" if not desired else "lot_rounding")
                self._record_signal_blocked(
                    vt_symbol, day, price, desired, target, pos, reason
                )

            if target == 0 and pos == 0 and had_level:
                machine.target = 0

            # 排名分：做多按偏离度降序（越强越优先）；
            # 做空按「接近 MA60」排序（刚跌破的优先，与候选筛选保持一致）
            dev: float = self.deviation.get(vt_symbol, 0.0)
            rank_score: float = dev if target > 0 else -abs(dev)

            plans.append({
                "vt_symbol": vt_symbol,
                "pos": pos,
                "target": target,
                "desired": desired,
                "price": price,
                "day": day,
                "lot": lot_size,
                "diff": diff,
                "is_open": is_open,
                "rank": rank_score,
                "veto": veto,
                "exit": self._exit_info_for(vt_symbol, bar, pos, diff),
            })

        # 阶段二：账户级总敞口 + 可用资金 / 当日亏损门禁。
        # 两者都只削减 is_open 的 plan，平仓/减仓不受影响。
        self._cap_new_open_by_exposure(plans, bars)
        # 数据陈旧时只允许平仓/减仓：历史日线缺口会让 ENTRYUP 过期，
        # 价格越过旧通道会被误判为突破（2026-11-20 小米假突破即此原因）。
        self._block_new_open_if_data_stale(plans)
        # 账户净值未到 / capital 口径错误 / 状态写盘失败 / 行情静默：禁止新开仓
        self._block_new_open_by_guards(plans)
        self._pretrade_cash_gate(plans, bars)

        # 阶段三：所有委托统一走订单账本，保证唯一意图 ID 与幂等。
        self._submit_plans(plans, "close_only" if close_only else "normal")


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
        # 新池的合约在 GUI 预加载时可能还没从 IB 查询到，get_size() 会返回 None。
        # 股票合约乘数为 1，这里做兜底，避免 calculate_unit() 里 None * float。
        self.vt_symbol: str = vt_symbol
        self.contract_size: int = int(contract_size or 1)
        self.lot_size: int = max(1, int(lot_size or 1))
        self.capital: int = int(capital or 0)
        self.risk_level: float = float(risk_level or 0.0)

        # 状态机（信号逻辑迁入引擎，公式只出指标）
        self.machine = machine

        # 变量
        self.target: int = 0
        self.unit: int = 0

    def calculate_unit(self) -> int:
        """按当前 N 计算单级仓位股数

        重启对账时也用它：用券商持仓 / unit 反推实际持有级别。
        """
        if (
            not self.machine.n
            or self.machine.n <= 0
            or self.contract_size <= 0
            or self.capital <= 0
            or self.risk_level <= 0
        ):
            return 0

        if getattr(self, "corporate_action_unit_frozen", False) and self.target and self.unit > 0:
            return self.unit
        unit: float = (self.capital * self.risk_level) / (
            self.machine.n * self.contract_size
        )

        # 按每手股数整手对齐，不足一手归0（美股 lot_size=1 时保底1手）
        unit = int(unit / self.lot_size) * self.lot_size
        if unit == 0 and self.lot_size == 1:
            unit = 1

        return int(unit)

    def on_bar(self, bar: BarData) -> None:
        """K线推送"""
        # 推送给状态机计算信号
        self.machine.step(bar)

        # 无仓位时更新unit
        if not self.target:
            self.unit = self.calculate_unit()

        # 获取状态机目标仓位
        self.target = int(self.machine.target * self.unit)

    def update_daily_bar(self, bar: BarData, has_position: bool | None = None) -> None:
        """日K线推送；has_position 为真实持仓，决定入场通道与 N 是否冻结。"""
        self.machine.commit_daily(bar, has_position)

    def get_target(self) -> int:
        """获取信号"""
        return self.target
