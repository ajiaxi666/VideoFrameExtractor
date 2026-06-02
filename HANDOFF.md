# 项目交接文档 - 视频镜头与关键帧提取工具

## 项目目标

做一个本地桌面应用，从视频中智能提取高质量关键帧，作为 Stable Diffusion 等生成模型的训练数据集、素材库，也可用于反推图片提示词。

## 当前状态

- 基础框架：Python + PyQt5 + OpenCV + PySceneDetect。
- 镜头检测：已升级为可调参数的多模式检测。
- 默认模式：`hybrid`，合并 ContentDetector、AdaptiveDetector 和 OpenCV 直方图差异切点。
- 相似镜头合并：默认启用温和的“运镜误切修正”，会合并边界前后很像且至少一侧较短的相邻镜头。
- 关键帧选择：支持每镜头多帧，按清晰度、信息量、对比度、曝光、色彩综合评分。
- UI：已重做为参数区、镜头列表、关键帧缩略图、预览微调区。
- 参数配置：支持保存参数、导入/导出参数，并设置启动默认参数。
- 手动微调：可在当前镜头范围内用滑块拖动预览，上一帧/下一帧预览，并把当前帧设为选中的关键帧。
- 导出：自动创建独立导出文件夹，保存无损 PNG 或有损 JPG + `metadata.json`，元数据包含检测参数、选帧参数、镜头范围、关键帧号。
- 商业化预留：UI 已加入账号与订阅入口，但尚未接入后端鉴权或支付。

## 本轮优化重点

用户反馈：检测到的镜头数偏少，希望能识别更多镜头切换，并增加可调参数和优化 UI。

已完成：

- `core/shot_detector.py`
  - 新增 `ShotDetectionSettings`。
  - 新增 `mode`：`hybrid` / `content` / `adaptive` / `histogram`。
  - 新增可调参数：`content_threshold`、`adaptive_threshold`、`histogram_threshold`、`min_scene_len_seconds`。
  - 默认阈值更灵敏：内容阈值 12，自适应阈值 2.0，差异阈值 0.16，最短镜头 0.35 秒。

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
  - 增加账号/注册/订阅入口占位。

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
