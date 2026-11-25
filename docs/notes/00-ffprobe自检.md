# 00 · ffprobe 自检：读懂一个视频文件

> 参考译文。样本：`../_work/video-subtitle-remover/test/test3.mp4`
> 命令：`ffprobe -v error -print_format json -show_format -show_streams test3.mp4`

---

## 0. ffprobe 是什么

ffmpeg 的**只读**搭档：ffmpeg 负责**改写**（转码/裁剪/烧字幕），ffprobe 只**看**不动。
输出可以是 JSON / XML / CSV / 纯文本 —— 本项目统一用 JSON（程序好解析）。

**三个常用选项**
| 选项 | 作用 |
|---|---|
| `-v error` | 少打废话，只留错误 |
| `-print_format json` | 输出格式 |
| `-show_format -show_streams` | 容器信息 + 每条流信息 |

---

## 1. 顶层结构

```json
{ "streams": [ {视频流}, {音频流} ], "format": {容器信息} }
```

- `streams`：**流**。一个文件 = 容器 + 若干条流。视频流一条、音频流一条，**各有各的规格和时长**
- `format`：**容器**（mp4/mkv…），装着所有流

> 🔑 关键认知：**"一个视频文件"其实不是一坨画面，而是一个盒子（容器）里装着至少两条流。**
> 所有"音画不同步"的根源，都来自这两条流各有各的时间轴。

---

## 2. 视频流字段（streams[0]）

| 字段 | 本例值 | 含义 |
|---|---|---|
| `codec_name` / `codec_long_name` | h264 | 编码格式 = H.264/AVC（兼容性最好，网页/手机通吃） |
| `profile` / `level` | High / 40 | High profile + Level 4.0（level ≈ 能力档位，对应分辨率与码率上限） |
| `mime_codec_string` | avc1.640028 | 编码规格串：`64`=High profile，`00`=约束标志，`28`=Level 4.0 |
| `width` / `height` | 1920 / 1080 | **显示尺寸**（观众看到的） |
| `coded_width` / `coded_height` | 1920 / 1080 | **编码尺寸**。和显示尺寸不同 = 有裁剪/padding（当编码器要求 16 对齐时会补边） |
| `has_b_frames` | **2** | 🔑 **有 B 帧** → 存在 PTS ≠ DTS（见 §5） |
| `pix_fmt` | yuv420p | 像素格式：8bit + YUV 4:2:0 色度抽样。**视频不用 RGB，因为 RGB 体积大且兼容性差** |
| `sample_aspect_ratio` | 1:1 | 像素宽高比（每个像素是不是方的） |
| `display_aspect_ratio` | 16:9 | 显示宽高比（画面看起来的形状） |
| `color_space` / `color_transfer` / `color_primaries` | bt709 | 色彩空间/传递函数/色域。**SD 是 bt601、HD 是 bt709 —— 混用会导致偏色** |
| `field_order` | progressive | 逐行扫描（老电视是隔行 interlace） |
| `r_frame_rate` | **24/1** | 🔑 **帧率 = 分数**。分子/分母，别用除法当整数用 |
| `avg_frame_rate` | 24/1 | 🔑 平均帧率。**和 r_frame_rate 不一致 = VFR（可变帧率）** |
| `time_base` | 1/12288 | 🔑 时间基：1 个时间戳单位 = 1/12288 秒 |
| `start_pts` / `start_time` | 0 / 0.000000 | 起始时间戳（0 = 从头开始） |
| `duration_ts` | 148480 | 以 time_base 为单位的时长 |
| `duration` | 12.083333 | 视频流时长（秒） |
| `bit_rate` | 2807632 | 视频码率 ≈ 2.8 Mbps |
| `nb_frames` | **290** | 帧数 |

**验证两个数**（自己算一遍）：
- `duration_ts ÷ time_base 的分母` = 148480 ÷ 12288 = **12.08333 秒** ✅ 和 `duration` 一致
- `duration × r_frame_rate` = 12.083333 × 24 = **290 帧** ✅ 和 `nb_frames` 一致

> 🔑 **这就是"帧数对不对得上"的判据**。对得上 → CFR 且没丢帧；对不上 → 有 VFR 段或丢帧。

---

## 3. 音频流字段（streams[1]）

