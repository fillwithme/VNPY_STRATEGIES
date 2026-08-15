import numpy as np

from vnpy_portfoliostrategy import (
    StrategyTemplate,
    ArrayManager,
    BarData,
    Direction
)


def calculate_score(data: np.ndarray) -> float:
    """计算强弱得分（一元线性回归：斜率 * R²，数值上等价于 sklearn 实现）

    用 numpy polyfit 替代 sklearn.LinearRegression：25 点回归用 sklearn
    会带来巨大对象创建/拟合开销，分钟级回测下慢 20~50 倍。
    """
    # 执行回归
    x: np.ndarray = np.arange(1, len(data) + 1, dtype=float)
    y: np.ndarray = data / data[0]
    slope, intercept = np.polyfit(x, y, 1)

    # R² = 1 - SS_res / SS_tot
    y_pred: np.ndarray = slope * x + intercept
    ss_res: float = float(np.sum((y - y_pred) ** 2))
    ss_tot: float = float(np.sum((y - np.mean(y)) ** 2))
    r2: float = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return slope * r2


class EtfRotationStrategy(StrategyTemplate):
    """ETF轮动策略"""

    author: str = "CZL"

    regression_window: int = 25         # 线性回归窗口
    holding_size = 3                    # 最大持仓数
    min_score: float = 0.0              # 空仓阈值：得分 <= 该值的ETF不持有（0=动量转负即空仓）
    fixed_capital: int = 1_000_000      # 固定持仓市值

    parameters = [
        "regression_window",
        "holding_size",
        "min_score",
    ]

    def on_init(self) -> None:
        """策略初始化"""
        # 确保缓存数据足够回归计算
        size: int = self.regression_window + 1

        # 创建每个合约的时序数据容器
        self.ams: dict[str, ArrayManager] = {}

        for vt_symbol in self.vt_symbols:
            self.ams[vt_symbol] = ArrayManager(size)

        # 创建持仓合约名称的列表
        self.holding_symbols = []

        self.write_log("策略初始化")

    def on_start(self) -> None:
        """策略启动"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止"""
        self.write_log("策略停止")

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K线切片推送"""
        # 更新K线到时序容器
        for vt_symbol, bar in bars.items():
            am: ArrayManager = self.ams[vt_symbol]
            am.update_bar(bar)

        # 计算每只ETF的分数
        score_data: dict[str, float] = {}

        for vt_symbol, bar in bars.items():
            am: ArrayManager = self.ams[vt_symbol]
            if not am.inited:
                return

            data: np.array = am.close[-self.regression_window:]
            score_data[vt_symbol] = calculate_score(data)

        # 重置所有合约目标
        for vt_symbol in self.vt_symbols:
            self.set_target(vt_symbol, 0)

        # 选出得分领先的ETF（按得分降序）
        top_ranked = sorted(score_data,
                            key=lambda x: score_data[x],
                            reverse=True)

        # 空仓规则：得分 <= min_score（默认0，即动量转负）的ETF排除，规避系统性下跌；
        # 若所有ETF得分均不达标，则 selected 为空 -> 全部空仓
        selected = [s for s in top_ranked if score_data[s] > self.min_score][:self.holding_size]

        # 如果目标持仓集合发生变化，才触发调仓
        if set(selected) != set(self.holding_symbols):
            # 每只固定 1/holding_size 份额：正动量股票不足时自动留出现金（降仓），
            # 而非按实际数量均分（后者会在正动量稀少时反向集中资金）
            per_capital: float = self.fixed_capital / self.holding_size
            for security in selected:
                price: float = bars[security].close_price
                volume: int = 100 * int(per_capital / (price * 100))
                self.set_target(security, volume)
            self.holding_symbols = selected
            # 根据设置好的目标仓位进行交易
            self.rebalance_portfolio(bars)

        # 推送UI更新
        self.put_event()

    def calculate_price(self, vt_symbol, direction, reference):
        if direction == Direction.LONG:
            price: float = round(reference * 1.1, 3)
        else:
            price = round(reference * 0.90, 3)

        return price
