#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quick_Create_Release.py - 项目发布打包脚本

读取多个 ignore 文件规则（可位于不同子目录），排除匹配的文件后打包为 release zip
命名格式: YYYYMMDDHHMM_项目名_vX.X.X.zip
"""

import os
import zipfile
from datetime import datetime
from pathlib import Path
import fnmatch

# ==================== 常量配置 ====================

# 压缩包后缀名称
PROJECT_NAME = "Any-Wireless-Mic"

# 发布目录（相对于项目根目录）
RELEASE_DIR = Path("0_Release")

# ignore 文件路径列表（相对于项目根目录，语法同 .gitignore）
# 每个文件中的规则作用域为该文件所在目录及其子目录
IGNORE_FILES = [
    ".gitignore",
]

# 额外排除的文件/文件夹（全局生效）
EXTRA_IGNORE = [
    ".git",
    "0_Release",
    "0_Backup",
    "0_Reference",
    "0_Design",
    "0_Doc",
    ".gitignore",
    "1_Script",
    "setup_claude_dir.py",
    "sync_rules_to_clinerules.py",
    "CLAUDE.md",
]

# ==================== 功能函数 ====================


def parse_ignore_file(ignore_path):
    """解析单个 ignore 文件，返回 (作用域目录, 规则列表)"""
    path = Path(ignore_path)
    if not path.exists():
        print(f"  跳过: {ignore_path}（文件不存在）")
        return None

    scope_dir = str(path.parent)  # 规则作用域 = ignore 文件所在目录
    if scope_dir == ".":
        scope_dir = ""

    rules = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rules.append(line)

    print(f"  加载: {ignore_path}（{len(rules)} 条规则，作用域: {scope_dir or '根目录'}）")
    return (scope_dir, rules)


def load_ignore_rules():
    """加载所有 ignore 文件，返回 [(作用域, 规则列表), ...]"""
    scoped_rules = []
    for ignore_file in IGNORE_FILES:
        result = parse_ignore_file(ignore_file)
        if result:
            scoped_rules.append(result)
    # 全局额外规则（作用域为根目录）
    if EXTRA_IGNORE:
        scoped_rules.append(("", EXTRA_IGNORE))
    return scoped_rules


def should_ignore(rel_path, scoped_rules):
    """检查相对路径是否应被排除"""
    rel_str = str(rel_path)
    name = Path(rel_path).name

    for scope_dir, rules in scoped_rules:
        # 判断路径是否在该 ignore 文件的作用域内
        if scope_dir and not rel_str.startswith(scope_dir + "/") and rel_str != scope_dir:
            continue

        # 在作用域内，取相对于作用域的路径来匹配
        if scope_dir:
            local_path = rel_str[len(scope_dir) + 1:]
        else:
            local_path = rel_str

        for rule in rules:
            # 去掉尾部斜杠（目录标记）
            clean_rule = rule.rstrip("/")
            # 匹配文件/目录名
            if fnmatch.fnmatch(name, clean_rule):
                return True
            # 匹配相对路径
            if fnmatch.fnmatch(local_path, clean_rule):
                return True
            # 匹配带通配的路径
            if fnmatch.fnmatch(local_path, clean_rule + "/*"):
                return True

    return False


def collect_files(scoped_rules):
    """遍历项目目录，收集未被排除的文件"""
    files = []
    for root, dirs, filenames in os.walk("."):
        rel_root = os.path.relpath(root, ".")
        if rel_root == ".":
            rel_root = ""

        # 过滤目录：排除匹配的子目录，阻止深入
        filtered_dirs = []
        for d in dirs:
            rel_dir = os.path.join(rel_root, d) if rel_root else d
            if not should_ignore(rel_dir, scoped_rules):
                filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        # 收集文件
        for f in filenames:
            rel_file = os.path.join(rel_root, f) if rel_root else f
            if not should_ignore(rel_file, scoped_rules):
                files.append(rel_file)

    return sorted(files)


def format_size(size_bytes):
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def create_release():
    """创建发布包"""

    print("=" * 60)
    print(f"项目发布打包工具 - {PROJECT_NAME}")
    print("=" * 60)

    # 第一步：加载排除规则
    print("\n[步骤 1/4] 加载排除规则...")
    scoped_rules = load_ignore_rules()
    if not scoped_rules:
        print("警告: 未加载到任何排除规则，将打包所有文件")

    # 第二步：收集文件
    print("\n[步骤 2/4] 扫描项目文件...")
    files = collect_files(scoped_rules)

    if not files:
        print("错误: 没有找到任何要打包的文件")
        return False

    # 计算总大小
    total_size = sum(os.path.getsize(f) for f in files)

    # 第三步：显示打包内容，等待确认
    print("\n[步骤 3/4] 要打包的内容:")
    print("-" * 60)

    for f in files[:500]:
        size_kb = os.path.getsize(f) / 1024
        print(f"  📄 {f} ({size_kb:.1f} KB)")
    if len(files) > 500:
        print(f"  ... 还有 {len(files) - 500} 个文件")

    print("-" * 60)
    print(f"总计: {len(files)} 个文件, 总大小: {format_size(total_size)}")

    # 读取版本号
    version = ""
    version_file = Path("VERSION")
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8").strip()

    # 生成发布包文件名
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    if version:
        release_name = f"{timestamp}_{PROJECT_NAME}_v{version}.zip"
    else:
        release_name = f"{timestamp}_{PROJECT_NAME}.zip"
    release_path = RELEASE_DIR / release_name

    print(f"\n版本号: {version if version else '无'}")
    print(f"文件名: {release_name}")

    # 用户确认
    print("\n是否继续打包? (y/n): ", end="")
    user_input = input().strip().lower()
    if user_input not in ["y", "yes", "是"]:
        print("已取消打包")
        return False

    # 第四步：执行打包
    print("\n[步骤 4/4] 开始打包...")
    RELEASE_DIR.mkdir(exist_ok=True)

    print(f"正在创建发布包: {release_name}")

    with zipfile.ZipFile(release_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for f in files:
            size_kb = os.path.getsize(f) / 1024
            print(f"  添加: {f} ({size_kb:.1f} KB)")
            zipf.write(f, f)

    # 完成
    size_str = format_size(release_path.stat().st_size)
    print("\n" + "=" * 60)
    print("发布包创建完成!")
    print(f"  文件: {release_path}")
    print(f"  大小: {size_str}")
    print("=" * 60)

    return True


if __name__ == "__main__":
    create_release()
