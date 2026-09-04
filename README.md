# EdgeDockTool

一个 Windows 桌面快捷启动器。

按下 `Alt + Space`，会在当前屏幕中央打开带毛玻璃效果的启动面板。常用文件、文件夹和程序入口都可以收纳到面板中，并通过搜索快速筛选。

## 功能

- `Alt + Space` 全局快捷键呼出/隐藏中央面板
- 当前鼠标所在屏幕居中显示，支持双屏
- 深色半透明毛玻璃面板、淡入缩放和点击反馈动画
- 面板内搜索应用名称或路径，回车可直接打开第一项
- 托盘右键可打开设置、重启、退出
- 设置页支持拖拽添加文件和文件夹
- 快捷入口会自动保存到本地配置

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

- 优先双击 `dist\EdgeDockTool.exe`
- 或双击 `启动EdgeDockTool.bat`，它现在会优先拉起无控制台窗口版本
- 排查报错时可双击 `测试启动EdgeDockTool.bat`，会保留命令行窗口并输出日志

## 配置说明

- 程序配置会保存在 `%APPDATA%\EdgeDockTool\config.json`
- 仓库中不保存你的本地快捷入口数据
- 如果配置损坏，程序会自动回退到默认配置
- 默认快捷键是 `Alt + Space`，可在设置中重新录入

## 技术栈

- Python 3.12+
- PySide6

## 许可证

本项目采用 MIT License，见 [`LICENSE`](LICENSE)。