| 字段 | 本例值 | 含义 |
|---|---|---|
| `codec_name` / `profile` | aac / LC | AAC-LC（最通用） |
| `sample_fmt` | fltp | 采样格式（浮点平面） |
| `sample_rate` | **44100** | 🔑 44.1kHz（CD 标准）。**视频行业常用 48000** |
| `channels` / `channel_layout` | 2 / stereo | 声道数 / 布局（立体声/单声道/5.1…） |
| `time_base` | 1/44100 | 音频时间基 = 1/采样率 |
| `duration` | **12.003991** | 🔑 音频流时长 |
| `nb_frames` | 518 | AAC 每帧 1024 个采样 → 529376÷1024 ≈ 517，帧数对得上 |
| `initial_padding` / `seek_preroll` | 1024 | 解码器预热帧（流媒体起播要预读） |

---

## 4. 容器字段（format）

| 字段 | 本例值 | 含义 |
|---|---|---|
| `format_name` | mov,mp4,m4a,3gp,3g2,mj2 | 能装的格式列表（mp4 其实是 QuickTime 家族的一员） |
| `duration` | 12.083333 | 容器总时长 = 各流时长的最大值 |
| `size` | 4438677 | 文件大小（字节）≈ 4.2 MB |
| `bit_rate` | 2938710 | 整体码率 ≈ 2.8 Mbps |
| `nb_streams` | 2 | 两条流 |
| `tags.encoder` | **Lavf58.76.100** | 🔑 封装时用的 ffmpeg 版本（Lavf58 ↔ FFmpeg 5.0）→ **产物带工具版本指纹** |

> 🔑 **"两个 duration"的真相**：
> `format.duration` = 12.083333 = 视频流时长（取各流最大值）；
> 音频流 duration = 12.003991。
> **两者差 79ms** —— 这 79ms 就是音画不同步的物理来源。素材入库时必须检查这个差值。

---

## 5. 三个必须能讲出来的洞察

### ① PTS ≠ DTS（有 B 帧的必然结果）
本例 `has_b_frames = 2`。B 帧需要参考前后帧才能解码，所以**解码顺序 ≠ 显示顺序**：
- **DTS**（Decode Time Stamp）= 什么时候**解码**
- **PTS**（Presentation Time Stamp）= 什么时候**显示**
字幕、剪辑、音画同步一律认 **PTS**；解复用器排包认 DTS。

### ② r_frame_rate vs avg_frame_rate
`r_frame_rate` 是"基帧率"（容器声明的），`avg_frame_rate` 是"实际平均帧率"。
两者不等 → **VFR 可变帧率**（手机录屏、老片、变速视频都是 VFR）。
本例两者都是 24/1 → CFR 恒定帧率。

### ③ 规格不统一是真实工程痛点（本项目素材库的铁证）
同一批 5 个 demo 素材实测：

| 文件 | 分辨率 | 帧率 | 时长 | 采样率 |
|---|---|---|---|---|
| test.mp4 | 852×480 | 25 | 20.9s | 48000 |
| test1.mp4 | 640×360 | 29.97 | 120.0s | — |
| test2.mp4 | 1280×720 | 30 | 8.0s | — |
| test3.mp4 | 1920×1080 | 24 | 12.1s | 44100 |
| test4.mp4 | 720×1280（竖屏） | 30 | 92.7s | — |

**帧率有 24/25/29.97/30，分辨率有 360p~1080p，采样率有 44100/48000，方向有横有竖。**
→ 这就是为什么 pipeline 第一步必须是**素材入库自检 + 规格统一**：
统一帧率（否则字幕逐帧对不上）、统一采样率（否则拼接有爆音）、统一分辨率与方向（否则合成参数要分支）。

---

## 6. 脚本要输出的字段（映射）

| 需求 | 字段 | 来源 |
|---|---|---|
| 分辨率 | width / height | 视频流 |
| 帧率 | r_frame_rate / avg_frame_rate | 视频流 |
| 视频编码 | codec_name | 视频流 |
| 时长 | duration（流级 + 容器级两个） | 视频流 & format |
| 音频编码 | codec_name | 音频流 |
| 采样率 | sample_rate | 音频流 |
| 声道 | channels | 音频流 |
| 帧数 | nb_frames | 视频流 |
| 音画时长差 | 视频流 duration − 音频流 duration | 两个流相减 |
| 像素格式 / 色彩空间 | pix_fmt / color_space | 视频流 |

---

## 7. 我的踩坑记录（待补）

<!-- 这里写你自己踩的坑：粘贴控制字符、PATH 找不到、命令被换行拆开等 -->
