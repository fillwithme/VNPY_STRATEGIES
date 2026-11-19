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
from typing import Callable, Dict, List

import numpy as np

from vnpy.trader.constant import Direction, Interval, Status
from vnpy.trader.object import AccountData, BarData, TickData, TradeData

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
        "price_add_pct",
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
        "legacy_symbols"
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
        self.equity: float = float(self.capital)        # 组合净值（实盘优先用账户现金+持仓市值）
        self.peak_equity: float = float(self.capital)   # 净值历史峰值
        self.current_drawdown: float = 0.0              # 当前回撤（相对峰值）

        # ---- 账户资金（由 IB 通过 on_account 推送）----
        # 注意：VeighNa 的 account.balance 装的是 IB 的 TotalCashValue（现金余额），
        # 不是 NetLiquidation（净值）。净值 = 现金 + 持仓市值，由 _compute_equity 计算。
        self.account_cash: float = 0.0                  # 现金余额
        self.account_available: float = 0.0             # 可用资金

        # 策略基准净值：首次拿到账户数据时记录，之后持久化。
        # 用于算「策略自启动以来的盈亏」，与账户历史盈亏区分开。
        self._baseline_equity: float = 0.0

        # 净值锚点：只有确认持仓与券商一致后才初始化峰值/基准，
        # 避免用错误持仓算出的虚高净值污染回撤与策略盈亏。
        self._equity_anchor_ready: bool = False
        self._positions_verified: bool = False
        self._trading_blocked: bool = False
        self._block_logged: bool = False

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
        self.live_mode: bool = True

        # 实盘可观测性状态
        self._last_heartbeat_key: str = ""              # 上次心跳的分钟键（限频，避免每根 bar 写文件）
        self._alert_sent_dd: bool = False               # 回撤告警是否已发送（避免刷屏）

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

        if self.init_load_days > 0:
            days: int = self.init_load_days
        elif is_backtest:
            days = 30
        else:
            days = 400

        self.write_log(f"初始化加载 {days} 天历史数据")
        self.load_bars(days)

    def on_start(self):
        """启动"""
        self.write_log("策略启动")

        # 版本锁定：记录代码 commit 与配置指纹，便于事后归因
        if self.log_version_info:
            self._log_version_info()

        # P0-3：优先恢复上次退出时的状态机状态（防断线重启后"失忆"）
        if self.use_state_persist:
            self._load_state()

        # 实盘启动持仓校验：不一致就禁止下单，避免按错误持仓继续交易
        if self.live_mode:
            self._verify_positions_on_start()

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

    def on_account(self, account: AccountData) -> None:
        """账户资金更新（IB 推送，每秒一次）

        注意：VeighNa 的 account.balance 对应 IB 的 TotalCashValue（现金），
        不是 NetLiquidation（净值）。多币种账户下更会混淆。
        净值统一由 _compute_equity() 用「现金 + 持仓市值」计算。
        """
        self.account_cash = account.balance
        self.account_available = account.available

    def _broker_net_positions(self) -> Dict[str, int]:
        """读取券商净持仓（只保留本策略标的）"""
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
                volume = -volume

            result[vt_symbol] = result.get(vt_symbol, 0) + volume

        return result

    def _verify_positions_on_start(self) -> None:
        """启动时比对策略持仓与券商持仓

        不一致时的处理：
          · sync_positions_from_broker=True  -> 以券商为准覆盖 pos_data
          · 否则                              -> 禁止下单（send_orders 直接返回）

        这样即使 state / portfolio_strategy_data 损坏，也不会继续发错单。
        """
        if not self.check_positions_on_start:
            self._positions_verified = True
            self.write_log("[对账] 已跳过启动持仓校验（check_positions_on_start=False）")
            return

        broker: Dict[str, int] = self._broker_net_positions()
        broker_count: int = sum(1 for v in broker.values() if v)

        if self.sync_positions_from_broker:
            self.cost_basis.clear()

            for vt_symbol in self.vt_symbols:
                volume = broker.get(vt_symbol, 0)
                self.pos_data[vt_symbol] = volume
                self.targets[vt_symbol] = self.targets.get(vt_symbol, 0)

            self._positions_verified = True
            self._trading_blocked = False
            self.write_log(
                f"[对账] 已用券商持仓覆盖策略持仓（{broker_count} 个非零标的）"
            )
            return

        mismatches: List[tuple[str, int, int]] = []
        for vt_symbol in self.vt_symbols:
            strategy_volume = int(self.get_pos(vt_symbol))
            broker_volume = broker.get(vt_symbol, 0)
            if strategy_volume != broker_volume:
                mismatches.append((vt_symbol, strategy_volume, broker_volume))

        if not mismatches:
            self._positions_verified = True
            self._trading_blocked = False
            self.write_log(f"[对账] 策略持仓与券商一致（{broker_count} 个非零标的）")
            return

        self._positions_verified = False
        self._trading_blocked = True
        self.write_log(
            f"[对账] 持仓不一致，已禁止下单（{len(mismatches)} 个标的）"
        )
        for vt_symbol, strategy_volume, broker_volume in mismatches[:10]:
            self.write_log(
                f"[对账]   {vt_symbol}: 策略={strategy_volume}，券商={broker_volume}"
            )
        self.write_log(
            "[对账] 处理方式：核对券商持仓后，在配置中设置 "
            "sync_positions_from_broker=true 重启一次，或修正状态文件"
        )

    def _compute_equity(self) -> float:
        """组合净值 = 账户现金 + 策略持仓市值

        为什么不直接用自建递推：
          自建 equity 从 capital（配置的风险预算，150 万）起算，
          与账户真实净值（约 1000 万）相差 6.5 倍，导致净值/敞口/回撤全部失真。

        为什么不直接用 account.balance：
          VeighNa 的「余额」是 IB 的 TotalCashValue（现金），不是净值。

        口径：
          只统计策略标的（vt_symbols），不含手工买入的非策略持仓（如 AAPL）。
          回测中 account_cash 为 0，自动回退到自建递推的 equity。
        """
        cash: float = self.account_cash
        if cash <= 0:
            return self.equity

        market_value: float = 0.0
        for vt_symbol in self.vt_symbols:
            pos: int = self.get_pos(vt_symbol)
            if not pos:
                continue
            price: float = self.prev_prices.get(vt_symbol, 0.0)
            market_value += abs(pos) * price

        return cash + market_value

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
        except OSError:
            return

        # 持仓市值与敞口（用上一根 bar 收盘价估算，与权益递推口径一致）
        holdings: Dict[str, int] = {
            s: self.get_pos(s) for s in self.vt_symbols if self.get_pos(s)
        }
        market_value: float = 0.0
        for s, pos in holdings.items():
            price: float = self.prev_prices.get(s, 0.0)
            market_value += abs(pos) * price

        # 敞口口径：持仓市值 / 组合净值（真实杠杆倍数）
        # 注意不要用 capital（风险预算）当分母，那会把敞口放大 6 倍以上
        equity: float = self.equity
        gross: float = (market_value / equity) if equity > 0 else 0.0

        # 今日盈亏（相对上一交易日收盘净值）
        today_pnl: float = 0.0
        if self._daily_nav is not None:
            today_pnl = self.equity - self._daily_nav

        # 成交质量
        slips: List[float] = [r["slippage_bps"] for r in self.fill_records]
        avg_slip: float = (sum(slips) / len(slips)) if slips else 0.0

        payload: dict = {
            "time": dt.isoformat(),
            "strategy": self.strategy_name,
            "breadth": round(self.market_breadth, 4),
            "max_positions": self.max_positions,
            "trend_filter_enabled": bool(self.use_trend_filter),
            "positions_verified": bool(self._positions_verified),
            "trading_blocked": bool(self._trading_blocked),
            "positions": holdings,
            "fill_count": len(self.fill_records),
            # 账户
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown": round(self.current_drawdown, 4),
            "market_value": round(market_value, 2),
            # 现金：实盘用 IB 推送的账户现金，回测用 equity - 市值
            "cash": round(
                self.account_cash if self.account_cash > 0 else (self.equity - market_value),
                2,
            ),
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
        }

        path = base / f"heartbeat_{self.strategy_name}.json"
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

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
                    "avg_cost": round(abs(cost / pos), 4) if pos else 0.0,
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

    def _save_state(self) -> None:
        """保存状态机关键字段到磁盘（P0-3）

        只保存「冻结语义」相关的字段：target / entry / n / inited。
        cci 与 traded 每根/每日重置，无需持久化。
        使用临时文件 + 原子替换，避免写入中断导致文件损坏。
        """
        data: Dict[str, dict] = {}
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
            }

        # 元信息（策略基准净值）：用带下划线的特殊键，加载时按标的查不到会跳过
        data["_meta"] = {
            "baseline_equity": float(self._baseline_equity),
            "saved_at": datetime.now().isoformat(),
        }

        try:
            path = self._state_file()
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except Exception as exc:                    # 持久化失败不阻断交易
            self.write_log(f"[state] 保存失败: {exc}")

    def _load_state(self) -> None:
        """从磁盘恢复状态机（P0-3）

        恢复后强制 inited=True：日线缓冲会由后续行情继续追加，
        但入场通道与 N 值必须保留，否则会按新价格重新冻结、导致止损线错位。
        """
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
                data = json.load(f)
        except Exception as exc:
            self.write_log(f"[state] 读取失败，按全新状态运行: {exc}")
            return

        # 元信息：恢复基准净值（用于「策略累计盈亏」）
        meta = data.pop("_meta", None)
        if isinstance(meta, dict):
            saved_baseline = float(meta.get("baseline_equity", 0.0) or 0.0)
            if saved_baseline > 0:
                self._baseline_equity = saved_baseline
                self.write_log(f"[state] 恢复基准净值: {saved_baseline:,.2f}")

        restored: int = 0
        for vt_symbol, fields in data.items():
            signal = self.signals.get(vt_symbol)
            if signal is None:
                continue

            machine = signal.machine
            machine.target = int(fields.get("target", 0))
            machine.long_entry = float(fields.get("long_entry", 0.0))
            machine.short_entry = float(fields.get("short_entry", 0.0))
            machine.n = float(fields.get("n", 0.0))
            machine.entry_up = float(fields.get("entry_up", 0.0))
            machine.entry_down = float(fields.get("entry_down", 0.0))
            machine.exit_up = float(fields.get("exit_up", 0.0))
            machine.exit_down = float(fields.get("exit_down", 0.0))
            machine.inited = True
            restored += 1

        self.write_log(f"[state] 已恢复 {restored} 个标的的状态: {path}")

    def on_tick(self, tick: TickData):
        """Tick推送"""
        self._tick_count += 1
        self._last_tick_time = tick.datetime.isoformat()

        # 首笔 tick 落一条日志：区分「IB 根本没推行情」与「bar 合成异常」
        if self._tick_count == 1:
            self.write_log(
                f"[行情] 收到首笔 tick: {tick.vt_symbol} "
                f"price={tick.last_price} time={tick.datetime}"
            )

        # 心跳仍由 on_bars 写：心跳语义 = 「策略完整处理完一根 bar」。
        # tick 计数与 last_tick_time 单独放在心跳文件里，供排查行情链路。
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

    def _prune_ghost_orders(self) -> None:
        """清理「幽灵」与「卡死」委托

        两类需要清理：
          ① active_orderids 里有、但 orders 字典里查不到的（幽灵 id）
          ② orders 里存在、但状态长期停在 SUBMITTING 的（卡死）

        ② 的成因：IB 拒单（如 110 价格非法）时通过 error() 通知，
        未必触发 on_order 回调，vnpy 的 order.status 就永远停在
        SUBMITTING，is_active() 一直为 True，导致每根 bar 都去撤
        一批 IB 侧根本不存在的订单（错误码 10147）。
        """
        removed: int = 0
        stuck_seconds: float = 30.0

        for oid in list(self.active_orderids):
            order = self.orders.get(oid)

            # ① 完全查不到 → 幽灵
            if order is None:
                self.active_orderids.discard(oid)
                removed += 1
                continue

            # ② 卡在 SUBMITTING 超过 30 秒 → 判定为被 IB 静默拒绝
            try:
                order_dt = order.datetime
                now = (
                    datetime.now(order_dt.tzinfo) if order_dt.tzinfo else datetime.now()
                )
                age: float = (now - order_dt).total_seconds()
            except Exception:
                age = 0.0

            if order.status == Status.SUBMITTING and age > stuck_seconds:
                self.active_orderids.discard(oid)
                order.status = Status.REJECTED
                removed += 1

        if removed:
            self.write_log(f"[清理] 移除 {removed} 个卡死/幽灵委托 id")

    def on_bars(self, bars: Dict[str, BarData]):
        """原始K线推送"""
        if not bars:
            return

        bar: BarData = list(bars.values())[0]
        self._last_bar_time = bar.datetime.isoformat()
        self.write_log(f"{bar.datetime} - {list(bars.keys())}")

        # 先清理幽灵 id：IB 拒单（如 110 价格非法）时未必触发 on_order，
        # 被拒的 id 会残留在 active_orderids 里，越积越多。
        self._prune_ghost_orders()

        # 全撤之前委托
        self.cancel_all()

        # 实盘心跳：记录最近行情时间与持仓，供控制面板判断策略是否存活
        if self.heartbeat_enabled and self.live_mode:
            self._write_heartbeat(bar.datetime)

        # 应急平仓：检测到标记文件时清空目标仓位（只平仓，不开新仓）
        if self.emergency_flatten_file and Path(self.emergency_flatten_file).exists():
            if not getattr(self, "_emergency_logged", False):
                self.write_log("检测到应急平仓标记，清空所有目标仓位")
                self._emergency_logged = True
            for vt_symbol in self.vt_symbols:
                self.targets[vt_symbol] = 0

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

        # 记录当日收盘净值（供心跳的「今日盈亏」与衰减监控使用）
        # 必须限定「实盘 + 已启动」：初始化时会回放 400 天历史日线，
        # 那时 equity 还是 capital，若写入会把「今日盈亏」基数变成初始资金。
        if self.live_mode and self.trading:
            self._daily_nav = self.equity

        # P0-3：每日收盘后落盘（防 IB 每日强制断线导致重启后失忆）
        # 同样只在已启动后保存，避免初始化阶段把 baseline_equity=0 写进文件。
        if self.use_state_persist and self.live_mode and self.trading:
            self._save_state()

        # P0-2：每日收盘自动导出执行监控（无需人工干预）
        if self.export_fills_daily and self.live_mode and self.trading:
            self._auto_export_fills()

    def calculate_targets(self, bars: Dict[str, BarData]) -> None:
        """计算每个合约的目标"""
        for vt_symbol, bar in bars.items():
            signal: TurtleSignal = self.signals[vt_symbol]
            signal.on_bar(bar)
            self.targets[vt_symbol] = signal.get_target()

            # 换池过渡期：遗留标的只允许减仓/平仓，禁止加仓或开新仓
            if vt_symbol in self.legacy_symbols:
                pos = self.get_pos(vt_symbol)
                if abs(self.targets[vt_symbol]) > abs(pos):
                    self.targets[vt_symbol] = pos

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
        if self.account_cash > 0:
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

            if self.use_cost_exposure:
                # P1-16：用持仓成本而非市值，避免浮盈锁死加仓额度
                value: float = abs(self.cost_basis.get(vt_symbol, 0.0))
            else:
                value = abs(pos) * bar.close_price

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
        # 启动持仓校验不一致时禁止下单：只保留撤单、观察和诊断能力
        if self._trading_blocked:
            if not self._block_logged:
                self.write_log("[对账] 交易已被持仓校验拦截，不会发送任何新委托")
                self._block_logged = True
            return

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

            # P1-5 / P1-17 Efficiency Ratio 过滤：
            # 固定阈值（原）或历史分位（自适应，消除牛熊市阈值松紧不一的问题）
            if is_open and pos == 0 and target != 0 and self.use_er_filter:
                er_now: float = self.er_values.get(vt_symbol, 0.0)
                if self.use_er_percentile and self.er_cross_sectional:
                    # P1-18 横截面：当天排名未进入前 (1-er_percentile) 的不开仓
                    if self.er_pass and vt_symbol not in self.er_pass:
                        target = pos
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
                elif er_now < self.er_threshold:
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
            # 跌额度恢复再买入」的高频反复调仓（实测成交笔数放大 58 倍以上）。
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
                price: float = self._round_price(
                    vt_symbol,
                    plan["price"] + self._price_add(plan["price"], pricetick),
                    pricetick,
                )
                # P0-2：以 bar 收盘价作为滑点基准（不含超价）
                self._record_order_intent(vt_symbol, plan["price"], abs(diff))

                # 海龟所有开平仓都会先回到仓位0，因此只需判断开仓还是平仓
                if pos < 0:
                    self.cover(vt_symbol, price, abs(diff))
                else:
                    self.buy(vt_symbol, price, abs(diff))
            else:
                # 禁止做空时跳过空头开仓（平多头不受影响）
                if not self.allow_short and pos <= 0:
                    continue

                price = self._round_price(
                    vt_symbol,
                    plan["price"] - self._price_add(plan["price"], pricetick),
                    pricetick,
                )
                self._record_order_intent(vt_symbol, plan["price"], abs(diff))

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
