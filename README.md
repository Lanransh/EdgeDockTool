# EdgeDockTool

一个 Windows 桌面快捷启动器。

按下 `Alt + Space`，会在当前屏幕中央打开带毛玻璃效果的启动面板。常用文件、文件夹和程序入口都可以收纳到面板中，并通过搜索快速筛选。

## 功能

- `Alt + Space` 全局快捷键呼出/隐藏中央面板
- 当前鼠标所在屏幕居中显示，支持双屏
- 深色半透明毛玻璃面板，以及类似 macOS Dock 的悬停放大、点击回弹和堆叠淡入动画
- 面板提供小、中、大三档固定尺寸，避免自由缩放导致的拖动卡顿
- 面板内搜索应用名称或路径，回车可直接打开第一项
- 设置中可开启“可拖动模式”，从资源管理器直接拖入软件、文件或文件夹
- 将一个软件图标拖到另一个图标上会自动创建堆叠，点击堆叠即可展开
- 面板右上角可打开设置或进入本地快捷入口文件夹
- 托盘右键可打开设置、重启、退出
- 快捷入口由 `%APPDATA%\EdgeDockTool\Shortcuts` 文件夹管理，可直接重命名、移动或删除

## 安装

建议先创建虚拟环境，再安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```

更像普通软件的方式：

- 下载仓库后直接双击 `release\EdgeDockTool.exe`，无需安装 Python
- 或双击 `启动EdgeDockTool.bat`，脚本会优先启动仓库内的发布版
- 排查报错时可双击 `测试启动EdgeDockTool.bat`，会保留命令行窗口并输出日志

双击普通启动脚本后窗口会立即关闭，这是脚本把程序放到后台运行的正常表现；程序运行后会留在系统托盘。若 Alt+Space 没有反应，请先在任务管理器结束旧的 EdgeDockTool Python 进程，再重新双击启动脚本。也可以直接在项目目录运行 `.venv\Scripts\python.exe main.py` 观察报错。

重复启动会直接唤醒正在运行的面板。后台运行异常以及毛玻璃降级原因记录在 `%APPDATA%\EdgeDockTool\error.log`。

## 配置说明

- 程序配置会保存在 `%APPDATA%\EdgeDockTool\config.json`
- 快捷入口和堆叠保存在 `%APPDATA%\EdgeDockTool\Shortcuts`
- 如果配置损坏，程序会自动回退到默认配置
- 默认快捷键是 `Alt + Space`
- 从旧版边缘停靠版本升级时，会自动迁移到 `Alt + Space`
- 首次运行新版时，旧配置中的快捷入口会自动迁移到管理文件夹

## 技术栈

- Python 3.12+
- PySide6

## 许可证

本项目采用 MIT License，见 [`LICENSE`](LICENSE)。
