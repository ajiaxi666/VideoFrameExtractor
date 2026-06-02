# 视频镜头与关键帧提取工具

一个本地桌面应用，用于从视频中检测镜头切换，并自动提取清晰、有代表性的关键帧，适合作为生成模型训练数据集、素材库或提示词反推素材。

## 当前能力

- 镜头检测：支持标准内容检测、自适应检测、直方图差异检测和混合增强模式。
- 参数可调：用“切分灵敏度”滑块控制镜头切分密度，内部自动映射内容阈值、自适应阈值、差异阈值和最短镜头时长。
- 运镜误切修正：可自动合并快速运镜造成的相似相邻镜头。
- 参数配置：支持保存参数、导入/导出参数，并设置启动默认参数。
- 关键帧选择：按清晰度、信息量、对比度、曝光和色彩综合评分。
- 多关键帧：每个镜头可提取 1-6 张关键帧。
- 缩略图视图：快速浏览所有选中关键帧。
- 手动微调：可在当前镜头范围内拖动帧滑块，逐帧前后移动，并替换当前关键帧。
- 数据集导出：自动在选择的位置创建独立导出文件夹，保存无损 PNG 或有损 JPG 和 `metadata.json`。
- 账号订阅入口：UI 已预留登录、注册、订阅按钮，后续可接后端和支付。

## 安装

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

如果公司电脑无法直接运行 exe，就使用源码包：

1. 解压 `VideoFrameExtractor-source.zip`。
2. 双击 `setup_windows.cmd` 安装依赖。
3. 双击 `run_app.cmd` 启动。

## 参数建议

- 想识别更多镜头：使用“混合增强”，降低“内容阈值”和“差异阈值”。
- 误切太多：提高“差异阈值”，或把“最短镜头”调到 0.5-1.0 秒。
- 快节奏广告/短视频：内容阈值 10-14，差异阈值 0.12-0.18。
- 电影/长镜头素材：内容阈值 18-27，差异阈值 0.20-0.30。
- 训练数据集：每镜头 2-3 帧通常比只取 1 帧更稳。

## 项目结构

```text
video-frame-extractor/
├── main.py
├── requirements.txt
├── README.md
├── HANDOFF.md
├── ui/
│   ├── __init__.py
│   └── main_window.py
└── core/
    ├── __init__.py
    ├── video_processor.py
    ├── shot_detector.py
    ├── frame_selector.py
    └── image_saver.py
```
