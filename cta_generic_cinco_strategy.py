# -*- coding: utf-8 -*-
"""
cta_generic_cinco_strategy.py - 通达信公式驱动 + 停止单 CTA 策略（vnpy_ctastrategy）

【设计理念】
基于 CincoStrategy 改造为「公式驱动 + 停止单」系统：
布林带三轨计算与移动止损完整逻辑（持仓以来最高/最低价跟踪、止损价计算、
平仓信号）全部写入通达信公式，使用 BARSLAST(开仓信号)+1 动态窗口 +
新增 HHVD/LLVD 变长窗口函数在纯向量化框架内近似「持仓以来最高/最低价」，
不依赖引擎逐根迭代执行模式。
策略侧不再包含任何布林带计算代码、持仓状态变量与限价单逻辑，
仅读取公式输出的数值列，按原版下单方式（全部使用停止单 stop=True）执行交易。

【公式输出 -> 交易动作】（对应原版 on_15min_bar 分支）
    UPPER / LOWER   -> 无持仓: buy(UPPER) + short(LOWER) 双挂停止单
    LONGSTOP        -> 持多:   sell(LONGSTOP) 停止单（移动止损）
    SHORTSTOP       -> 持空:   cover(SHORTSTOP) 停止单（移动止损）
    波动率定仓: trading_size = int(risk_level / ATR(atr_window))

【已知偏差】
持仓期价格再次突破上/下轨时 BARSLAST 收缩 -> HHVD/LLVD 窗口变小 ->
可能漏掉更早的持仓高点/低点，止损价略低/略高、平仓略晚于逐根精确版
（约 1% 边缘场景偏差）。

【使用方式】
    回测请喂 1 分钟数据（本策略由 BarGenerator 合成 15 分钟 bar）；
    json_path 填公式目录 / formulas.json / 单个 .tdx 文件（跨盘请用绝对路径）；
    formula_name 填 .tdx 文件名（即武器名），formula_params 传 JSON 覆盖公式参数。
    布林带参数 BOLLW/BOLLD 与移动止损倍数 TRAIL_LONG/TRAIL_SHORT 均通过
    formula_params 传入公式，策略侧 trailing_long/trailing_short 仅供 GUI 展示。
"""
import json
import sys
from collections import deque
from pathlib import Path

import pandas as pd
from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy_ctastrategy import CtaTemplate, BarGenerator, ArrayManager
from vnpy_ctastrategy.base import StopOrder


# ----------------------------------------------------------------------
# 工具函数：定位并导入 tdx_engine（与本策略文件解耦，便于随目录迁移）
# ----------------------------------------------------------------------
def _import_tdx_engine(extra_hints: list[str] | None = None):
    """按优先级定位 tdx_engine 模块并返回 TdxEngine 类

    查找顺序：
        1. 当前 Python 环境（若 examples/tdx_formula 已被加入 sys.path）
        2. 向上遍历目录，定位 <项目根>/examples/tdx_formula 或策略目录内 tdx_engine.py
        3. extra_hints 提示路径（如公式目录所在目录，跨盘放置时兜底）
    """
    try:
        from tdx_engine import TdxEngine
        return TdxEngine
    except ImportError:
        pass

    here = Path(__file__).resolve().parent
    for parent in here.parents:
        pkg_dir = parent / "examples" / "tdx_formula"
        if pkg_dir.exists() and (pkg_dir / "tdx_engine.py").exists():
            sys.path.insert(0, str(pkg_dir))
            from tdx_engine import TdxEngine
            return TdxEngine
        if (parent / "tdx_engine.py").exists():
            sys.path.insert(0, str(parent))
            from tdx_engine import TdxEngine
            return TdxEngine

    for hint in (extra_hints or []):
        hp = Path(hint)
        if (hp / "tdx_engine.py").exists():
            sys.path.insert(0, str(hp))
            from tdx_engine import TdxEngine
            return TdxEngine

    raise ImportError(
        "无法定位 tdx_engine.py，请将 examples/tdx_formula 加入 sys.path，"
        "或把本策略文件与 tdx_engine.py 放在同一目录。"
    )


