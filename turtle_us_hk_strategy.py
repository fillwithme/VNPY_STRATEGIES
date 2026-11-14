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
from collections import deque
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np

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
        "short_rebound_ma"
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
                short_stop_mult=self.short_stop_mult,
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

        # ---- 组合层风控状态（P0-1 / P0-2 / P1-1）----
        self.equity: float = float(self.capital)        # 自建权益（逐bar标记市值递推）
        self.peak_equity: float = float(self.capital)   # 权益历史峰值
        self.current_drawdown: float = 0.0              # 当前回撤（相对峰值）
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
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.update_daily_bar(bar)

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

    def calculate_targets(self, bars: Dict[str, BarData]) -> None:
        """计算每个合约的目标"""
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.on_bar(bar)
            self.targets[vt_symbol] = signal.get_target()

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
        # 1) 权益递推
        for vt_symbol, bar in bars.items():
            prev: float | None = self.prev_prices.get(vt_symbol)
            pos: int = self.get_pos(vt_symbol)
            if prev and pos:
                self.equity += pos * (bar.close_price - prev)
            self.prev_prices[vt_symbol] = bar.close_price

        # 2) 峰值与当前回撤
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        if self.peak_equity > 0:
            self.current_drawdown = max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
        else:
            self.current_drawdown = 0.0

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

        for vt_symbol, dq in self.daily_closes.items():
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

            self.er_values[vt_symbol] = net / path if path > 0 else 0.0

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

    def _cap_target_by_weight(self, vt_symbol: str, target: int, price: float) -> int:
        """按单标权重上限裁剪目标持仓（P0-1），仅用于新增敞口方向"""
        if self.max_symbol_weight <= 0 or price <= 0 or target == 0:
            return target

        lot_size: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
        cap_shares: int = int((self.capital * self.max_symbol_weight) / price / lot_size) * lot_size

        if target > 0:
            return min(target, cap_shares)
        return max(target, -cap_shares)

    def _cap_new_open_by_exposure(self, plans: List[dict], bars: Dict[str, BarData]) -> None:
        """按总敞口剩余额度约束新增敞口（P0-1），就地修改 plans 的 diff

        规则：
        1. 额度不足时**整笔拒绝**（不做等比缩减），避免每天买一点造成大量碎单；
        2. 约束覆盖首次建仓**与加仓**，否则 4 级金字塔会让持仓变成首仓的 4 倍，
           总敞口被加仓悄悄突破（实测会退化成约 5 倍隐含杠杆）。
        """
        use_split: bool = self.max_long_exposure > 0 or self.max_short_exposure > 0
        if not use_split and self.max_gross_exposure <= 0:
            return

        long_gross: float = 0.0
        short_gross: float = 0.0
        total_gross: float = 0.0
        for vt_symbol, bar in bars.items():
            pos: int = self.get_pos(vt_symbol)
            if not pos:
                continue
            value: float = abs(pos) * bar.close_price
            total_gross += value
            if pos > 0:
                long_gross += value
            else:
                short_gross += value

        if use_split:
            # 分方向模式：多头与空头各用一份额度
            long_budget: float = self.capital * self.max_long_exposure - long_gross
            short_budget: float = self.capital * self.max_short_exposure - short_gross
            budget: float = 0.0
        else:
            # 总量模式：多空共用一份额度（原行为）
            budget = self.capital * self.max_gross_exposure - total_gross
            long_budget = budget
            short_budget = budget

        # 按排名分降序分配额度：优先满足「最强多头」与「最接近 MA60 的空头」
        # 避免因 vt_symbols 配置顺序导致「差标的」先占额度
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

    def send_orders(self, bars: Dict[str, BarData]) -> None:
        """发送委托（组合层风控流水线：熔断/波动率 → 单标权重 → 总敞口）"""
        plans: List[dict] = []

        # 阶段一：收集计划 + 新开仓缩放 + 单标权重裁剪
        for vt_symbol, bar in bars.items():
            target: int = self.targets[vt_symbol]
            pos: int = self.get_pos(vt_symbol)
            lot_size: int = int(self.lot_sizes.get(vt_symbol, 1)) or 1
            price: float = bar.close_price

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
                    target = int(target * budget_scale / lot_size) * lot_size

                # P1-7 固定预算：单只建仓金额 <= capital / 当前允许持仓数
                # 持仓上限越少（弱市），单只预算越大；仍受单标权重与总敞口上限约束
                if pos == 0 and self.use_fixed_budget and self.max_positions > 0:
                    budget: float = self.capital / self.max_positions
                    budget_shares: int = int(budget / price / lot_size) * lot_size
                    if abs(target) > budget_shares:
                        target = budget_shares if target > 0 else -budget_shares

                if self.new_open_scale <= 0:
                    # 二级熔断：连加仓也禁止，保持现有仓位
                    target = pos
                elif pos == 0 and self.new_open_scale < 1.0:
                    # 一级熔断 / 高波动：仅缩减首次建仓
                    target = int(target * self.new_open_scale / lot_size) * lot_size

            # P1-3 + P1-10 自适应选股：按方向检查各自的允许集合
            if is_open and pos == 0 and target != 0 and self.use_trend_filter:
                if target > 0 and vt_symbol not in self.allowed_long:
                    target = pos
                elif target < 0 and vt_symbol not in self.allowed_short:
                    target = pos

            # P1-4 ADX 趋势强度过滤：ADX 不足说明无趋势，不开新仓
            if is_open and pos == 0 and target != 0 and self.use_adx_filter:
                if self.adx_values.get(vt_symbol, 0.0) < self.adx_threshold:
                    target = pos

            # P1-5 Efficiency Ratio 过滤：ER 低说明在震荡，突破多为假信号
            if is_open and pos == 0 and target != 0 and self.use_er_filter:
                if self.er_values.get(vt_symbol, 0.0) < self.er_threshold:
                    target = pos

            # P1-9 相对强度过滤：跑输市场平均的标的不开新仓
            if is_open and pos == 0 and target != 0 and self.use_rs_filter:
                if self.rs_values.get(vt_symbol, 0.0) < self.rs_threshold:
                    target = pos

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

            # 权重裁剪仅作用于首次建仓（pos==0）。
            # 若对已持仓也裁剪，权重上限会随价格波动而变，导致「涨超限卖出、
            # 跌额度恢复再买入」的高频反复调仓（实测成交笔数放大 7 倍以上）。
            if is_open and pos == 0:
                target = self._cap_target_by_weight(vt_symbol, target, price)

            # P1-8 单笔加仓金额上限：价格跳空触发多个加仓档位时，拆成多笔买入，
            # 避免在 1.5N 处一次性买满（海龟原版应在 0.5N / 1.0N / 1.5N 分批）
            if is_open and pos > 0 and self.max_addon_amount > 0:
                addon_cap: int = int(self.max_addon_amount / price / lot_size) * lot_size
                if target - pos > addon_cap:
                    target = pos + addon_cap

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

            diff: int = int((target - pos) / lot_size) * lot_size

            # 排名分：做多按偏离度降序（越强越优先）；
            # 做空按「接近 MA60」排序（刚跌破的优先，与候选筛选保持一致）
            dev: float = self.deviation.get(vt_symbol, 0.0)
            rank_score: float = dev if target > 0 else -abs(dev)

            plans.append({
                "vt_symbol": vt_symbol,
                "pos": pos,
                "target": target,
                "price": price,
                "lot": lot_size,
                "diff": diff,
                "is_open": is_open,
                "rank": rank_score,
            })

        # 阶段二：总敞口等比缩减
        self._cap_new_open_by_exposure(plans, bars)

        # 阶段三：下单（沿用原有超价与多空方向逻辑）
        for plan in plans:
            vt_symbol: str = plan["vt_symbol"]
            diff: int = plan["diff"]
            if not diff:
                continue

            pos: int = plan["pos"]
            pricetick: float = self.get_pricetick(vt_symbol)

            if diff > 0:
                price: float = plan["price"] + self.price_add_ticks * pricetick

                # 海龟所有开平仓都会先回到仓位0，因此只需判断开仓还是平仓
                if pos < 0:
                    self.cover(vt_symbol, price, abs(diff))
                else:
                    self.buy(vt_symbol, price, abs(diff))
            else:
                # 禁止做空时跳过空头开仓（平多头不受影响）
                if not self.allow_short and pos <= 0:
                    continue

                price = plan["price"] - self.price_add_ticks * pricetick

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
