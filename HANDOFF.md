# 项目交接文档 - 视频镜头与关键帧提取工具

## 项目目标

做一个本地桌面应用，从视频中智能提取高质量关键帧，作为 Stable Diffusion 等生成模型的训练数据集、素材库，也可用于反推图片提示词。

## 当前状态

- 基础框架：Python + PyQt5 + OpenCV + PySceneDetect。
- 镜头检测：已升级为可调参数的多模式检测。
- 默认模式：`hybrid`，合并 ContentDetector、AdaptiveDetector 和 OpenCV 直方图差异切点。
- 相似镜头合并：默认启用温和的“运镜误切修正”，会合并边界前后很像且至少一侧较短的相邻镜头。
- 弱运动误切过滤：v0.3.7 在混合模式下过滤只由弱 content/adaptive 分数触发、缺少直方图支持的切点，避免同一长镜头里的车身靠近、LOGO 叠加或构图变化被切开。
- 关键帧选择：支持每镜头多帧，按清晰度、信息量、对比度、曝光、色彩综合评分。
- UI：已重做为参数区、镜头列表、关键帧缩略图、预览微调区。
- 参数配置：支持保存参数、导入/导出参数，并设置启动默认参数。
- 手动微调：可在当前镜头范围内用滑块拖动预览，上一帧/下一帧预览，并把当前帧设为选中的关键帧。
- 导出：自动创建独立导出文件夹，保存无损 PNG 或有损 JPG + `metadata.json`，元数据包含检测参数、选帧参数、镜头范围、关键帧号。
- 首尾帧导出：可导出当前镜头或全部镜头的首尾帧，默认避让切点 1 帧，命名为 `shot_013_start_f000200_t00-00-08.000.png` 这类格式。
- 分镜视频导出：通过 `core/video_exporter.py` 按镜头批量导出片段视频，优先使用 `imageio-ffmpeg`/系统 ffmpeg；没有 ffmpeg 时回退到 OpenCV 重编码。
- 拖拽导入：主窗口支持直接拖入本地视频文件。
- 检测缓存：检测完成后自动写入 `%APPDATA%\VideoFrameExtractor\projects`，下次导入同一视频会加载镜头与关键帧，不必再次检测。
- 检测结果文件：UI 支持保存/导入检测结果 JSON，并按当前视频大小和采样 hash 校验匹配关系。
- 长视频提速：`core/shot_detector.py` 将 ContentDetector 与 AdaptiveDetector 合并到同一次 OpenCV 解码循环，减少混合模式的重复全片扫描。
- 特征缓存：`core/feature_cache.py` 将每帧 content score 和 histogram score 保存到 `%APPDATA%\VideoFrameExtractor\features`，同视频再次检测时可直接复算切点。
- 缓存管理：UI 支持清当前视频缓存、清全部缓存、打开缓存文件夹、清空当前结果。
- UI 稳定性：v0.3.6 固定左侧控制栏宽度，所有路径类状态使用短显示 + 完整 tooltip，避免保存参数、导出目录等长文本撑宽界面。
- UI 优化：v0.3.8 增加顶部上下文栏，左侧改为文件、检测、关键帧、处理、导出、缓存、参数的工作流顺序，结果和预览区压缩信息层级。
- 产品上下文：新增 `PRODUCT.md`，记录该工具的产品定位和设计原则，后续 UI 调整按“稳定、清楚、可靠”的产品工具方向推进。
- 商业化预留：UI 已加入账号与订阅入口，但尚未接入后端鉴权或支付。

## 本轮优化重点

用户反馈：检测到的镜头数偏少，希望能识别更多镜头切换，并增加可调参数和优化 UI。

已完成：

