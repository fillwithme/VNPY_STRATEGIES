# -*- coding: utf-8 -*-
"""
dual_ma_both_side_portfolio.py - 通达信公式容器 Portfolio 策略（vnpy_portfoliostrategy）

【设计理念】
本策略是一个「公式容器」：自身不写死任何交易逻辑，而是把同一个通达信
交易系统公式应用于一篮子标的，由 TdxEngine 自动解析并输出标准信号，
本策略负责把每个标的的信号翻译为「目标仓位」并通过 rebalance_portfolio 调仓。

【与 CTA 版本的区别】
    CTA 版（DualMaBothSideStrategy）作用于单一合约，下单即成交、持仓即生效；
    本 Portfolio 版管理多个合约，采用「目标仓位 + 定期调仓」模式：
        buy/cover 信号 -> 目标仓位 = +fixed_size
        short       -> 目标仓位 = -fixed_size
        sell/cover  -> 目标仓位 = 0
    vnpy 的 Portfolio 实盘引擎不推送 on_order/on_trade 回调，
    因此必须用 set_target() + rebalance_portfolio() 来管理仓位。

【信号映射】（由 TdxEngine 的 SYSTEM_OUTPUTS 统一完成）
    通达信输出名          -> 标准信号  -> 目标仓位
    ENTERLONG / 买入条件  -> buy       +fixed_size
    EXITLONG  / 卖出条件  -> sell      0
    ENTERSHORT           -> short     -fixed_size
    EXITSHORT            -> cover     0

【示例公式】双向双均线交易系统.tdx
    #param SHORT: 5, 1, 60, 短均线周期
    #param LONG: 10, 2, 120, 长均线周期
    MA5:=MA(CLOSE,SHORT);
    MA10:=MA(CLOSE,LONG);
    ENTERLONG:CROSS(MA5,MA10);   # 金叉 -> buy
    EXITLONG:CROSS(MA10,MA5);    # 死叉 -> sell
    ENTERSHORT:CROSS(MA10,MA5);  # 死叉 -> short
    EXITSHORT:CROSS(MA5,MA10);   # 金叉 -> cover

【使用方式】
    1) 在 vnpy 图形界面「组合策略」-> 添加策略 -> 选择 DualMaBothSidePortfolioStrategy
    2) 参数说明：
       json_path      : 公式来源路径，支持绝对路径 / 相对路径 / ~（详见下方说明）
       formula_name   : 公式名（.tdx 文件名或 JSON 索引中的 name 字段）
       formula_params : 公式参数覆盖，JSON 字符串，如 '{"SHORT": 24, "LONG": 120}'
       min_bars       : 至少缓存多少根 K 线后才开始计算信号（指标预热）
       max_bars       : K 线缓存上限（环形缓冲，超出自动丢弃最旧 K 线）
       fixed_size     : 每次信号触发的目标仓位手数（正数开多 / 负数开空）
       bar_interval   : 实盘初始化时加载历史 K 线的周期，d=日线 h=小时线 m=分钟线

【json_path 相对路径解析规则】
    除绝对路径（含 ~ 家目录展开）外，相对路径按下述基准依次查找：
        1. 当前工作目录 Path.cwd()
        2. 本策略文件所在目录
        3. 项目根目录（向上查找 examples/tdx_formula 所在处）
    例如从 G:\vnpy-4.4.0\examples\veighna_trader 启动时，可直接填
    "..\\tdx_formula\\formulas"。

【实现要点】
    - _BarBuffer：单标的 OHLCV 环形缓冲，按 vt_symbol 分别缓存
    - on_bars 一次性收到全部标的最新 K 线，逐标的运行公式并更新目标仓位
    - 末尾调用 rebalance_portfolio(bars) 统一按目标仓位发出调仓委托
    - 实盘 on_init 按 bar_interval 周期加载历史数据预热各标的缓存
"""
import json
import sys
from collections import deque
from pathlib import Path

import pandas as pd
from vnpy.trader.constant import Interval
from vnpy_portfoliostrategy import StrategyTemplate


