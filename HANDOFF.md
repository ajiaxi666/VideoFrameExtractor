# 项目交接文档 - 视频镜头与关键帧提取工具

## 项目目标

做一个 Windows 本地桌面应用，从视频中智能提取高质量关键帧，作为生成模型训练数据集、素材库，也可用于反推图片提示词。

## 当前状态

- 基础框架：Python + PyQt5 + OpenCV + PySceneDetect。
- 镜头检测：已升级为可调参数的多模式检测。
- 默认模式：`hybrid`，合并 ContentDetector、AdaptiveDetector 和 OpenCV 直方图差异切点。
- 相似镜头合并：默认启用温和的“运镜误切修正”，会合并边界前后很像且至少一侧较短的相邻镜头。
- 弱运动误切过滤：v0.3.7 在混合模式下过滤只由弱 content/adaptive 分数触发、缺少直方图支持的切点，避免同一长镜头里的构图变化被切开。
- 关键帧选择：支持每镜头多帧，按清晰度、信息量、对比度、曝光、色彩综合评分。
- UI：v0.3.15 已重做为大预览工作区、关键帧缩略图区、底部镜头表格和右侧参数 inspector。
- 参数配置：支持保存参数、导入/导出参数，并设置启动默认参数。
- 手动微调：可在当前镜头范围内用滑块拖动预览，上一帧/下一帧预览，并把当前帧设为选中的关键帧。
- 导出：自动创建独立导出文件夹，保存无损 PNG 或有损 JPG + `metadata.json`，元数据包含检测参数、选帧参数、镜头范围、关键帧号。
- 首中尾帧导出：可导出当前镜头或全部镜头的首帧、中间帧和尾帧，首尾默认避让切点 1 帧，命名为 `shot_013_01_start_f000200_t00-00-08.000.png`、`shot_013_02_middle_f000215_t00-00-08.600.png` 和 `shot_013_03_end_f000230_t00-00-09.200.png` 这类格式，方便按名称排序。
- 拖拽导入：主窗口支持直接拖入本地视频文件。
- 检测缓存：检测完成后自动写入 `%APPDATA%\VideoFrameExtractor\projects`，下次导入同一视频会加载镜头与关键帧，不必再次检测。
- 检测结果文件：UI 支持保存/导入检测结果 JSON，并按当前视频大小和采样 hash 校验匹配关系。
- 长视频提速：`core/shot_detector.py` 将 ContentDetector 与 AdaptiveDetector 合并到同一次 OpenCV 解码循环，减少混合模式的重复全片扫描。
- 特征缓存：`core/feature_cache.py` 将每帧 content score 和 histogram score 保存到 `%APPDATA%\VideoFrameExtractor\features`，同视频再次检测时可直接复算切点。
- 缓存管理：UI 支持清当前视频缓存、清全部缓存、打开缓存文件夹、清空当前结果。
- 功能收敛：v0.3.11 移除分镜视频片段导出、ffmpeg 依赖和相关 UI，产品重心回到关键帧、首中尾帧和检测结果缓存。
- 首中尾帧：v0.3.13 在首尾帧导出中加入 `02_middle` 中间帧，并将尾帧顺序更新为 `03_end`；极短镜头中重复帧会自动去重。
- UI 与桌面图标：v0.3.14 新增蓝白胶片关键帧桌面图标并接入 Windows 打包。
- UI 重构：v0.3.15 按参考图重做 Windows 外壳，参数与导出移到右侧 inspector，左侧主工作区包含大预览、首中尾缩略图、其它关键帧条和底部镜头表格；主界面不再使用传统 `QGroupBox` 堆叠。
- 产品上下文：`PRODUCT.md` 记录该工具的产品定位和设计原则，后续 UI 调整按“稳定、清楚、可靠”的产品工具方向推进。
- 开源状态：社区版采用 GPLv3；PyQt5 路线与 GPLv3 公开授权保持一致。当前不创建 GitHub Release。

## 本轮整理重点

本轮只做开源前整理，不改变检测算法、缓存格式、导出命名、图片质量逻辑或打包入口。

已完成：

- 移除旧版用户体系占位方法和说明。
- README 改为 Windows 本地工具说明，并加入 GPLv3 授权状态。
- HANDOFF 改为公开版交接文档，去掉本机绝对路径、内部样片路径和未采用的外部服务建议。
- 新增 `THIRD_PARTY_LICENSES.md`、`CONTRIBUTING.md`、`SECURITY.md` 和 `OPEN_SOURCE_CHECKLIST.md`。
- 新增 GPLv3 `LICENSE`，并在贡献说明中标明外部代码贡献边界。

## 主要模块

- `main.py`：应用入口，创建 `QApplication` 和主窗口。
- `ui/main_window.py`：主界面、参数面板、拖拽导入、缓存管理、预览微调、导出动作。
- `core/shot_detector.py`：镜头检测与切点合并逻辑。
- `core/feature_cache.py`：低分辨率特征缓存。
- `core/frame_selector.py`：关键帧评分和多帧选择。
- `core/image_saver.py`：关键帧和首中尾帧图片导出。
- `core/video_processor.py`：视频信息读取、帧读取和缩略图支持。

## 验证记录

历史验证使用内部样片完成，公开文档不保留样片路径。

- 长样片：旧逻辑检测 172 个镜头，新默认混合模式检测 209 个镜头。
- 短样片：旧逻辑检测 17 个镜头，新默认混合模式检测 31 个镜头。
- 打包验证：`release/VideoFrameExtractor-portable.zip` 解压后运行 `VideoFrameExtractor\VideoFrameExtractor.exe`，可启动并保持运行 5 秒以上。

## 启动方式

源码运行：

```cmd
python main.py
```

Windows 快捷脚本：

```cmd
run_app.cmd
```

首次在新电脑运行源码包：

```cmd
setup_windows.cmd
```

## 打包方式

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

预期产物：

- `release\VideoFrameExtractor-portable.zip`
- `release\VideoFrameExtractor-source.zip`

## 后续建议

1. 加批量处理多个视频。
2. 增加相似帧去重，可用 perceptual hash 或 SSIM。
3. 增加项目保存/打开，让检测结果和导出配置更容易迁移。
4. 补公开截图或短演示，使用非私有素材。
5. 后续如需要非 GPL 公开授权，再评估迁移到 PySide6 或其它 UI 技术。
