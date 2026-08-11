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
    波动率定仓: trading_size = int(risk_level / ATR(atr_window))，并按 lot_size 整手对齐（不足一手归零不下单）

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

# ============================================================================
# 第 1 部分：导入依赖库
# ============================================================================

# json：解析 formula_params 字符串（JSON 格式的参数覆盖串）
import json

# sys：管理 Python 搜索路径，用于定位 tdx_engine 模块
import sys

# deque：双端队列，用作 K 线数据的环形缓存（自动丢弃最旧数据）
from collections import deque

# Path：跨平台路径操作（拼接/判断/规范化）
from pathlib import Path

# pandas：把 K 线缓存组装成 DataFrame 喂给公式引擎
import pandas as pd

# vnpy 基础对象：BarData(K线)、OrderData(委托)、TickData(行情)、TradeData(成交)
from vnpy.trader.object import BarData, OrderData, TickData, TradeData

# vnpy CTA 策略框架：CtaTemplate(策略基类)、BarGenerator(K线合成)、ArrayManager(指标缓存)
from vnpy_ctastrategy import CtaTemplate, BarGenerator, ArrayManager

# 停止单对象（vnpy CTA 模块内置的本地停止单，策略回调里接收状态）
from vnpy_ctastrategy.base import StopOrder


# ============================================================================
# 第 2 部分：工具函数（与策略文件解耦，便于随目录整体迁移）
# ============================================================================

# ----------------------------------------------------------------------
# 工具函数 1：定位并导入 tdx_engine（通达信公式引擎）
# ----------------------------------------------------------------------
def _import_tdx_engine(extra_hints: list[str] | None = None):
    """按优先级定位 tdx_engine 模块并返回 TdxEngine 类

    查找顺序：
        1. 当前 Python 环境（若 examples/tdx_formula 已被加入 sys.path）
        2. 向上遍历目录，定位 <项目根>/examples/tdx_formula 或策略目录内 tdx_engine.py
        3. extra_hints 提示路径（如公式目录所在目录，跨盘放置时兜底）
    """
    # 第 1 级：直接尝试从当前环境导入（说明 tdx_formula 已在 sys.path 中）
    try:
        from tdx_engine import TdxEngine
        return TdxEngine
    except ImportError:
        pass  # 导入失败不报错，继续向下查找

    # 第 2 级：从"本文件所在目录"开始，逐级向上找 <项目根>/examples/tdx_formula
    here = Path(__file__).resolve().parent  # 本策略文件所在目录（绝对路径）
    for parent in here.parents:            # 遍历每一级父目录
        pkg_dir = parent / "examples" / "tdx_formula"  # 候选：父目录下的 examples/tdx_formula
        if pkg_dir.exists() and (pkg_dir / "tdx_engine.py").exists():
            sys.path.insert(0, str(pkg_dir))           # 命中则把该目录加入 sys.path
            from tdx_engine import TdxEngine
            return TdxEngine
        if (parent / "tdx_engine.py").exists():        # 候选：父目录下直接有 tdx_engine.py
            sys.path.insert(0, str(parent))
            from tdx_engine import TdxEngine
            return TdxEngine

    # 第 3 级：用调用方传入的提示路径兜底（如公式目录所在的目录）
    for hint in (extra_hints or []):
        hp = Path(hint)
        if (hp / "tdx_engine.py").exists():            # 提示目录下存在 tdx_engine.py
            sys.path.insert(0, str(hp))
            from tdx_engine import TdxEngine
            return TdxEngine

    # 全部失败：抛出带说明的异常，方便使用者快速定位问题
    raise ImportError(
        "无法定位 tdx_engine.py，请将 examples/tdx_formula 加入 sys.path，"
        "或把本策略文件与 tdx_engine.py 放在同一目录。"
    )


