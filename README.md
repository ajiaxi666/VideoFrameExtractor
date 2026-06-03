# 视频镜头与关键帧提取工具

VideoFrameExtractor 是一个 Windows 本地桌面工具，用于从视频中检测镜头切换，并自动提取清晰、有代表性的关键帧。它适合用于 AIGC 训练数据集整理、素材库建立、镜头复查和图片提示词反推素材准备。

## License

VideoFrameExtractor community edition is released under the GNU General Public License v3.0. See [LICENSE](LICENSE).

Because this project uses PyQt5, GPLv3 is the intended license for the public community codebase. Third-party dependency notes are recorded in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## 当前能力

- 镜头检测：支持标准内容检测、自适应检测、直方图差异检测和混合增强模式。
- 参数可调：用“切分灵敏度”滑块控制镜头切分密度，内部自动映射内容阈值、自适应阈值、差异阈值和最短镜头时长。
- 运镜误切修正：可自动合并快速运镜造成的相似相邻镜头。
- 弱切点过滤：默认混合模式会过滤只由轻微内容分数触发、缺少直方图支持的运动误切。
- 参数配置：支持保存参数、导入/导出参数，并设置启动默认参数。
- 关键帧选择：按清晰度、信息量、对比度、曝光和色彩综合评分。
- 多关键帧：每个镜头可提取 1-6 张关键帧。
- 缩略图视图：当前镜头首中尾帧独立展示，其它镜头关键帧以横向缩略图条浏览。
- 手动微调：可在当前镜头范围内拖动帧滑块，逐帧前后移动，并替换当前关键帧。
- 数据集导出：自动在选择的位置创建独立导出文件夹，保存无损 PNG 或有损 JPG 和 `metadata.json`。
- 首中尾帧导出：支持导出当前镜头或全部镜头的首帧、中间帧和尾帧，文件名包含镜头号、顺序、帧号和时间码，按名称排序时首帧、中间帧、尾帧依次排列。
- 拖拽导入：可把视频文件直接拖进窗口开始载入。
- 检测缓存：检测完成后自动保存镜头与关键帧结果，下次导入同一视频会直接加载缓存。
- 检测结果文件：支持手动保存/导入检测结果 JSON，便于迁移到其它电脑或备份项目。
- 长视频优化：混合检测中的 Content/Adaptive 检测合并为单次解码，减少长片重复扫描。
- 特征缓存：首次检测会保存每帧低分辨率特征，下次重新检测同一视频可直接复用特征，少做解码。
- 缓存管理：支持清当前视频缓存、清全部缓存、打开缓存文件夹，以及清空当前结果释放缩略图占用。
- 工作台界面：顶部显示当前视频上下文、版本和缓存状态，左侧是大预览、关键帧预览和底部镜头表格，右侧是参数与导出 inspector。
- 可调参数栏：右侧参数区支持拖动调整宽度，基础参数常显，高级检测和选帧参数默认折叠。
- 预览快捷跳转：当前镜头预览区支持一键跳到首帧、中间帧和尾帧，便于复查首中尾帧导出结果。
- 桌面图标：Windows 便携版包含蓝白胶片关键帧图标，用于 EXE、窗口标题栏和任务栏。
- 检测提速：v0.3.10 将镜头检测统一在 240px 分析帧和 5 帧采样步长上完成，并减少选帧阶段的无效取图，首轮大视频检测更快，二次导入仍可走缓存。

## 安装

建议使用 Python 3.10 或更新版本。

```bash
pip install -r requirements.txt
```

## 启动

```bash
python main.py
```

Windows 上也可以直接双击：

- `setup_windows.cmd`：第一次在新电脑上运行，创建虚拟环境并安装依赖。
- `run_app.cmd`：启动应用。

## 打包

在 Windows PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

生成结果：

```text
release/VideoFrameExtractor-portable.zip
release/VideoFrameExtractor-source.zip
```

解压后运行：

```text
VideoFrameExtractor/VideoFrameExtractor.exe
```

如果目标电脑无法直接运行 exe，可以使用源码包：

1. 解压 `VideoFrameExtractor-source.zip`。
2. 双击 `setup_windows.cmd` 安装依赖。
3. 双击 `run_app.cmd` 启动。

## 参数建议

- 1 小时电影建议优先用默认混合模式；如果只追求速度，可以切到“标准内容检测”，会少跑补充检测但可能漏掉弱切点。
- 杜比/HDR/几十 GB 原片主要风险不是文件大小，而是本机 OpenCV/FFmpeg 能否解码该编码格式；检测过程按帧流式读取，不会把整部视频载入内存。
- 缓存只保存镜头结果和低分辨率特征，不复制原视频；如果 C 盘空间紧张，可在“缓存管理”里清理。
- 想识别更多镜头：使用“混合增强”，降低“内容阈值”和“差异阈值”。
- 误切太多：提高“差异阈值”，或把“最短镜头”调到 0.5-1.0 秒。
- 快节奏广告/短视频：内容阈值 10-14，差异阈值 0.12-0.18。
- 电影/长镜头素材：内容阈值 18-27，差异阈值 0.20-0.30。
- 训练数据集：每镜头 2-3 帧通常比只取 1 帧更稳。

## 贡献与安全

- 贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全与隐私注意事项见 [SECURITY.md](SECURITY.md)。
- 请不要把原视频、检测缓存、导出图片或私人项目文件提交到仓库。
- 当前暂不接收未经过授权确认的外部代码贡献；提交 PR 前请先阅读贡献说明。

## 项目结构

```text
video-frame-extractor/
├── assets/
│   ├── app_icon.ico
│   └── app_icon.png
├── core/
│   ├── __init__.py
│   ├── feature_cache.py
│   ├── frame_selector.py
│   ├── image_saver.py
│   ├── shot_detector.py
│   └── video_processor.py
├── ui/
│   ├── __init__.py
│   └── main_window.py
├── main.py
├── requirements.txt
├── build_portable.ps1
├── setup_windows.cmd
├── run_app.cmd
├── LICENSE
├── README.md
├── PRODUCT.md
├── HANDOFF.md
├── CONTRIBUTING.md
├── SECURITY.md
├── THIRD_PARTY_LICENSES.md
└── OPEN_SOURCE_CHECKLIST.md
```
