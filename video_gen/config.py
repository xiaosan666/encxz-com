"""全局配置：路径、画布规格、配色、模版几何尺寸。

所有几何数值都是从示例视频逐帧量测出来的（1920x1080 坐标系）。
"""

from pathlib import Path

# ---------------------------------------------------------------- 路径
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
XLSX_PATH = DATA_DIR / "en.xlsx"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache"
ASSETS_DIR = ROOT / "assets"
MODELS_DIR = ROOT / "models"      # whisper 等本地模型缓存

# ---------------------------------------------------------------- 画布
W, H = 1920, 1080
FPS = 30

# ---------------------------------------------------------------- 配色（取自示例视频采样）
BG = (248, 247, 242)          # 纸张底色 #F8F7F2
MAROON = (155, 18, 28)        # 酒红 #9B121C：logo、中文标题、高亮
INK = (12, 12, 10)            # 正文黑
GRAY = (121, 120, 115)        # 第二遍未激活歌词 #797873
PAGE_GRAY = (198, 197, 192)   # 页码 #C6C5C0
RULE_DARK = (28, 28, 26)      # 页眉分隔线

# ---------------------------------------------------------------- 字体
FONT_CANDIDATES = {
    "en_reg": [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
    ],
    "en_bold": [
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    ],
    "cn_reg": [
        ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
         "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc", 3),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/System/Library/Fonts/STHeiti Light.ttc", 1),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
    ],
    "cn_bold": [
        ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
         "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc", 7),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
    ],
}

# ---------------------------------------------------------------- 页眉模版几何
LOGO_CIRCLE = (270, 133, 53)      # cx, cy, r
LOGO_CIRCLE_W = 6                 # 圆环线宽
LOGO_TEXT_X = 353                 # 英语初学者 / encxz.com 左边界
LOGO_LINE1_BASELINE = 128         # 英语初学者
LOGO_LINE2_BASELINE = 188         # encxz.com
LOGO_LINE1_SIZE = 46
LOGO_LINE2_SIZE = 45
LOGO_EN_SIZE = 34                 # 圆环里的 EN

RULE_X0, RULE_X1 = 228, 1692
RULE_THICK_Y = 233                # 粗线（5px 高）
RULE_THICK_H = 5
RULE_HAIRLINES = (246, 250, 255, 259, 264)   # 细装饰线

LEVEL_X_RIGHT = 1568              # 级别：A1 右对齐位置
LEVEL_BASELINE = 153
LEVEL_SIZE = 50

HEADER_TITLE_BASELINE = 144       # 页眉中间的英文标题
HEADER_TITLE_SIZE = 36
HEADER_TITLE_MIN_SIZE = 22        # 过长标题自动缩到的最小字号
HEADER_TITLE_MAX_WIDTH = 690      # 夹在 logo 与等级之间的可用宽度（两端各留 ~40px）

# ---------------------------------------------------------------- 封面页
COVER_TITLE_CENTER = 430          # 英文标题（居中，块中心）
COVER_TITLE_SIZE = 121
COVER_CN_CENTER = 634             # 中文标题
COVER_CN_SIZE = 72
COVER_TAG_CENTER = 796            # English Listening Practice
COVER_TAG_SIZE = 40
COVER_MAX_WIDTH = 1500
COVER_TITLE_RATIO = 1.16
COVER_CN_RATIO = 1.30

# ---------------------------------------------------------------- 第一遍
P1_EN_CENTER_Y = 471              # 英文文本块垂直中心
P1_EN_SIZE = 90
P1_EN_MIN_SIZE = 46
P1_EN_MAX_WIDTH = 1400
P1_EN_MAX_LINES = 3
P1_EN_LINE_RATIO = 1.32           # 行距 = 字号 * 该系数

P1_CN_BASELINE = 876              # 中文副标题（居中）
P1_CN_SIZE = 49
P1_CN_MIN_SIZE = 34
P1_CN_MAX_WIDTH = 1340
P1_CN_MAX_LINES = 2

P1_COUNTER_X_RIGHT = 1698         # 页码 04/09
P1_COUNTER_BASELINE = 1048

# ---------------------------------------------------------------- 第二遍（滚动歌词）
P2_TEXT_X = 308                   # 歌词左边界
P2_VIEW_TOP = 292                 # 歌词可视区上边界
P2_VIEW_BOTTOM = 966              # 歌词可视区下边界
P2_FADE_TOP = 36                  # 顶部渐隐高度
P2_FADE_BOTTOM = 64               # 底部渐隐高度
P2_LINE_STEP = 80                 # 行距
P2_PARA_EXTRA = 27                # 段落之间额外间距
P2_BASE_SIZE = 47                 # 未激活字号
P2_ACTIVE_SIZE = 61               # 激活字号（放大）
P2_LINE_MAX_WIDTH = 1304
P2_PIN_BASELINE = 650             # 当前句基线在屏幕上的固定位置
P2_SCROLL_ANIM = 0.45             # 滚动动画时长（秒）

# ---------------------------------------------------------------- 时间轴
COVER_SEC = 2.0                   # 封面停留
GAP_SEC = 2.0                     # 第一遍与第二遍之间的静默
TAIL_SEC = 0.6                    # 片尾留白

# ---------------------------------------------------------------- 音频响度归一化
# 源 mp3 的响度差异很大（实测 -46.1 ~ -9.0 LUFS，中位数 -22.5），编码时若不做补偿，
# 视频就会原样继承源的音量，同一批视频听起来参差不齐。这里统一补一道静态增益：
# 先量源音频的 EBU R128 整体响度，再施加「目标响度 - 实测响度」的线性增益。
AUDIO_NORMALIZE = True            # False = 关闭归一化，完全按源音量输出
AUDIO_TARGET_LUFS = -21.5         # 目标整体响度（对齐现有 A1 视频的响度中心）
AUDIO_TRUE_PEAK_DBTP = -1.5       # 真峰值上限：增益后若超此值就以峰值优先，宁可略轻也不削波

# ---------------------------------------------------------------- 对齐（静音检测）
SILENCE_NOISE_DB = -35.0          # 静音判定门限
SILENCE_MIN_DUR = 0.15            # 最短静音时长（秒）
MIN_SEGMENT = 0.25                # 句段最短时长
# 切点偏离期望位置超过容差时，回退到按词数比例分配的估计值
ALIGN_TOL_MIN = 0.30              # 容差下限（秒）
ALIGN_TOL_MAX = 1.20              # 容差上限（秒）
ALIGN_TOL_FACTOR = 0.60           # 容差 = 平均句长 * 该系数