# ----------------------------------------------------------------------
# 工具函数 2：把 json_path 参数归一化为绝对路径
# ----------------------------------------------------------------------
def _resolve_formula_path(raw: str) -> str:
    """把 json_path 参数归一化为绝对路径

    - 绝对路径 / ~ 家目录：直接规范化
    - 相对路径：依次尝试「当前工作目录 / 本策略文件目录及各级父目录」为基准
    - 全部未命中时回退为 cwd/raw 的绝对形式（由 TdxEngine 抛错提示）
    """
    if not raw:  # 空字符串直接拒绝
        raise ValueError("json_path 不能为空")

    # 先展开 ~（用户主目录），再判断是否为绝对路径
    p = Path(raw).expanduser()
    if p.is_absolute():  # 绝对路径无需解析，直接返回
        return str(p)

    # 相对路径：构建候选基准目录列表
    here = Path(__file__).resolve().parent   # 本策略文件所在目录
    candidates = [Path.cwd()]                # 候选 1：当前工作目录
    for base in [here, *here.parents]:       # 候选 2：本文件目录及其各级父目录
        candidates.append(base)
        if (base / "examples" / "tdx_formula").exists():
            break                            # 找到项目根就停止向上，避免遍历过头

    seen: set[Path] = set()  # 去重集合，避免重复检查同一目录
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        cand = base / raw    # 拼接候选绝对路径
        if cand.exists():    # 命中即返回规范化后的绝对路径
            return str(cand.resolve())

    # 全部未命中：回退为 cwd/raw 的绝对形式（后续由 TdxEngine 抛错提示）
    return str((Path.cwd() / raw).resolve())


