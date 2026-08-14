"""
兼容层：为从旧版 vnpy_stock 移植的缠论组件补齐缺失依赖。

新版 vnpy 4.4.0 中以下内容已不存在，这里从旧版 constant.py / utility.py 原样移植：
- ChanSignals 枚举（缠论信号值）
- get_underlying_symbol（合约短号）
- get_trading_date（交易日）
"""
from enum import Enum
from functools import lru_cache
import re
from datetime import datetime, timedelta

from vnpy.trader.constant import Interval as _Interval

# 新版 vnpy 4.4.0 移除了 Interval.SECOND（秒线周期），
# 但旧版缠论组件 cta_line_bar.py 仍引用它，这里动态补回以兼容。
# 官方 Interval 为单例，patch 后 cta_line_bar 中的引用同样生效。
if not hasattr(_Interval, 'SECOND'):
    _second_member = object.__new__(_Interval)
    _second_member._name_ = 'SECOND'
    _second_member._value_ = 's'
    _Interval._member_map_['SECOND'] = _second_member
    _Interval._value2member_map_['s'] = _second_member


class Color(Enum):
    """ Kline color """
    RED = 'Red'
    BLUE = 'Blue'
    EQUAL = 'Equal'


class ChanSignals(Enum):
    """
    缠论信号
    来源：https://github.com/zengbin93/czsc
    """
    Other = "Other~其他"
    Y = "Y~是"
    N = "N~否"

    INB = "INB~向下笔买点区间"
    INS = "INS~向上笔卖点区间"

    FXB = "FXB~向下笔结束分型左侧高点升破"
    FXS = "FXS~向上笔结束分型左侧低点跌破"

    BU0 = "BU0~向上笔顶分完成"
    BU1 = "BU1~向上笔走势延伸"

    BD0 = "BD0~向下笔底分完成"
    BD1 = "BD1~向下笔走势延伸"

    # TK = Triple K
    TK1 = "TK1~三K底分"
    TK2 = "TK2~三K上涨"
    TK3 = "TK3~三K顶分"
    TK4 = "TK4~三K下跌"

    LA0 = "LA0~底背驰"
    LQ0 = "LQ0~趋势底背驰"    # 主要针对连续不重叠的5笔
    LB0 = "LB0~双重底背驰"
    LG0 = "LG0~上颈线突破"
    LH0 = "LH0~向上中枢完成"
    LI0 = "LI0~类三买"
    LJ0 = "LJ0~向上三角扩张中枢"
    LK0 = "LK0~向上三角收敛中枢"
    LL0 = "LL0~向上平台型中枢"

    SA0 = "SA0~顶背驰"
    SQ0 = "SQ0~趋势顶背驰"    # 主要针对连续不重叠的5笔
    SB0 = "SB0~双重顶背驰"
    SG0 = "SG0~下颈线突破"
    SH0 = "SH0~向下中枢完成"
    SI0 = "SI0~类三卖"
    SJ0 = "SJ0~向下三角扩张中枢"
    SK0 = "SK0~向下三角收敛中枢"
    SL0 = "SL0~向下平台型中枢"

    # 三笔形态信号
    X3LA0 = "X3LA0~向下不重合"
    X3LB0 = "X3LB0~向下奔走型"
    X3LC0 = "X3LC0~向下收敛"
    X3LD0 = "X3LD0~向下扩张"
    X3LE0 = "X3LE0~向下盘背"
    X3LF0 = "X3LF0~向下无背"

    X3SA0 = "X3SA0~向上不重合"
    X3SB0 = "X3SB0~向上奔走型"
    X3SC0 = "X3SC0~向上收敛"
    X3SD0 = "X3SD0~向上扩张"
    X3SE0 = "X3SE0~向上盘背"
    X3SF0 = "X3SF0~向上无背"

    # 趋势类买卖点(9~13笔分析结果）
    Q1L0 = "Q1L0~趋势类一买"
    Q2L0 = "Q2L0~趋势类二买"
    Q3L0 = "Q3L0~趋势类三买"

    Q1S0 = "Q1S0~趋势类一卖"
    Q2S0 = "Q2S0~趋势类二卖"
    Q3S0 = "Q3S0~趋势类三卖"


@lru_cache()
def get_underlying_symbol(symbol: str):
    """
    取得合约的短号.  rb2005 => rb
    :param symbol:
    :return: 短号
    """
    # 套利合约
    if symbol.find(' ') != -1:
        # 排除SP SPC SPD
        s = symbol.split(' ')
        if len(s) < 2:
            return symbol
        symbol = s[1]

        # 只提取leg1合约
        if symbol.find('&') != -1:
            s = symbol.split('&')
            if len(s) < 2:
                return symbol
            symbol = s[0]

    p = re.compile(r"([A-Z]+)[0-9]+", re.I)
    underlying_symbol = p.match(symbol)

    if underlying_symbol is None:
        return symbol

    return underlying_symbol.group(1)


def get_trading_date(dt: datetime = None):
    """
    根据输入的时间，返回交易日的日期
    :param dt:
    :return:
    """
    if dt is None:
        dt = datetime.now()

    if dt.isoweekday() in [6, 7]:
        # 星期六,星期天=>星期一
        return (dt + timedelta(days=8 - dt.isoweekday())).strftime('%Y-%m-%d')

    if dt.hour >= 20:
        if dt.isoweekday() == 5:
            # 星期五=》星期一
            return (dt + timedelta(days=3)).strftime('%Y-%m-%d')
        else:
            # 第二天
            return (dt + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        return dt.strftime('%Y-%m-%d')