- `core/shot_detector.py`
  - 新增 `ShotDetectionSettings`。
  - 新增 `mode`：`hybrid` / `content` / `adaptive` / `histogram`。
  - 新增可调参数：`content_threshold`、`adaptive_threshold`、`histogram_threshold`、`min_scene_len_seconds`。
  - 默认阈值更灵敏：内容阈值 12，自适应阈值 2.0，差异阈值 0.16，最短镜头 0.35 秒。
  - ContentDetector 与 AdaptiveDetector 可在单次解码中并行处理，改善长视频检测速度。
  - 检测时会收集并保存轻量特征；缓存命中后可跳过主检测解码，用特征分数重新计算切点。
  - 新增弱运动误切过滤：强切点或有 histogram 支持的切点保留，弱 content 单点切点默认丢弃。
- `core/feature_cache.py`
  - 新增视频特征缓存，缓存键由文件名、大小和头/中/尾采样 hash 组成。
  - 缓存文件为压缩 `.npz`，不会复制原视频。

- `core/frame_selector.py`
  - 新增 `FrameSelectionSettings`。
  - 支持每镜头多关键帧。
  - 增加时间间隔约束，避免多张关键帧挤在同一瞬间。
  - 评分加入色彩饱和度。

- `ui/main_window.py`
  - 参数面板重新组织。
  - 增加切分灵敏度滑块和运镜误切修正滑块，隐藏繁琐阈值，保留每镜头帧数控制。
  - 增加参数保存、导入、导出、设为默认。
  - 增加关键帧缩略图网格。
  - 增加当前镜头帧滑块、手动逐帧替换关键帧和键盘快捷键。
  - 导出时自动创建 `{视频名}_keyframes_{时间}` 子文件夹。
  - 增加当前/批量首尾帧导出和分镜视频导出按钮。
  - 增加拖拽导入视频、检测结果自动缓存加载、检测结果 JSON 保存/导入。
  - 增加缓存管理面板：缓存大小、清当前视频缓存、清全部缓存、打开缓存文件夹、清空当前结果。
  - 固定左侧栏宽度，关闭横向滚动，状态栏长路径自动省略并用 tooltip 保留完整内容。
  - 增加账号/注册/订阅入口占位。
- `core/video_exporter.py`
  - 新增镜头片段视频导出器。
  - 文件名包含 shot 编号、起止帧号和起止时间码。
  - 支持 ffmpeg copy、ffmpeg 高质量重编码，以及 OpenCV 回退路径。

## 实测结果

在 `D:\AIGC\拉片切片\新建文件夹\jlyzw0zn3MoQB198_1080p.mp4` 上：

- 旧逻辑：`ContentDetector threshold=20` 检测 172 个镜头。
- 新默认混合模式：检测 209 个镜头。

在另一个短样片上：

- 旧逻辑：17 个镜头。
- 新默认混合模式：31 个镜头。

## 打包产物

已生成：

- `release\VideoFrameExtractor-portable.zip`
  - 约 93 MB。
  - 解压后运行 `VideoFrameExtractor\VideoFrameExtractor.exe`。
  - 已验证：从 zip 解压到临时目录后，exe 能启动并保持运行 5 秒以上。

- `release\VideoFrameExtractor-source.zip`
  - 源码续跑包。
  - 新电脑上先运行 `setup_windows.cmd`，再运行 `run_app.cmd`。

当前源码目录也可直接运行：

```cmd
run_app.cmd
```

## 启动方式

```cmd
cd C:\Users\Administrator\.claude\video-frame-extractor
python main.py
```

## 后续建议

1. 加批量处理多个视频。
2. 增加相似帧去重，可用 perceptual hash 或 SSIM。
3. 增加项目保存/打开，避免每次重新检测。
4. 登录订阅建议接入 FastAPI + PostgreSQL/Supabase/Auth.js 任一后端方案。
5. 订阅支付可先按目标市场选择 Stripe、微信支付、支付宝或 Paddle。
6. 如果要做订阅制桌面软件，需要增加授权校验、离线宽限期、套餐额度和导出水印/限制策略。