# ============================================================================
# 第 3 部分：策略类 CtaGenericCincoStrategy（继承 vnpy CTA 策略基类）
# ============================================================================
class CtaGenericCincoStrategy(CtaTemplate):
    """布林带移动止损系统：公式驱动 + 全部停止单下单"""

    author = "vnpy"  # 策略作者（vnpy GUI 展示用）

    # ========================================================================
    # 3.1 策略参数（parameters 列表中的项，GUI 界面里可调）
    # ========================================================================
    # 定義參數
    # ---- 公式来源（武器库）----
    json_path = "../tdx_formula/formulas"      # 公式来源: 目录 / formulas.json / 单个 .tdx
    formula_name = "布林带移动止损系统"         # 公式名（.tdx 文件名，即武器名）
    formula_params = ""                        # 公式参数覆盖，JSON 字符串
    min_bars = 200                             # 指标预热最少 K 线数
    max_bars = 2000                            # K 线缓存上限（环形缓冲）

    # ---- 风控参数（trailing_* 仅供 GUI 展示，实际值以公式 TRAIL_* 为准）----
    trailing_long = 0.65                       # 多单移动止损倍数（GUI 展示用）
    trailing_short = 0.65                      # 空单移动止损倍数（GUI 展示用）
    atr_window = 4                             # 波动率定仓用的 ATR 周期
    risk_level = 300                           # 风险预算（单笔冒的风险金额）
    lot_size = 1                               # 每手股数（港股腾讯0700=100；美股无手数概念填1）

    # ========================================================================
    # 3.2 策略变量（variables 列表中的项，GUI 界面实时显示）
    # ========================================================================
    # 定義變數
    boll_up = 0        # 布林带上轨（公式输出，最新一根 bar 的值）
    boll_down = 0      # 布林带下轨（公式输出，最新一根 bar 的值）
    trading_size = 0   # 本次下单股数（已按 lot_size 整手对齐，不足一手为 0 不下单）
    long_stop = 0      # 多单移动止损价（公式输出）
    short_stop = 0     # 空单移动止损价（公式输出）
    atr_value = 0      # 最新 ATR 值（用于定仓）

    # ---- 可在 GUI 中修改的参数白名单（与上面参数定义一一对应）----
    parameters = [
        "json_path",        # 公式来源路径
        "formula_name",     # 公式名（武器名）
        "formula_params",   # 公式参数覆盖串
        "min_bars",         # 预热最少 K 线数
        "max_bars",         # 缓存上限
        "trailing_long",    # 多单止损倍数（展示用）
        "trailing_short",   # 空单止损倍数（展示用）
        "atr_window",       # ATR 周期
        "risk_level",       # 风险预算
        "lot_size"          # 每手股数（整手对齐）
    ]

    # ---- 可在 GUI 中实时查看的变量白名单 ----
    variables = [
        "boll_up",       # 布林带上轨
        "boll_down",     # 布林带下轨
        "trading_size",  # 本次下单股数（已按 lot_size 整手对齐）
        "long_stop",     # 多单止损价
        "short_stop",    # 空单止损价
        "atr_value"      # ATR 值
    ]

    # ========================================================================
    # 3.3 构造函数：初始化对象
    # ========================================================================
    def __init__(
        self,
        cta_engine,               # 策略引擎（由 vnpy 框架自动传入）
        strategy_name: str,       # 策略实例名（如 "布林带1号"）
        vt_symbol: str,           # 交易合约代码（如 "700-HKD-STK.SEHK"）
        setting: dict,            # 策略参数字典（来自回测配置或 GUI）
    ):
        """"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        # K线合成器：把 1 分钟 bar 合成为 15 分钟 bar（15 表示每 15 根合成 1 根）
        self.bg = BarGenerator(self.on_bar, 15, self.on_15min_bar)

        # 指标管理器：维护最近 N 根 bar 的 OHLCV 数组，供 ATR 等指标计算
        self.am = ArrayManager()

        # 加载通达信公式（武器库核心）：解析公式、缓存参数等
        self._load_formula()

    # ========================================================================
    # 3.4 私有方法：加载通达信公式
    # ========================================================================
    def _load_formula(self):
        """加载通达信公式（武器库核心）"""
        # 把配置里的 json_path 归一化为绝对路径
        formula_path = _resolve_formula_path(self.json_path)

        # 定位并导入 TdxEngine 类（extra_hints 传公式目录，兜底用）
        TdxEngine = _import_tdx_engine(
            extra_hints=[str(Path(formula_path).parent)]
        )

        # 创建引擎实例，指向公式目录 / formulas.json / 单个 .tdx
        engine = TdxEngine(formula_path)

        # 按名称取出指定公式（返回公式对象，含参数、输出列等信息）
        self.formula = engine.get(self.formula_name)

        # 解析 formula_params 字符串 -> 参数字典（空字符串则用公式默认参数）
        self.formula_params_dict: dict = (
            json.loads(self.formula_params) if self.formula_params else {}
        )

        # 初始化 5 个环形缓存，分别存 open/high/low/close/volume（最多 max_bars 根）
        self.opens = deque(maxlen=self.max_bars)
        self.highs = deque(maxlen=self.max_bars)
        self.lows = deque(maxlen=self.max_bars)
        self.closes = deque(maxlen=self.max_bars)
        self.volumes = deque(maxlen=self.max_bars)

        # 写入策略日志：公式加载成功 + 方向 + 可用参数名（方便排查配置问题）
        self.write_log(
            f"公式 [{self.formula.name}] 加载成功，方向={self.formula.direction}，"
            f"参数={[p.name for p in self.formula.params.values()]}"
        )

    # ========================================================================
    # 3.5 vnpy 策略生命周期回调（框架自动调用）
    # ========================================================================
    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

        # 公式模式需加载足够历史数据预热指标缓存
        # 取 min_bars*2 与 200 中的较大值（天），保证有足够的 K 线供 MA/STD 预热
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
        # 实时行情下走 tick 路径：交给 K 线合成器攒成 1 分钟 bar
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData):
        """
        Callback of new bar data update.
        """
        # 1 分钟 bar 到达：交给合成器，攒够 15 根后触发 on_15min_bar
        self.bg.update_bar(bar)

    def on_15min_bar(self, bar: BarData):
        """"""
        # 每根 15 分钟 bar 开始：先撤掉上一根挂的所有未成交委托（避免堆积）
        self.cancel_all()

        # 更新指标缓存（ArrayManager 内部维护 OHLCV 数组，供 ATR 计算）
        self.am.update_bar(bar)

        # 核心逻辑：运行公式 + 读取数值列 + 停止单下单
        self._process_formula_bar(bar)

        # 推送事件给 GUI，刷新界面上的变量显示
        self.put_event()

    # ========================================================================
    # 3.6 公式信号源（武器库主流程）
    # ========================================================================

    def _process_formula_bar(self, bar: BarData):
        """写入缓存 -> 运行公式 -> 读取数值列 -> 全部停止单下单"""
        # ---- 步骤 1：把当前 bar 的 OHLCV 写入环形缓存 ----
        self.opens.append(bar.open_price)    # 开盘价
        self.highs.append(bar.high_price)    # 最高价
        self.lows.append(bar.low_price)      # 最低价
        self.closes.append(bar.close_price)  # 收盘价
        self.volumes.append(bar.volume)      # 成交量

        # ---- 步骤 2：预热判断（未初始化或数据不足时不交易）----
        if not self.inited or len(self.closes) < self.min_bars:
            return  # 还没攒够 200 根，直接跳过，等下一根

        # ---- 步骤 3：把缓存组装成 DataFrame 喂给公式引擎 ----
        df = pd.DataFrame({
            "open": list(self.opens),    # 开盘价序列
            "high": list(self.highs),    # 最高价序列
            "low": list(self.lows),      # 最低价序列
            "close": list(self.closes),  # 收盘价序列
            "volume": list(self.volumes),  # 成交量序列
        })

        # 运行公式（传入参数覆盖），返回所有输出列（UPPER/LOWER/LONGSTOP 等）
        out = self.formula.run(df, **self.formula_params_dict)

        # ---- 步骤 4：读取公式输出的数值列（取最后一根 bar 的值）----
        # 读取公式输出的布林带与移动止损数值（替代原版 am.boll + 策略侧状态机）
        self.boll_up = float(out["UPPER"].iloc[-1])       # 布林带上轨
        self.boll_down = float(out["LOWER"].iloc[-1])     # 布林带下轨
        self.long_stop = float(out["LONGSTOP"].iloc[-1])  # 多单移动止损价
        self.short_stop = float(out["SHORTSTOP"].iloc[-1])  # 空单移动止损价

        # ---- 步骤 5：按持仓状态分支，全部用停止单下单 ----
        # 与原版 on_15min_bar 逐行一致的分支（全部停止单）
        if not self.pos:  # 无持仓：双挂布林带上/下轨的停止单
            self.atr_value = self.am.atr(self.atr_window)  # 计算 ATR（波动率）
            # 波动率定仓：风险预算 / 当前波动率 = 期望股数
            raw_size = int(self.risk_level / self.atr_value)
            # 整手对齐：按下单量/lot_size 向下取整到其整数倍，不足一手归零不下单（与海龟一致）
            lot = max(int(self.lot_size), 1)
            self.trading_size = int(raw_size / lot) * lot

            # 不足一手不下单，跳过本次双挂停止单
            if not self.trading_size:
                return

            # 挂多单停止单：价格向上突破上轨则买入（stop=True 表示停止单）
            self.buy(self.boll_up, self.trading_size, stop=True)
            # 挂空单停止单：价格向下跌破下轨则卖出（开空）
            self.short(self.boll_down, self.trading_size, stop=True)

        elif self.pos > 0:  # 持有多单：挂移动止损卖出单
            self.sell(self.long_stop, abs(self.pos), stop=True)

        else:  # 持有空单：挂移动止损买入单（平空）
            self.cover(self.short_stop, abs(self.pos), stop=True)

    # ========================================================================
    # 3.7 其它 vnpy 回调（成交/委托/停止单状态更新）
    # ========================================================================
    def on_trade(self, trade: TradeData):
        """
        Callback of new trade data update.
        """
        # 每次成交后刷新 GUI 显示（持仓、盈亏等由框架自动更新）
        self.put_event()

    def on_order(self, order: OrderData):
        """
        Callback of new order data update.
        """
        pass  # 普通委托更新暂不处理

    def on_stop_order(self, stop_order: StopOrder):
        """
        Callback of stop order update.
        """
        pass  # 停止单状态更新暂不处理
