# workspace-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](./README.md) | [中文版](./README-zh.md)

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

workspace-guard 为 Hermes 智能体的工作目录强制执行文件纪律，采用两层互补
机制：捆绑的 `workspace-organization` 技能负责教导规则，插件负责在违规发生
之前将其拦截。本仓库版本：0.2.0。

## 安装

```bash
hermes plugins install shawVV1992/workspace-guard --enable
```

一条原生命令即可安装插件及其捆绑的技能、脚本与配置模板。重启 Hermes 后
守卫即开始生效。无需安装脚本，也无需单独安装技能。

## 更新

```bash
hermes plugins install shawVV1992/workspace-guard --force
```

重装不会清除 `guard-config.yaml`。

## 卸载

```bash
hermes plugins remove workspace-guard
```

## 功能

每个产生文件的 Hermes 对话都会在工作目录根部得到一个会话目录，命名为
`YYYYMMDD_HHMMSS_TaskName/`，其中 `Outputs/` 存放正式交付物、`.tmp/` 存放
中间文件。守卫会拦截指向工作目录根部（合法会话目录之外）的文件写入；
外部路径放行并记入日志。

## 配置

可选、由用户维护：`HERMES_HOME/workspace-guard/guard-config.yaml`
（Windows：`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`；POSIX：
`~/.hermes/workspace-guard/guard-config.yaml`）。键：`exempt_paths`、
`allowed_root_files`、`working_dir_root`、`terminal_guard`。

## 命令

- `/workspace-guard status` — 查看生效配置及其来源
- `/workspace-guard stats` — 本会话的拦截统计
- `/workspace-guard doctor` — 配置自检

## License

MIT
