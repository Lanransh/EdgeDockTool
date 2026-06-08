# EdgeDockTool

一个 Windows 桌面边缘磁吸快捷工具。

它适合把常用文件、文件夹和程序入口收纳到屏幕边缘，支持双屏、托盘常驻、边缘悬停展开和自定义快捷入口。

## 功能

- 窗口可停靠在左、右、上、下四个边缘
- 鼠标在边缘停留后自动展开侧边抽屉
- 支持双屏，跟随鼠标所在屏幕触发
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

## 技术栈

- Python 3.12+
- PySide6

## 许可证

本项目采用 MIT License，见 [`LICENSE`](LICENSE)。