def _resolve_formula_path(raw: str) -> str:
    """把 json_path 参数归一化为绝对路径

    - 绝对路径 / ~ 家目录：直接规范化
    - 相对路径：依次尝试「当前工作目录 / 本策略文件目录及各级父目录」为基准
    - 全部未命中时回退为 cwd/raw 的绝对形式（由 TdxEngine 抛错提示）
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
        if (base / "examples" / "tdx_formula").exists():
            break

    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        cand = base / raw
        if cand.exists():
            return str(cand.resolve())

    return str((Path.cwd() / raw).resolve())


class CtaGenericCincoStrategy(CtaTemplate):
    """"""

    author = "vnpy"

    # 定義參數
    # ---- 公式来源（武器库）----
    json_path = "../tdx_formula/formulas"      # 公式来源: 目录 / formulas.json / 单个 .tdx
    formula_name = "布林带移动止损系统"         # 公式名（.tdx 文件名，即武器名）
    formula_params = ""                        # 公式参数覆盖，JSON 字符串
    min_bars = 200                             # 指标预热最少 K 线数
    max_bars = 2000                            # K 线缓存上限（环形缓冲）

    # ---- 风控参数（trailing_* 仅供 GUI 展示，实际值以公式 TRAIL_* 为准）----
    trailing_long = 0.65
    trailing_short = 0.65
    atr_window = 4
    risk_level = 300

    # 定義變數
    boll_up = 0
    boll_down = 0
    trading_size = 0
    long_stop = 0
    short_stop = 0
    atr_value = 0

    parameters = [
        "json_path",
        "formula_name",
        "formula_params",
        "min_bars",
        "max_bars",
        "trailing_long",
        "trailing_short",
        "atr_window",
        "risk_level"
    ]
    variables = [
        "boll_up",
        "boll_down",
        "trading_size",
        "long_stop",
        "short_stop",
        "atr_value"
    ]

    def __init__(
        self,
        cta_engine,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ):
        """"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.bg = BarGenerator(self.on_bar, 15, self.on_15min_bar)
        self.am = ArrayManager()

        # 加载通达信公式（武器库核心）
        self._load_formula()

    def _load_formula(self):
        """加载通达信公式（武器库核心）"""
        formula_path = _resolve_formula_path(self.json_path)
        TdxEngine = _import_tdx_engine(
            extra_hints=[str(Path(formula_path).parent)]
        )
        engine = TdxEngine(formula_path)
        self.formula = engine.get(self.formula_name)

        self.formula_params_dict: dict = (
            json.loads(self.formula_params) if self.formula_params else {}
        )
        self.opens = deque(maxlen=self.max_bars)
        self.highs = deque(maxlen=self.max_bars)
        self.lows = deque(maxlen=self.max_bars)
        self.closes = deque(maxlen=self.max_bars)
        self.volumes = deque(maxlen=self.max_bars)

        self.write_log(
            f"公式 [{self.formula.name}] 加载成功，方向={self.formula.direction}，"
            f"参数={[p.name for p in self.formula.params.values()]}"
        )

    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

        # 公式模式需加载足够历史数据预热指标缓存
        days = max(self.min_bars * 2, 200)
        self.load_bar(days)

    def on_start(self):
        """
        Callback when strategy is started.
        """
        self.write_log("策略启动")

    def on_stop(self):
        """
        Callback when strategy is stopped.
        """
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """
        Callback of new tick data update.
        """
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData):
        """
        Callback of new bar data update.
        """
        self.bg.update_bar(bar)

    def on_15min_bar(self, bar: BarData):
        """"""
        self.cancel_all()

        self.am.update_bar(bar)

        self._process_formula_bar(bar)

        self.put_event()

    # ------------------------------------------------------------------
    # 公式信号源（武器库主流程）
    # ------------------------------------------------------------------
    def _process_formula_bar(self, bar: BarData):
        """写入缓存 -> 运行公式 -> 读取数值列 -> 全部停止单下单"""
        self.opens.append(bar.open_price)
        self.highs.append(bar.high_price)
        self.lows.append(bar.low_price)
        self.closes.append(bar.close_price)
        self.volumes.append(bar.volume)

        if not self.inited or len(self.closes) < self.min_bars:
            return

        # 运行通达信公式，提取最新一根 K 线的数值列
        df = pd.DataFrame({
            "open": list(self.opens),
            "high": list(self.highs),
            "low": list(self.lows),
            "close": list(self.closes),
            "volume": list(self.volumes),
        })
        out = self.formula.run(df, **self.formula_params_dict)

        # 读取公式输出的布林带与移动止损数值（替代原版 am.boll + 策略侧状态机）
        self.boll_up = float(out["UPPER"].iloc[-1])
        self.boll_down = float(out["LOWER"].iloc[-1])
        self.long_stop = float(out["LONGSTOP"].iloc[-1])
        self.short_stop = float(out["SHORTSTOP"].iloc[-1])

        # 与原版 on_15min_bar 逐行一致的分支（全部停止单）
        if not self.pos:
            self.atr_value = self.am.atr(self.atr_window)
            self.trading_size = int(self.risk_level / self.atr_value)

            self.buy(self.boll_up, self.trading_size, stop=True)
            self.short(self.boll_down, self.trading_size, stop=True)

        elif self.pos > 0:
            self.sell(self.long_stop, abs(self.pos), stop=True)

        else:
            self.cover(self.short_stop, abs(self.pos), stop=True)

    def on_trade(self, trade: TradeData):
        """
        Callback of new trade data update.
        """
        self.put_event()

    def on_order(self, order: OrderData):
        """
        Callback of new order data update.
        """
        pass

    def on_stop_order(self, stop_order: StopOrder):
        """
        Callback of stop order update.
        """
        pass