# ----------------------------------------------------------------------
# 工具函数：定位并导入 tdx_engine（与本策略文件解耦，便于随目录迁移）
# ----------------------------------------------------------------------
def _import_tdx_engine(extra_hints: list[str] | None = None):
    """按优先级定位 tdx_engine 模块并返回 TdxEngine 类

    查找顺序：
        1. 当前 Python 环境（若 examples/tdx_formula 已被加入 sys.path）
        2. 向上遍历目录，定位 <项目根>/examples/tdx_formula
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


# 周期字符串 -> vnpy Interval 常量
_INTERVAL_MAP = {
    "d": Interval.DAILY,
    "h": Interval.HOUR,
    "m": Interval.MINUTE,
}


class _BarBuffer:
    """单标的 OHLCV 环形缓冲（maxlen 固定，超出自动丢弃最旧 K 线）"""

    def __init__(self, max_bars: int):
        self.opens = deque(maxlen=max_bars)
        self.highs = deque(maxlen=max_bars)
        self.lows = deque(maxlen=max_bars)
        self.closes = deque(maxlen=max_bars)
        self.volumes = deque(maxlen=max_bars)

    def push(self, bar) -> None:
        """追加一根 K 线"""
        self.opens.append(bar.open_price)
        self.highs.append(bar.high_price)
        self.lows.append(bar.low_price)
        self.closes.append(bar.close_price)
        self.volumes.append(bar.volume)

    def __len__(self) -> int:
        return len(self.closes)

    def to_df(self) -> pd.DataFrame:
        """转换为 TdxEngine 期望的 OHLCV DataFrame"""
        return pd.DataFrame({
            "open": list(self.opens),
            "high": list(self.highs),
            "low": list(self.lows),
            "close": list(self.closes),
            "volume": list(self.volumes),
        })


class DualMaBothSidePortfolioStrategy(StrategyTemplate):
    """通达信公式容器 Portfolio 策略（默认示例：双向双均线交易系统）"""

    author = "CodeBuddy"

    parameters = [
        "json_path", "formula_name", "formula_params",
        "min_bars", "max_bars", "fixed_size", "bar_interval",
    ]

    # ---- 默认参数值（图形界面加载策略后可直接修改）----
    json_path: str = "../tdx_formula/formulas"   # 公式来源：目录 / formulas.json / 单个 .tdx 文件
    formula_name: str = "双向双均线交易系统"      # 公式名（.tdx 文件名或 JSON 中 name）
    formula_params: str = ""                     # 公式参数覆盖，JSON 字符串，如 '{"SHORT": 10, "LONG": 30}'
    min_bars: int = 200                          # 至少缓存多少根 K 线后开始计算
    max_bars: int = 2000                         # K 线缓存上限（环形缓冲）
    fixed_size: int = 1                          # 每次信号触发的目标仓位手数
    bar_interval: str = "d"                      # 初始化加载历史 K 线周期：d/h/m

    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        # 1) 加载通达信公式（容器核心：任意公式都可接入）
        formula_dir = Path(_resolve_formula_path(self.json_path))
        TdxEngine = _import_tdx_engine([str(formula_dir.parent)])
        engine = TdxEngine(str(formula_dir))
        self.formula = engine.get(self.formula_name)
        # 2) 解析公式参数覆盖（JSON 字符串 -> dict，空串视为不覆盖）
        self.formula_params_dict: dict = (
            json.loads(self.formula_params) if self.formula_params else {}
        )
        # 3) 为每个标的分立一个 OHLCV 环形缓冲
        self.buffers: dict[str, _BarBuffer] = {
            vt: _BarBuffer(self.max_bars) for vt in vt_symbols
        }

        self.write_log(
            f"公式 [{self.formula.name}] 加载成功，方向={self.formula.direction}，"
            f"参数={[p.name for p in self.formula.params.values()]}"
        )

    # ------------------------------------------------------------------
    # 策略初始化：实盘启动前加载历史 K 线预热各标的缓存
    # ------------------------------------------------------------------
    def on_init(self):
        """加载 min_bars 对应周期的历史数据（load_bars 会按时间调用 on_bars）"""
        interval = _INTERVAL_MAP.get(self.bar_interval, Interval.DAILY)
        days = max(self.min_bars * 2, 200)   # 留足非交易日余量，保证缓存满
        self.load_bars(days, interval)
        self.write_log("历史数据加载完成，策略初始化结束")

    # ------------------------------------------------------------------
    # 多标的 K 线回调：实盘由 tick 合成、回测由引擎按时间切分推送
    # ------------------------------------------------------------------
    def on_bars(self, bars: dict):
        """每轮 K 线切片：逐标的运行公式 -> 更新目标仓位 -> 统一调仓"""
        for vt_symbol, bar in bars.items():
            buf = self.buffers[vt_symbol]
            buf.push(bar)

            # 缓存不足：跳过计算，等待指标预热完成
            if len(buf) < self.min_bars:
                continue

            # 运行通达信公式并提取最新信号
            out = self.formula.run(buf.to_df(), **self.formula_params_dict)
            buy = bool(out["buy"].iloc[-1]) if "buy" in out else False
            sell = bool(out["sell"].iloc[-1]) if "sell" in out else False
            short = bool(out["short"].iloc[-1]) if "short" in out else False
            cover = bool(out["cover"].iloc[-1]) if "cover" in out else False

            # 双向目标仓位映射（+开多 / 0 空仓 / -开空）
            if buy and cover:                    # 金叉：平空并开多
                target = self.fixed_size
            elif sell and short:                 # 死叉：平多并开空
                target = -self.fixed_size
            elif buy:
                target = self.fixed_size
            elif short:
                target = -self.fixed_size
            elif sell or cover:
                target = 0
            else:
                # 无新信号：保持原目标仓位（维持现有持仓方向）
                target = self.get_target(vt_symbol)

            # 目标仓位变化时才更新并记录日志
            if target != self.get_target(vt_symbol):
                self.set_target(vt_symbol, target)
                self.write_log(f"{vt_symbol} 目标仓位 -> {target}")

        # 统一按目标仓位执行调仓（内置先平后开，自动处理多空反转）
        self.rebalance_portfolio(bars)

        # 推送界面更新
        self.put_event()
