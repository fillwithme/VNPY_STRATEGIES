import os
import numpy as np
import pandas as pd

from ctypes import CDLL, cast, c_int, c_float, POINTER, ARRAY

LIBRARY_NAME = "libChanlunX"
LIBRARY_PATH = os.path.split(__file__)[0]


class ChanLibrary(object):
    """缠论DLL抽象类
    此处使用NumPy的Ctypes接口来加载缠论DLL
    并且封装了Windows和Linux加载不同DLL文件的差异
    """

    def __init__(self, bi_style=1, bi_frac_range=2, duan_style=0, debug=False):
        self._bi_style = bi_style
        self._bi_frac_range = bi_frac_range
        self._duan_style = duan_style
        self._debug = debug

    @classmethod
    def _get_library(cls):
        """全局唯一的DLL对象
        """
        lib = getattr(cls, "_lib", None)
        if lib is None:
            lib = cls._load_library()
            cls._lib = lib
        return lib

    @classmethod
    def _load_library(cls):
        ## 使用NumPy的Ctypes便捷接口加载DLL
        lib = np.ctypeslib.load_library(LIBRARY_NAME, LIBRARY_PATH)

        ## 定义多个函数接口
        lib.ChanK.argtypes = [
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            c_int,
        ]
        lib.ChanBi.argtypes = [
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            c_int,
            c_int,
            c_int,
        ]
        lib.ChanDuan.argtypes = [
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            c_int,
            c_int,
        ]
        lib.ChanZhongShu.argtypes = [
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            c_int,
        ]
        lib.ChanZhongShu2.argtypes = [
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            np.ctypeslib.ndpointer(
                dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
            c_int,
        ]
        return lib

    @classmethod
    def _free_library(cls):
        lib = getattr(cls, "_lib", None)
        if lib:
            try:
                import win32api
                win32api.FreeLibrary(lib._handle)
            except:
                pass
    # 下面的函数都是对DLL的进一步封装
    # 封装的原因是因为，这个DLL的函数是C风格的，接收的参数都是指向一个数组的指针
    # 因此这里做一下类型转换和内存空间分配

    def chanK(self, high_list, low_list):
        """导入K线"""
        if self._debug:
            print(f'导入K线')
        count = len(high_list)
        arr_direction = np.zeros(count).astype(np.float32)
        arr_include = np.zeros(count).astype(np.float32)
        arr_high = np.zeros(count).astype(np.float32)
        arr_low = np.zeros(count).astype(np.float32)

        lib = self._get_library()
        lib.ChanK(arr_direction, arr_include, arr_high, arr_low, high_list, low_list,
                  count)
        if self._debug:
            print(f'导入K线完成')
        return arr_direction, arr_include, arr_high, arr_low

    def chanBi(self, high_list, low_list):
        """计算笔"""
        if self._debug:
            print(f'计算分笔')
        count = len(high_list)
        arr_bi = np.zeros(count).astype(np.float32)

        lib = self._get_library()
        lib.ChanBi(arr_bi, high_list, low_list, count, self._bi_style,
                   self._bi_frac_range)
        if self._debug:
            print(f'计算分笔完成')
        return arr_bi

    def chanDuan(self, bi, high_list, low_list):
        """计算段"""
        if self._debug:
            print(f'计算线段开始')
        count = len(high_list)
        arr_duan = np.zeros(count).astype(np.float32)

        lib = self._get_library()
        lib.ChanDuan(arr_duan, bi, high_list, low_list, count, self._duan_style)
        if self._debug:
            print(f'计算线段完成')
        return arr_duan

    def chanZhongShu(self, duan, high_list, low_list):
        """计算中枢"""
        if self._debug:
            print(f'计算中枢开始')
        count = len(high_list)
        arr_direction = np.zeros(count).astype(np.float32)
        arr_range = np.zeros(count).astype(np.float32)
        arr_high = np.zeros(count).astype(np.float32)
        arr_low = np.zeros(count).astype(np.float32)

        lib = self._get_library()
        lib.ChanZhongShu2(
            arr_high,
            arr_low,
            arr_range,
            arr_direction,
            duan,
            high_list,
            low_list,
            count,
        )
        if self._debug:
            print(f'计算中枢完成')
        return arr_high, arr_low, arr_range, arr_direction


class ChanGraph(object):
    """缠论图
    指定缠论DLL和一段价格序列
    生成所有缠论元素
    由于这个DLL不具有增量更新的能力
    因此每次数据更新都需要输入整个缓冲区重新生成整个缠论图

    # 特别注意！
    由于ChanDLL在处理某些新元素突然形成的时候，
    其行为有一些尚未探明的地方，暂时没有时间仔细阅读源代码来了解其真实的代码逻辑
    为避免遇到意料之外的行为，同时增加策略的灵活性，
    建议所有判断“新形成分型、笔、段”的判断，
    都不要直接通过记录”分型、笔、段“的对象列表的变化来检测，
    而是直接根据最后一个Bar的价格和”分型、笔、段“列表，判断是否满足缠论的规则。
    这个做法对于某些不能最终确定新元素生成的情况也适用。
    """

    def __init__(self, chan_lib, index, high, low):
        """初始化缠论图

        :param chan_lib: 缠论DLL类
        :param index: K线索引，可以输入任意类型的索引，比如Datetime或者单纯的顺序数，通常把DataFrame的索引传入就可以了
        :param high: K线高点数组
        :param low: K线低点数组
        """
        self._chan_lib = chan_lib
        if isinstance(high, list):
            self._high = np.asarray(high, dtype=np.float32)
        else:
            self._high = high.astype(np.float32)
        if isinstance(low, list):
            self._low = np.asarray(low, dtype=np.float32)
        else:
            self._low = low.astype(np.float32)
        if isinstance(index, list):
            self._index = index
        else:
            self._index = list(index)

        self._generate(self._index, self._high, self._low)

    #@profile
    def _generate(self, index, high, low):
        """开始计算"""

        # 导入K线
        _, k_include, k_high, k_low = self._chan_lib.chanK(high, low)

        # 计算笔
        bi = self._chan_lib.chanBi(high, low)

        # 计算段
        duan = self._chan_lib.chanDuan(bi, high, low)

        # 计算段中枢
        zs_high, zs_low, zs_range, zs_dir = self._chan_lib.chanZhongShu(
            duan, high, low)

        # 计算笔中枢
        bzs_high, bzs_low, bzs_range, bzs_dir = self._chan_lib.chanZhongShu(
            bi, high, low)

        bi = list(bi.astype(int))
        duan = list(duan.astype(int))
        zs_range = list(zs_range.astype(int))
        bzs_range = list(bzs_range.astype(int))

        independent_k_idx = np.array(range(len(k_include)))
        independent_k_idx = independent_k_idx[k_include == 0]

        kline_list = []
        for i in range(len(independent_k_idx) - 1):
            a = independent_k_idx[i]
            b = independent_k_idx[i + 1]
            s = slice(a, b)
            kline = ChanK(
                index=index[s],
                high=high[s],
                low=low[s],
            )
            kline_list.append(kline)

        fenxing_list = []
        bi_list = []
        bi_map = {} # 笔映射表（结束索引号->笔对象）
        bi_start_idx = None
        bi_direction = 0
        duan_list = []
        duan_map = {} # 段映射表（结束索引号->段对象）
        duan_start_idx = None
        duan_direction = 0
        zs_list = []
        zs_start_idx = None
        zs_direction = 0
        zs_start_duan_idx = None
        bzs_list = []
        bzs_start_idx = None
        bzs_end_idx = None
        bzs_direction = 0
        if len(bi)> 5:
            for idx in range(5, len(bi)):
                if bi[idx] != 0:
                    ## 发现一个新的分型

                    ## 以当前索引为起点（包括当前索引），向左查找独立K线
                    ## 找到的第2条K线就是分型起点
                    a = independent_k_idx[independent_k_idx <= idx]
                    if len(a) == 0:
                        ## 左侧没有K线，是无效的分型
                        continue
                    elif len(a) < 2:
                        ## 左侧只有一根
                        fx_start_idx = a[-1]
                    else:
                        fx_start_idx = a[-2]

                    ## 以当前索引为起点（不包括当前索引），向右查找独立K线
                    ## 以找到的第1个K线为分型终点
                    a = independent_k_idx[independent_k_idx > idx]
                    is_last_fx = False
                    if len(a) == 0:
                        ## 末尾未成形的分型
                        fx_end_idx = len(index) - 1
                        is_last_fx = True
                    else:
                        fx_end_idx = a[0]

                    fenxing_list.append(
                        ChanFenXing(
                            direction=bi[idx],
                            index=index[fx_start_idx:fx_end_idx + 1],
                            high=high[fx_start_idx:fx_end_idx + 1],
                            low=low[fx_start_idx:fx_end_idx + 1],
                            last_fx=is_last_fx,
                        ))

                    if bi_direction == 0:
                        bi_start_idx = idx
                        bi_direction = bi[idx]
                    elif bi_direction != bi[idx]:
                        bi_obj = ChanBi(
                            direction=-bi_direction,
                            index=index[bi_start_idx:idx + 1],
                            high=high[bi_start_idx:idx + 1],
                            low=low[bi_start_idx:idx + 1],
                        )
                        bi_list.append(bi_obj)
                        bi_map[idx] = bi_obj
                        bi_direction = bi[idx]
                        bi_start_idx = idx
                    #else:
                    #    raise Exception()
        if len(duan) > 5:
            for idx in range(5, len(duan)):
                if duan[idx] != 0:
                    """发现一个线段"""
                    if duan_direction == 0:
                        # 最新段的开始位置
                        duan_start_idx = idx
                        # 最新段的方向
                        duan_direction = duan[idx]
                    elif duan_direction != duan[idx]:
                        # 新线段的方向，与旧线段不同，新增一个线段
                        duan_bi_list = [
                            bi_obj for bi_end_idx, bi_obj in bi_map.items()
                            if bi_end_idx > duan_start_idx and bi_end_idx <= idx
                        ]
                        duan_obj = ChanDuan(
                            direction=-duan_direction,
                            index=index[duan_start_idx:idx + 1],
                            high=high[duan_start_idx:idx + 1],
                            low=low[duan_start_idx:idx + 1],
                            bi_list=duan_bi_list
                        )
                        duan_list.append(duan_obj)
                        duan_map[idx] = duan_obj
                        # 更改为新的方向/开始位置
                        duan_direction = duan[idx]
                        duan_start_idx = idx
                    #else:
                    #    raise Exception()
        if len(bzs_range) > 5:
            for idx in range(5, len(bzs_range)):
                # 处理 笔中枢
                if bzs_range[idx] == 1:
                    bzs_start_idx = idx - 1
                    bzs_direction = bzs_dir[idx]
                    bzs_start_bi_idx = len(bi_list)
                elif bzs_start_idx is not None and bzs_range[idx] == 2:
                    bzs_end_idx = idx + 1
                    bzs_bi_list = [
                        bi_obj for bi_end_idx, bi_obj in bi_map.items()
                        if bi_end_idx > bzs_start_idx and bi_end_idx <= bzs_end_idx
                    ]
                    bzs_list.append(
                        ChanBiZhongShu(
                            direction=bzs_direction,
                            index=index[bzs_start_idx:bzs_end_idx + 1],
                            high=bzs_high[bzs_start_idx:bzs_end_idx + 1],
                            low=bzs_low[bzs_start_idx:bzs_end_idx + 1],
                            bi_list=bzs_bi_list
                        )
                    )

                # 段中枢
                if zs_range[idx] == 1:
                    zs_start_idx = idx - 1
                    zs_direction = zs_dir[idx]
                    zs_start_duan_idx = len(duan_list)
                elif zs_range[idx] == 2:
                    zs_end_idx = idx + 1
                    zs_duan_list = [
                        duan_obj for duan_end_idx, duan_obj in duan_map.items()
                        if zs_start_idx and duan_end_idx > zs_start_idx and duan_end_idx <= zs_end_idx
                    ]
                    zs_list.append(
                        ChanDuanZhongShu(
                            direction=zs_direction,
                            index=index[zs_start_idx:idx+1],
                            high=zs_high[zs_start_idx:idx+1],
                            low=zs_low[zs_start_idx:idx+1],
                            duan_list=zs_duan_list,
                        ))

        self._kline_list = kline_list
        self._bi_list = bi_list
        self._duan_list = duan_list
        self._bzs_list = bzs_list
        self._zs_list = zs_list
        self._fenxing_list = fenxing_list

    @property
    def kline_list(self):
        """获取K线（已识别的）"""
        return self._kline_list

    @property
    def bi_list(self):
        """获取笔列表"""
        return self._bi_list

    @property
    def duan_list(self):
        """获取段列表"""
        return self._duan_list

    @property
    def bi_zhongshu_list(self):
        """获取笔中枢"""
        return self._bzs_list

    @property
    def duan_zhongshu_list(self):
       """获取段中枢"""
       return self._zs_list

    @property
    def fenxing_list(self):
        """获取分型"""
        return self._fenxing_list

    def plot(self):
        pass


#############################
## 下面开始的类都是数据类，用于结构化地在内存中持久化数据
#############################
class ChanObject(object):
    """缠论元素
    所有的缠论元素都有：方向、高点、低点、距离当前时刻的时间距离
    """

    def __init__(self, direction, index, high, low, obj_list=None):
        self._direction = direction  # 方向
        self._index = index
        self._high = high
        self._low = low
        self._obj_list = obj_list

    @property
    def direction(self):
        return self._direction

    @property
    def high(self):
        if self._direction == 1:
            return self._high[-1] or self._high[-2]
        else:
            return self._high[0] or self._high[1]

    @property
    def low(self):
        if self._direction == 1:
            return self._low[0] or self._low[1]
        else:
            return self._low[-1] or self._low[-2]

    @property
    def start(self):
        return self._index[0]

    @property
    def end(self):
        return self._index[-1]

    @property
    def bars(self):
        return len(self._index)

    @property
    def height(self):
        """高度"""
        return self.high - self.low

    @property
    def middle(self):
        """中点"""
        return self.low + 0.5 * self.height

    @property
    def atan(self):
        """斜率(即平均每根bar的涨跌）"""
        return self.height / (self.bars-1)

    def __len__(self):
        """长度等于Bar数量减1
        减1有两个原因：
        1. 计算斜率的时候，通常计算的是单位时间段内的平均涨跌，如果有N个Bar，它们中间只有N-1个时间段
        2. 计算大型结构的长度时，只需要把内部小结构的长度相加就可以了，不会把端点重复算2次
        """
        return self.bars - 1


class ChanK(ChanObject):
    def __init__(self, index, high, low):
        super().__init__(0, index, high, low)

    @property
    def high(self):
        return np.max(self._high)

    @property
    def low(self):
        return np.min(self._low)


class ChanFenXing(ChanObject):
    """缠论分型"""
    # 顶分型定义：不含包含关系的3根K线，中间一根的高点最高，低点也最高；direction = 1
    # 底分型定义：不含包含关系的3根K线，中间一根的低点最低，高点也最低；direction = -1
    def __init__(self, direction, index, high, low, last_fx=False):
        if not last_fx:
            assert len(index) >= 3
            assert len(high) >= 3
            assert len(low) >= 3
        super().__init__(direction, index, high, low)
        self._last_fx = last_fx

    @property
    def high(self):
        if self._direction == 1:  # 顶分型
            return np.max(self._high)  # 高点等于最高一根K线的高点
        else:
            low_bar = np.argmin(self._low)  # 找到最低点所在的K线
            left = self._high[:low_bar]  # 分成左右两段
            right = self._high[low_bar + 1:]

            if len(left) == 0 or len(right) == 0:
                left_high = self._high[0]
                right_high = self._high[-1]
            else:
                left_high = np.max(left)
                right_high = np.max(right)
            return min(left_high, right_high)

    @property
    def low(self):
        if self._direction == 1:  # 顶分型
            high_bar = np.argmax(self._high)  # 找到最高点所在的K线
            left = self._low[:high_bar]  # 分成左右两段
            right = self._low[high_bar + 1:]

            if len(left) == 0 or len(right) == 0:
                left_low = self._low[0]
                right_low = self._low[-1]
            else:
                left_low = np.min(left)
                right_low = np.min(right)
            return max(left_low, right_low)
        else:
            return np.min(self._low)

    @property
    def index(self):
        if self._direction == 1:  # 顶分型
            high_bar = np.argmax(self._high)  # 找到最高点所在的K线
            return self._index[high_bar]
        else:
            low_bar = np.argmin(self._low)  # 找到最低点所在的K线
            return self._index[low_bar]

    @property
    def is_rt(self):
        """是否最后实时分型，是：不确定，实时分型， 否：确定的，bar已经确认，非实时"""
        return self._last_fx

class ChanBi(ChanObject):
    pass


class ChanDuan(ChanObject):
    """ 缠论： 段 """
    def __init__(self, direction, index, high, low, bi_list):
        super().__init__(direction, index, high, low, bi_list)

    @property
    def bi_list(self):
        """返回其包含的若干分笔"""
        return self._obj_list

    @property
    def second_high(self):
        """返回第二分笔高点"""
        if len(self.bi_list) > 1:
            return max([bi.high for bi in self.bi_list if bi.high < self.high])
        else:
            return self.low
    @property
    def last_low(self):
        """返回最后一个分笔低点"""
        if len(self.bi_list) > 1:
            return self.bi_list[-1].low
        else:
            return self.low

    @property
    def second_low(self):
        """返回第二分笔低点"""
        if len(self.bi_list) > 1:
            return min([bi.low for bi in self.bi_list if bi.low > self.low])
        else:
            return self.high

    @property
    def last_high(self):
        """返回最后一个分笔高点"""
        if len(self.bi_list) > 1:
            return self.bi_list[-1].high
        else:
            return self.high

    @property
    def momentum(self):
        """动能(当前高度/所有分笔累计高度，1 => 0.0x，数值越高，表示动能越强)"""
        if len(self.bi_list) == 1:
            return 1
        else:
            return round(self.height/sum([bi.height for bi in self.bi_list]), 2)

class ChanBiZhongShu(ChanObject):
    """笔中枢"""
    def __init__(self, direction, index, high, low, bi_list):
        super().__init__(direction, index, high, low, bi_list)

    @property
    def bi_list(self):
        """返回其中枢的若干分笔"""
        return self._obj_list

    def get_type(self):
        """
        获取中枢得分析 《缠中说禅》得形态
        :param zs:
        :return:
            1、 [balance] 平台型: 其特点是最近两个高低点基本一致，其心理含义就是平衡
            2、 [defend] 顺势平台型: 其特点是最近两个高低点依次移动，其心理含义是防守,防守者，就是中枢首分笔得方向。
            3、[attact] 奔走型:其特点是分笔波动导致重叠，其中枢高度小于任一分笔得高度，其心理含义就是进攻，进攻方向是进入中枢前得方向
            4、[enlarge]三角放大型: 指中枢前段，逐步扩大，防守者强烈反击试探。在走势类型中，放大型中枢，通常出现在中枢B，代表变盘了
            5、[close]三角收敛型: 其特点是最近两个高低点被包含
        """
        if len(self._obj_list) < 3:
            return 'attact'

        min_height = min([bi.height for bi in self._obj_list if bi.height >= self.height])

        # 收敛型
        is_close = self._obj_list[-1].height < self._obj_list[-2].height < self._obj_list[-3].height
        if is_close:
            return 'close'
        # 放大型
        is_enlarge = self._obj_list[2].height > self._obj_list[1].height > self._obj_list[0].height
        if is_enlarge:
            return 'enlarge'

        # 奔走型：仅仅位重叠分笔重叠
        if min_height * 0.8 > self.height:
            return 'attact'

        # 中枢得高度，跟分笔高低差不多。
        if self.height > (self.max_high - self.min_low) * 0.8:
            return 'balance'

        return 'defend'

    @property
    def max_high(self):
        """所有中枢内分笔的最高点"""
        return  max([bi.high for bi in self._obj_list])

    @property
    def min_low(self):
        """所有中枢内分笔的最低点"""
        return min([bi.low for bi in self._obj_list])

    @property
    def second_high(self):
        """返回第二分笔高点"""
        if len(self._obj_list) > 2:
            max_high = self.max_high
            high_s = [bi.high for bi in self._obj_list if bi.high < max_high]
            if len(high_s) > 0:
                return max(high_s)
            else:
                return self.low
        else:
            return self.low

    @property
    def second_low(self):
        """返回第二分笔低点"""
        if len(self._obj_list) > 2:
            min_low = self.min_low
            low_s = [bi.low for bi in self._obj_list if bi.low > min_low]
            if len(low_s) > 0:
                return min(low_s)
            else:
                return self.high
        else:
            return self.high

class ChanDuanZhongShu(ChanObject):
    """段 中枢"""
    def __init__(self, direction, index, high, low, duan_list):
        super().__init__(direction, index, high, low, duan_list)

    @property
    def duan_list(self):
        """返回其若干线段"""
        return self._obj_list

    @property
    def max_high(self):
        """所有中枢内线段的最高点"""
        return max([d.high for d in self._obj_list])

    @property
    def min_low(self):
        """所有中枢内线段的最低点"""
        return min([d.low for d in self._obj_list])
