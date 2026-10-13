# -*- coding: utf-8 -*-
"""
命令名单管理器 · CommandGuard v3.0
图形化管理 CodeBuddy 系产品（WorkBuddy 桌面版 / CodeBuddy 命令行 / CodeBuddy CN IDE）
的命令「放行 / 询问 / 拦截」名单。三栏并排、实时读取。

三个入口（位置支持自动检测 + 手动指定保底，见「设置…」按钮）：

  ① WorkBuddy 桌面版   ~/.workbuddy/settings.json
       - sandbox.orderedRules.command.rules（沙盒命令规则）
       - permissions.allow / ask / deny（Bash 权限）
  ② CodeBuddy 命令行   ~/.codebuddy/settings.json
       - permissions.allow / ask / deny（Bash 权限）
  ③ CodeBuddy CN IDE   在 %APPDATA% 下自动查找 *CodeBuddy* 目录：
       User/globalStorage/state.vscdb → 库键 CodeBuddy.settings（明文 JSON）
         autoApprovalSettings.customBlacklistCommands   命令黑名单（正则）
         autoApprovalSettings.disabledSecurityCategories 安全检查类别
         chat.autoRunMode                               RunEverything / AskEveryTime
       - 注意：IDE 只在启动时读取这份设置 → 保存时请先退出 IDE，保存后重开 IDE 生效。

免责声明：非官方工具，与腾讯及其 CodeBuddy / WorkBuddy 产品无隶属关系，仅读写本地配置文件。

用法：直接运行打开界面；`--selftest` 离线自检；`--smoke` 界面冒烟测试。

----
Copyright (C) 2026 GuDogShu

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

IS_WIN = os.name == "nt"
HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")

DEFAULTS = {
    "wb": os.path.join(HOME, ".workbuddy", "settings.json"),
    "cb": os.path.join(HOME, ".codebuddy", "settings.json"),
    "ide_db": os.path.join(APPDATA, "CodeBuddy CN", "User", "globalStorage", "state.vscdb"),
    "ide_json": os.path.join(APPDATA, "CodeBuddy CN", "User", "settings.json"),
}
IDE_STORE_KEY = "CodeBuddy.settings"

CONFIG_DIR = os.path.join(LOCALAPPDATA, "CommandGuard")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

PATHS = dict(DEFAULTS)      # 生效路径（手动指定 > 自动检测 > 默认兜底）
DETECTED = dict(DEFAULTS)   # 最近一次自动检测的结果

ACTION_CN = {"allow": "允许", "ask": "询问", "deny": "拒绝"}
ACTION_CHOICES = ["允许（直接执行，不再确认）", "询问（每次执行前确认）", "拒绝（直接拦截）"]
CHOICE_ACTION = {
    "允许（直接执行，不再确认）": "allow",
    "询问（每次执行前确认）": "ask",
    "拒绝（直接拦截）": "deny",
}

ROW_TAG_COLORS = {
    "allow": {"background": "#F1F8F1"},
    "ask": {"background": "#FFF9EC"},
    "deny": {"background": "#FDF0EF"},
}

CATEGORIES = [
    ("diskOps", "磁盘操作"),
    ("windowsSystem", "Windows 系统"),
    ("network", "网络"),
    ("systemServices", "系统服务"),
    ("userManagement", "用户管理"),
    ("fileDelete", "文件删除"),
    ("permissions", "权限修改"),
    ("processControl", "进程控制"),
    ("gitOps", "Git 操作"),
    ("injection", "注入类"),
    ("scriptExec", "脚本执行"),
    ("powershell", "PowerShell"),
    ("custom", "自定义"),
]


# ---------------- 配置与路径自动检测 ----------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _db_has_store(path):
    try:
        con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("select 1 from ItemTable where key = ? limit 1", (IDE_STORE_KEY,)).fetchone()
        finally:
            con.close()
        return bool(row)
    except Exception:
        return False


def autodetect_paths():
    """自动检测各配置位置；找不到的项为 None（不修改全局状态）。"""
    import glob as _glob
    det = {"wb": None, "cb": None, "ide_db": None, "ide_json": DEFAULTS["ide_json"]}
    for key in ("wb", "cb"):
        p = DEFAULTS[key]
        if os.path.exists(p):
            det[key] = p
    cands = []
    if IS_WIN and os.path.isdir(APPDATA):
        for pat in ("*CodeBuddy*", "*Buddy*"):
            cands.extend(_glob.glob(os.path.join(APPDATA, pat, "User", "globalStorage", "state.vscdb")))
    if DEFAULTS["ide_db"] not in cands and os.path.exists(DEFAULTS["ide_db"]):
        cands.insert(0, DEFAULTS["ide_db"])
    seen = set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        if os.path.exists(c) and _db_has_store(c):
            det["ide_db"] = c
            det["ide_json"] = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(c))), "User", "settings.json")
            break
    return det


def resolve_paths():
    """生效路径 = 手动指定（「设置…」里填的）优先 > 自动检测 > 默认兜底。"""
    global PATHS, DETECTED
    cfg = load_config()
    ov = cfg.get("overrides") or {}
    DETECTED = autodetect_paths()
    out = {}
    for key in ("wb", "cb", "ide_db"):
        v = str(ov.get(key) or "").strip()
        out[key] = v if v else (DETECTED.get(key) or DEFAULTS[key])
    out["ide_json"] = DETECTED.get("ide_json") or DEFAULTS["ide_json"]
    PATHS = out
    return PATHS


try:
    resolve_paths()
except Exception:
    PATHS = dict(DEFAULTS)


# ---------------- 基础读写 ----------------
def load_json(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    if not path:
        raise ValueError("没有可写入的路径")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)  # 自校验：写出的内容必须可再解析
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    bak = None
    if os.path.exists(path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = path + ".bak-" + stamp
        n = 1
        while os.path.exists(bak):
            n += 1
            bak = path + ".bak-" + stamp + "-" + str(n)
        shutil.copy2(path, bak)
    tmp = path + ".tmp-" + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return bak


def dig(data, *keys):
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def _ensure_dict(d, key):
    v = d.get(key)
    if not isinstance(v, dict):
        v = {}
        d[key] = v
    return v


# ---------------- ① 桌面版：沙盒命令规则 ----------------
def read_cmd_rules(data):
    out = []
    arr = dig(data, "sandbox", "orderedRules", "command", "rules")
    if not isinstance(arr, list):
        return out
    for it in arr:
        if not isinstance(it, dict):
            continue
        prefix = it.get("prefix", [])
        out.append({
            "id": str(it.get("id", "")),
            "prefix": [str(x) for x in prefix if isinstance(x, str)],
            "action": str(it.get("action", "allow")),
            "source": str(it.get("source", "user")),
            "reason": str(it.get("reason", "")),
        })
    return out


def write_cmd_rules(data, rules):
    sandbox = data.setdefault("sandbox", {})
    ordered = sandbox.setdefault("orderedRules", {})
    cmd = ordered.setdefault("command", {})
    if "presetVersion" not in cmd:
        cmd["presetVersion"] = 1
    cmd["customized"] = True
    arr = []
    for r in rules:
        item = {
            "id": r["id"],
            "prefix": list(r["prefix"]),
            "action": r["action"],
            "source": r.get("source") or "user",
        }
        if r.get("reason") and r["action"] != "allow":
            item["reason"] = r["reason"]
        arr.append(item)
    cmd["rules"] = arr

    # 同步旧版字段（与 WorkBuddy 自带迁移逻辑保持一致，防止再迁移时丢失）
    legacy = sandbox.setdefault("commandSafety", {})
    legacy["allow"] = [{"prefix": list(r["prefix"])} for r in rules if r["action"] == "allow"]
    legacy["ask"] = [{"prefix": list(r["prefix"])} for r in rules if r["action"] == "ask"]


# ---------------- ① ② 引擎 Bash 权限 ----------------
def read_perms(data):
    out = []
    p = data.get("permissions")
    if not isinstance(p, dict):
        return out
    for act in ("allow", "ask", "deny"):
        arr = p.get(act)
        if not isinstance(arr, list):
            continue
        for it in arr:
            if isinstance(it, str):
                out.append({"rule": it, "action": act})
    return out


def write_perms(data, perms):
    p = data.setdefault("permissions", {})
    for act in ("allow", "ask", "deny"):
        p[act] = [e["rule"] for e in perms if e["action"] == act]


# ---------------- 规则跨栏转换 ----------------
def convert_rule(item, src_kind, dst_kind):
    """把一条规则从 src_kind 列表格式转换成 dst_kind 列表格式；无法转换返回 None。"""
    if src_kind == dst_kind:
        return dict(item) if isinstance(item, dict) else item
    if src_kind == "cmd_rules" and dst_kind == "perms":
        prefix = item.get("prefix") or []
        if not prefix:
            return None
        return {"rule": "Bash(%s:*)" % " ".join(prefix), "action": item.get("action", "allow")}
    if src_kind == "perms" and dst_kind == "cmd_rules":
        m = re.match(r"^Bash\((.+):\*\)$", item.get("rule", ""))
        if not m:
            return None
        return {"id": "", "prefix": m.group(1).split(), "action": item.get("action", "allow"),
                "source": "user", "reason": ""}
    return None


# ---------------- ③ IDE 自有设置库（state.vscdb → CodeBuddy.settings） ----------------
def ide_store_load():
    db = PATHS.get("ide_db") or DEFAULTS["ide_db"]
    if not db or not os.path.exists(db):
        return {}
    con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=8)
    try:
        row = con.execute("select value from ItemTable where key = ?", (IDE_STORE_KEY,)).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        return {}
    v = row[0]
    s = v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
    return json.loads(s)


def ide_store_save(doc):
    """写入 IDE 设置存储（先用 SQLite 备份接口做一份数据库备份）。"""
    db = PATHS.get("ide_db") or DEFAULTS["ide_db"]
    text = json.dumps(doc, ensure_ascii=False)
    json.loads(text)  # 自校验
    if not db or not os.path.isdir(os.path.dirname(db)):
        raise FileNotFoundError("未找到 IDE 数据库目录：" + str(db))
    bak = None
    if os.path.exists(db):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = db + ".bak-" + stamp
        n = 1
        while os.path.exists(bak):
            n += 1
            bak = db + ".bak-" + stamp + "-" + str(n)
        src = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=8)
        dst = sqlite3.connect(bak)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    con = sqlite3.connect(db, timeout=15)
    try:
        con.execute("insert or replace into ItemTable(key, value) values(?, ?)", (IDE_STORE_KEY, text))
        con.commit()
    finally:
        con.close()
    return bak


def read_ide_blacklist(data):
    a = data.get("autoApprovalSettings")
    v = a.get("customBlacklistCommands") if isinstance(a, dict) else None
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []


def write_ide_blacklist(data, items):
    a = _ensure_dict(data, "autoApprovalSettings")
    a["customBlacklistCommands"] = list(items)


def read_ide_categories(data):
    a = data.get("autoApprovalSettings")
    v = a.get("disabledSecurityCategories") if isinstance(a, dict) else None
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []


def write_ide_categories(data, cats):
    a = _ensure_dict(data, "autoApprovalSettings")
    a["disabledSecurityCategories"] = list(cats)


# ---------------- 自检 ----------------
def selftest():
    base = os.path.dirname(os.path.abspath(sys.argv[0]))
    outdir = os.path.join(base, "selftest-out")
    os.makedirs(outdir, exist_ok=True)
    lines = []
    errors = 0

    resolve_paths()
    lines.append("[INFO] 自动检测：" + json.dumps(
        {k: DETECTED.get(k) for k in ("wb", "cb", "ide_db")}, ensure_ascii=False))

    # 1) 三个 JSON 文件回环
    for path in (PATHS["wb"], PATHS["cb"], PATHS.get("ide_json")):
        try:
            if not path or not os.path.exists(path):
                lines.append("[SKIP] " + str(path) + " 不存在")
                continue
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()
            d1 = json.loads(original)
            roundtrip = json.dumps(d1, ensure_ascii=False, indent=2)
            ok = (json.loads(roundtrip) == d1)
            tag = os.path.basename(os.path.dirname(path)) or "root"
            with open(os.path.join(outdir, "roundtrip-" + tag + "-settings.json"), "w", encoding="utf-8") as f:
                f.write(roundtrip)
            lines.append(("[OK] " if ok else "[FAIL] ") + path + " 解析->回写->再解析 " + ("一致" if ok else "不一致"))
            if not ok:
                errors += 1
        except Exception as e:
            lines.append("[ERROR] " + str(path) + " : " + repr(e))
            errors += 1

    # 2) 桌面版变更模拟
    try:
        data = load_json(PATHS["wb"])
        rules = read_cmd_rules(data)
        rules.append({"id": "command-user-9999", "prefix": ["zzz-selftest-probe"],
                      "action": "allow", "source": "user", "reason": ""})
        write_cmd_rules(data, rules)
        perms = read_perms(data)
        perms.append({"rule": "Bash(zzz-selftest-probe:*)", "action": "allow"})
        write_perms(data, perms)
        mutated = json.dumps(data, ensure_ascii=False, indent=2)
        json.loads(mutated)
        with open(os.path.join(outdir, "mutated-workbuddy.json"), "w", encoding="utf-8") as f:
            f.write(mutated)
        lines.append("[OK] 桌面版变更模拟完成：规则 %d 条、权限 %d 条（含探针）" % (len(rules), len(perms)))
    except Exception as e:
        lines.append("[ERROR] 桌面版变更模拟失败: " + repr(e))
        errors += 1

    # 3) IDE 设置库读取 + 变更模拟（只写文件产物，不碰数据库）
    try:
        db = PATHS.get("ide_db")
        if not db or not os.path.exists(db):
            lines.append("[SKIP] IDE 数据库不存在：" + str(db))
        else:
            doc = ide_store_load()
            orig = json.dumps(doc, ensure_ascii=False, indent=2)
            with open(os.path.join(outdir, "original-ide-store.json"), "w", encoding="utf-8") as f:
                f.write(orig)
            bl = read_ide_blacklist(doc)
            bl.append("zzz-selftest-probe")
            write_ide_blacklist(doc, bl)
            cats = read_ide_categories(doc)
            if "custom" not in cats:
                cats.append("custom")
            write_ide_categories(doc, cats)
            chat = _ensure_dict(doc, "chat")
            chat["autoRunMode"] = chat.get("autoRunMode") or "RunEverything"
            mutated = json.dumps(doc, ensure_ascii=False, indent=2)
            json.loads(mutated)
            with open(os.path.join(outdir, "mutated-ide-store.json"), "w", encoding="utf-8") as f:
                f.write(mutated)
            lines.append("[OK] IDE 设置库读取成功（顶层键 %d 个）；变更模拟：黑名单 %d 条、类别 %d 个"
                         % (len(doc.keys()), len(bl), len(cats)))
    except Exception as e:
        lines.append("[ERROR] IDE 设置库处理失败: " + repr(e))
        errors += 1

    # 4) 跨栏转换自检
    try:
        c1 = convert_rule({"id": "x", "prefix": ["rm"], "action": "allow"}, "cmd_rules", "perms")
        assert c1 == {"rule": "Bash(rm:*)", "action": "allow"}, c1
        c2 = convert_rule({"rule": "Bash(git status:*)", "action": "ask"}, "perms", "cmd_rules")
        assert c2 and c2["prefix"] == ["git", "status"] and c2["action"] == "ask", c2
        c3 = convert_rule({"rule": "Read(./.env)", "action": "allow"}, "perms", "cmd_rules")
        assert c3 is None, c3
        lines.append("[OK] 跨栏转换自检通过（cmd_rules<->perms）")
    except Exception as e:
        lines.append("[ERROR] 跨栏转换自检失败: " + repr(e))
        errors += 1

    lines.append("SELFTEST RESULT: " + ("PASS" if errors == 0 else "FAIL(%d)" % errors))
    with open(os.path.join(outdir, "selftest-report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if errors == 0 else 1


def detect_running():
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="ignore", timeout=8,
                             creationflags=flags).stdout.lower()
        found = []
        for key, label in (("workbuddy.exe", "WorkBuddy 桌面版"),
                           ("codebuddy cn.exe", "CodeBuddy CN IDE"),
                           ("codebuddy", "CodeBuddy"),
                           ("sandbox-center", "sandbox-center")):
            if key in out:
                found.append(label)
        return found
    except Exception:
        return []


HELP_TEXT = """【这个工具是干什么的】
图形化管理三个入口的命令「放行 / 询问 / 拦截」名单，三栏并排、各自实时读取（非官方工具，仅读写本地配置）。

【① WorkBuddy 桌面版】~/.workbuddy/settings.json
  · 沙盒命令规则：允许 / 询问 / 拒绝（允许=直接执行、询问=每次确认、拒绝=直接拦截）
  · Bash 权限：Bash(rm:*) 这类规则
【② CodeBuddy 命令行】~/.codebuddy/settings.json
  · Bash 权限：同上（命令行版的主要名单）
【③ CodeBuddy CN IDE】它自己的设置库（%APPDATA% 下 *CodeBuddy* 目录里的 state.vscdb → CodeBuddy.settings）
  · 命令黑名单：每项是对整条命令匹配的正则，命中会被拦截或需要确认
  · 安全检查类别：勾选 = 禁用该类内置检查（fileDelete 管删除等，共 13 类）
  · 运行模式：RunEverything=命令/MCP/文件写入不再弹确认；AskEveryTime=每次都询问

【配置位置（「设置…」按钮）】
· 启动时自动检测三个配置的位置；检测不到的项会显示为"未检测到"。
· 「设置…」里可以为每一项手动指定路径（优先于自动检测，作为保底），留空 = 用自动检测结果。
· 自动检测规则：①② 检查用户目录下的固定位置；③ 在 %APPDATA% 下查找 *CodeBuddy* 目录里
  的最多候选 state.vscdb，并校验其中包含 CodeBuddy.settings 键。

【重要：IDE 的生效方式】
· 这个 IDE 只在「启动时」读取它自己的设置库，所以：改完 → 完全退出 IDE → 保存 → 重新打开 IDE 即生效。
· 保存前请先退出 IDE（工具会提醒）；保存时自动为它的数据库做一份备份（state.vscdb.bak-时间戳）。
· 也可以在 IDE 自己界面的设置里直接改（找到带命令安全类别开关的那一页，是即时生效的）。

【命令怎么写（① ② 栏）】
· 按空格分词；只写命令名 = 该命令的所有用法（例：rm）；写 rm -rf = 仅匹配 rm -rf 开头。
· 列表顺序即保存顺序，可用上下移调整；建议把「拒绝」放最后。

【小技巧】
· 每个列表上方有「过滤」框，输入关键字即可实时筛选。
· 在列表里点右键：可快速把动作改成 允许/询问/拒绝，或用「复制到…」把这条规则复制到其他栏
  （①↔② 之间会自动转换格式：命令规则 ↔ Bash(...) 写法；重复的会自动跳过）。
· 窗口大小和位置会自动记住。
· 自动刷新只在内容"真的变了"的时候才更新，不会打断你正在看的列表。

【实时读取】
· 每 5 秒自动读取一次三处配置（有未保存修改的那一栏会暂停更新，不会覆盖你的编辑）。

【其他生效方式】
· ① 桌面版：重启 WorkBuddy 后生效。
· ② 命令行：下次启动新会话即生效。

【安全网】
· 每次保存自动备份；写入前 JSON 自校验，失败不覆盖原文件；只改名单相关字段，其他内容原样保留。
"""


# ---------------- 界面 ----------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    MODE_LABELS = [
        ("RunEverything", "RunEverything（运行所有内容：命令 / MCP / 文件写入都不再弹确认）"),
        ("AskEveryTime", "AskEveryTime（每次都询问）"),
    ]

    # ==================== 编辑对话框 ====================
    class RuleDialog(tk.Toplevel):
        def __init__(self, parent, rule=None):
            super().__init__(parent)
            self.title("添加命令规则" if rule is None else "编辑命令规则")
            self.resizable(False, False)
            self.transient(parent)
            self.result = None
            frm = ttk.Frame(self, padding=14)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="命令（按空格分词）：").grid(row=0, column=0, sticky="w")
            self.var_cmd = tk.StringVar(value=(" ".join(rule["prefix"]) if rule else ""))
            ttk.Entry(frm, textvariable=self.var_cmd, width=52).grid(row=1, column=0, pady=(2, 0), sticky="we")
            ttk.Label(frm, text="只写命令名 = 该命令的所有用法（例：rm）；写 rm -rf = 仅匹配 rm -rf 开头的用法。",
                      foreground="#777777").grid(row=2, column=0, sticky="w", pady=(2, 8))
            ttk.Label(frm, text="动作：").grid(row=3, column=0, sticky="w")
            default_choice = ACTION_CHOICES[0]
            if rule and rule.get("action") in ("allow", "ask", "deny"):
                default_choice = ACTION_CHOICES[("allow", "ask", "deny").index(rule["action"])]
            self.var_action = tk.StringVar(value=default_choice)
            ttk.Combobox(frm, textvariable=self.var_action, values=ACTION_CHOICES,
                         state="readonly", width=30).grid(row=4, column=0, sticky="w", pady=(2, 8))
            ttk.Label(frm, text="备注（可选，仅询问/拒绝时写入）：").grid(row=5, column=0, sticky="w")
            self.var_reason = tk.StringVar(value=(rule["reason"] if rule else ""))
            ttk.Entry(frm, textvariable=self.var_reason, width=52).grid(row=6, column=0, pady=(2, 10), sticky="we")
            btns = ttk.Frame(frm)
            btns.grid(row=7, column=0, sticky="e")
            ttk.Button(btns, text="确定", command=self.on_ok, width=10).pack(side="left", padx=4)
            ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="left")
            self.bind("<Return>", lambda e: self.on_ok())
            self.bind("<Escape>", lambda e: self.destroy())
            self.grab_set()
            self.wait_window(self)

        def on_ok(self):
            raw = self.var_cmd.get().strip()
            if not raw:
                messagebox.showwarning("提示", "请填写命令。", parent=self)
                return
            toks = raw.split()
            for t in toks:
                if '"' in t or "'" in t:
                    messagebox.showwarning("提示", "命令里不用带引号，直接用空格分词即可。", parent=self)
                    return
            self.result = {"prefix": toks, "action": CHOICE_ACTION[self.var_action.get()],
                           "reason": self.var_reason.get().strip()}
            self.destroy()

    class PermDialog(tk.Toplevel):
        def __init__(self, parent, perm=None):
            super().__init__(parent)
            self.title("添加权限规则" if perm is None else "编辑权限规则")
            self.resizable(False, False)
            self.transient(parent)
            self.result = None
            frm = ttk.Frame(self, padding=14)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="规则内容：").grid(row=0, column=0, sticky="w")
            self.var_rule = tk.StringVar(value=(perm["rule"] if perm else ""))
            ttk.Entry(frm, textvariable=self.var_rule, width=52).grid(row=1, column=0, pady=(2, 0), sticky="we")
            ttk.Label(frm, text="示例：Bash(rm:*)   Bash(python:*)   Bash(git status:*)   Read(./.env)   WebFetch",
                      foreground="#777777").grid(row=2, column=0, sticky="w", pady=(2, 8))
            ttk.Label(frm, text="动作：").grid(row=3, column=0, sticky="w")
            default_choice = ACTION_CHOICES[0]
            if perm and perm.get("action") in ("allow", "ask", "deny"):
                default_choice = ACTION_CHOICES[("allow", "ask", "deny").index(perm["action"])]
            self.var_action = tk.StringVar(value=default_choice)
            ttk.Combobox(frm, textvariable=self.var_action, values=ACTION_CHOICES,
                         state="readonly", width=30).grid(row=4, column=0, sticky="w", pady=(2, 10))
            btns = ttk.Frame(frm)
            btns.grid(row=5, column=0, sticky="e")
            ttk.Button(btns, text="确定", command=self.on_ok, width=10).pack(side="left", padx=4)
            ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="left")
            self.bind("<Return>", lambda e: self.on_ok())
            self.bind("<Escape>", lambda e: self.destroy())
            self.grab_set()
            self.wait_window(self)

        def on_ok(self):
            t = self.var_rule.get().strip()
            if not t:
                messagebox.showwarning("提示", "请填写规则内容。", parent=self)
                return
            self.result = {"rule": t, "action": CHOICE_ACTION[self.var_action.get()]}
            self.destroy()

    class TextDialog(tk.Toplevel):
        def __init__(self, parent, title, label, value="", hint=""):
            super().__init__(parent)
            self.title(title)
            self.resizable(False, False)
            self.transient(parent)
            self.result = None
            frm = ttk.Frame(self, padding=14)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=label).grid(row=0, column=0, sticky="w")
            self.var = tk.StringVar(value=value)
            ttk.Entry(frm, textvariable=self.var, width=56).grid(row=1, column=0, pady=(2, 0), sticky="we")
            if hint:
                ttk.Label(frm, text=hint, foreground="#777777").grid(row=2, column=0, sticky="w", pady=(2, 8))
            btns = ttk.Frame(frm)
            btns.grid(row=3, column=0, sticky="e", pady=(8, 0))
            ttk.Button(btns, text="确定", command=self.on_ok, width=10).pack(side="left", padx=4)
            ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="left")
            self.bind("<Return>", lambda e: self.on_ok())
            self.bind("<Escape>", lambda e: self.destroy())
            self.grab_set()
            self.wait_window(self)

        def on_ok(self):
            t = self.var.get().strip()
            if not t:
                messagebox.showwarning("提示", "请填写内容。", parent=self)
                return
            self.result = t
            self.destroy()

    # ==================== 设置对话框（配置位置） ====================
    class SettingsDialog(tk.Toplevel):
        ROWS = [
            ("wb", "① WorkBuddy 桌面版配置（settings.json）", False),
            ("cb", "② CodeBuddy 命令行配置（settings.json）", False),
            ("ide_db", "③ CodeBuddy CN IDE 数据库（state.vscdb）", True),
        ]

        def __init__(self, app):
            super().__init__(app)
            self.app = app
            self.title("设置 · 配置位置")
            self.resizable(False, False)
            self.transient(app)
            self.geometry("780x430")

            frm = ttk.Frame(self, padding=14)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="启动时会自动检测配置位置；下面「手动指定」优先于自动检测（保底），留空 = 用自动检测结果。",
                      foreground="#777777", wraplength=730).pack(anchor="w", pady=(0, 6))

            cfg = load_config()
            ov = cfg.get("overrides") or {}
            self.vars = {}
            self.det_labels = {}

            for key, title, _is_db in SettingsDialog.ROWS:
                box = ttk.LabelFrame(frm, text=title, padding=8)
                box.pack(fill="x", pady=4)
                self.det_labels[key] = ttk.Label(box, text="", foreground="#777777", wraplength=710)
                self.det_labels[key].pack(anchor="w")
                row = ttk.Frame(box)
                row.pack(fill="x", pady=(4, 0))
                ttk.Label(row, text="手动指定：").pack(side="left")
                var = tk.StringVar(value=str(ov.get(key) or ""))
                self.vars[key] = var
                ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
                ttk.Button(row, text="浏览…", width=8,
                           command=lambda k=key, d=_is_db: self.browse(k, d)).pack(side="left", padx=(4, 0))

            self._refresh_detected()

            btns = ttk.Frame(frm)
            btns.pack(fill="x", pady=(10, 0))
            ttk.Button(btns, text="重新自动检测", command=self.re_detect, width=14).pack(side="left")
            ttk.Button(btns, text="保存并应用", command=self.on_save, width=14).pack(side="right")
            ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="right", padx=6)
            self.bind("<Escape>", lambda e: self.destroy())
            self.grab_set()
            self.wait_window(self)

        def _fmt(self, key):
            d = DETECTED.get(key)
            if d and os.path.exists(d):
                return "自动检测：%s  ✓" % d
            return "自动检测：未找到（可以在下方手动指定）"

        def _refresh_detected(self):
            for key, _title, _is_db in SettingsDialog.ROWS:
                self.det_labels[key].configure(text=self._fmt(key))

        def re_detect(self):
            global DETECTED
            DETECTED = autodetect_paths()
            self._refresh_detected()

        def browse(self, key, is_db):
            from tkinter import filedialog
            cur = self.vars[key].get().strip() or (DETECTED.get(key) or "")
            init_dir = os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur)) else HOME
            if is_db:
                ft = [("CodeBuddy 数据库 state.vscdb", "*.vscdb"), ("所有文件", "*.*")]
            else:
                ft = [("settings.json", "settings.json"), ("JSON 文件", "*.json"), ("所有文件", "*.*")]
            p = filedialog.askopenfilename(parent=self, title="选择文件", initialdir=init_dir, filetypes=ft)
            if p:
                self.vars[key].set(p)

        def on_save(self):
            cfg = load_config()
            ov = {}
            for key, _title, _is_db in SettingsDialog.ROWS:
                v = self.vars[key].get().strip()
                if v:
                    ov[key] = v
            cfg["overrides"] = ov
            try:
                save_config(cfg)
            except Exception as e:
                messagebox.showerror("保存失败", "写入设置失败：%r" % (e,), parent=self)
                return
            resolve_paths()
            self.app.load_all()
            self.destroy()

    # ==================== 列表组件（树形） ====================
    KINDS = {
        "cmd_rules": dict(reader=read_cmd_rules, writer=write_cmd_rules, action_col=True, ordered=True),
        "perms": dict(reader=read_perms, writer=write_perms, action_col=True, ordered=True),
        "ide_blacklist": dict(reader=read_ide_blacklist, writer=write_ide_blacklist, action_col=False, ordered=False),
    }

    class ListPane(ttk.Frame):
        def __init__(self, master, app, kind, title, hint=""):
            super().__init__(master)
            self.app = app
            self.kind = kind
            self.list_name = title
            self.k = KINDS[kind]
            self.items = []
            self.dirty = False

            if hint:
                ttk.Label(self, text=hint, foreground="#777777", wraplength=360).pack(anchor="w", pady=(6, 0))

            filt = ttk.Frame(self)
            filt.pack(fill="x", pady=(6, 0))
            ttk.Label(filt, text="过滤：").pack(side="left")
            self.filter_var = tk.StringVar()
            ttk.Entry(filt, textvariable=self.filter_var).pack(side="left", fill="x", expand=True)
            self.filter_var.trace_add("write", lambda *a: self.render())

            body = ttk.Frame(self)
            body.pack(fill="both", expand=True, pady=(4, 0))

            col = ttk.Frame(body)
            col.pack(side="right", fill="y")
            ttk.Button(col, text="添加…", command=self.add_item, width=9).pack(pady=2)
            ttk.Button(col, text="编辑…", command=self.edit_item, width=9).pack(pady=2)
            ttk.Button(col, text="删除", command=self.del_item, width=9).pack(pady=2)
            if self.k["ordered"]:
                ttk.Button(col, text="上移", command=lambda: self.move_item(-1), width=9).pack(pady=2)
                ttk.Button(col, text="下移", command=lambda: self.move_item(1), width=9).pack(pady=2)

            cols = ("rule", "action") if self.k["action_col"] else ("rule",)
            self.tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
            widths = {"rule": 250, "action": 64}
            for c in cols:
                self.tree.heading(c, text={"rule": "规则", "action": "动作"}[c])
                self.tree.column(c, width=widths[c], stretch=(c == "rule"))
            for act, cfg in ROW_TAG_COLORS.items():
                self.tree.tag_configure(act, **cfg)
            self.tree.bind("<Double-1>", lambda e: self.edit_item())
            self.tree.bind("<Button-3>", self.popup_menu)
            self.tree.pack(side="left", fill="both", expand=True)

        # ---- 数据 ----
        def load_from(self, data):
            self.items = self.k["reader"](data)
            self.dirty = False

        def refresh_from(self, data):
            """自动刷新用：只有内容真的变了才更新（避免打断浏览/丢失选中）。"""
            new = self.k["reader"](data)
            if self.dirty or new == self.items:
                return
            self.items = new
            self.render()

        def is_dirty(self):
            return self.dirty

        def mark_clean(self):
            self.dirty = False

        def save_into(self, data):
            self.k["writer"](data, self.items)

        def count(self):
            return len(self.items)

        def render(self):
            self.tree.delete(*self.tree.get_children())
            needle = self.filter_var.get().strip().lower()
            for i, it in enumerate(self.items):
                if isinstance(it, str):
                    text = it
                    action = None
                else:
                    action = it.get("action", "allow")
                    text = " ".join(it["prefix"]) if self.kind == "cmd_rules" else it.get("rule", "")
                if needle and needle not in text.lower():
                    continue
                if action is None:
                    self.tree.insert("", "end", iid=str(i), values=(text,))
                    continue
                tag = action if action in ROW_TAG_COLORS else "allow"
                label = ACTION_CN.get(action, action)
                self.tree.insert("", "end", iid=str(i), values=(text, label), tags=(tag,))

        def selected_index(self):
            sel = self.tree.selection()
            return int(sel[0]) if sel else None

        # ---- 右键菜单 ----
        def popup_menu(self, event):
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
            idx = self.selected_index()
            if idx is None:
                return
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="编辑…", command=self.edit_item)
            menu.add_command(label="删除", command=self.del_item)
            if self.k["action_col"]:
                menu.add_separator()
                for act in ("allow", "ask", "deny"):
                    menu.add_command(label="设为" + ACTION_CN[act],
                                     command=lambda a=act: self.set_action(a))
            copies = self.app.copy_options(self)
            if copies:
                menu.add_separator()
                sub = tk.Menu(menu, tearoff=0)
                for label, fn in copies:
                    sub.add_command(label=label, command=fn)
                menu.add_cascade(label="复制到…", menu=sub)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def set_action(self, action):
            idx = self.selected_index()
            if idx is None or not self.k["action_col"]:
                return
            self.items[idx]["action"] = action
            self.dirty = True
            self.render()
            self.tree.selection_set(str(idx))
            self.app.mark_dirty_hint()

        # ---- 操作 ----
        def add_item(self):
            if self.kind == "cmd_rules":
                dlg = RuleDialog(self.app)
                if not dlg.result:
                    return
                item = {"id": self.app.next_rule_id(), "prefix": dlg.result["prefix"],
                        "action": dlg.result["action"], "source": "user", "reason": dlg.result["reason"]}
            elif self.kind == "perms":
                dlg = PermDialog(self.app)
                if not dlg.result:
                    return
                item = dlg.result
            else:
                dlg = TextDialog(self.app, "添加黑名单命令", "正则表达式（匹配整条命令）：", "",
                                 hint="命中该正则的命令会被拦截或需要确认；例如：rm\\s+-rf\\s+/")
                if not dlg.result:
                    return
                if not self.check_regex(dlg.result):
                    return
                item = dlg.result
            self.filter_var.set("")
            self.items.append(item)
            self.dirty = True
            self.render()
            self.tree.selection_set(str(len(self.items) - 1))
            self.tree.see(str(len(self.items) - 1))
            self.app.mark_dirty_hint()

        def edit_item(self):
            idx = self.selected_index()
            if idx is None:
                messagebox.showinfo("提示", "先选中一条。", parent=self.app)
                return
            cur = self.items[idx]
            if self.kind == "cmd_rules":
                dlg = RuleDialog(self.app, cur)
                if not dlg.result:
                    return
                cur["prefix"] = dlg.result["prefix"]
                cur["action"] = dlg.result["action"]
                cur["reason"] = dlg.result["reason"]
                if cur.get("source") != "user":
                    cur["source"] = "user"
            elif self.kind == "perms":
                dlg = PermDialog(self.app, cur)
                if not dlg.result:
                    return
                self.items[idx] = dlg.result
            else:
                dlg = TextDialog(self.app, "编辑黑名单命令", "正则表达式（匹配整条命令）：", cur)
                if not dlg.result:
                    return
                if not self.check_regex(dlg.result):
                    return
                self.items[idx] = dlg.result
            self.dirty = True
            self.render()
            self.tree.selection_set(str(idx))
            self.app.mark_dirty_hint()

        def del_item(self):
            idx = self.selected_index()
            if idx is None:
                messagebox.showinfo("提示", "先选中一条。", parent=self.app)
                return
            cur = self.items[idx]
            text = cur if isinstance(cur, str) else (" ".join(cur["prefix"]) if self.kind == "cmd_rules" else cur.get("rule", ""))
            if not messagebox.askyesno("删除确认", "确定删除「%s」吗？" % text, parent=self.app):
                return
            self.items.pop(idx)
            self.dirty = True
            self.render()
            self.app.mark_dirty_hint()

        def move_item(self, delta):
            idx = self.selected_index()
            if idx is None:
                messagebox.showinfo("提示", "先选中一条。", parent=self.app)
                return
            j = idx + delta
            if j < 0 or j >= len(self.items):
                return
            self.items[idx], self.items[j] = self.items[j], self.items[idx]
            self.dirty = True
            self.render()
            self.tree.selection_set(str(j))
            self.app.mark_dirty_hint()

        def check_regex(self, pattern):
            try:
                re.compile(pattern)
                return True
            except Exception as e:
                return messagebox.askyesno("正则校验",
                                           "这个正则 Python 侧校验没通过（%s）。\n可能只是语法差异，仍要保存吗？" % e,
                                           parent=self.app)

    # ==================== IDE：安全检查类别 ====================
    class CategoryPane(ttk.Frame):
        def __init__(self, master, app):
            super().__init__(master)
            self.app = app
            self.vars = {}
            self.extra = []
            self.snapshot = ()
            ttk.Label(self, text="勾选 = 禁用该类内置安全检查（该类的拦截/确认会被跳过）：",
                      foreground="#777777", wraplength=370).pack(anchor="w", pady=(6, 4))
            grid = ttk.Frame(self)
            grid.pack(anchor="w", fill="x")
            for i, (key, cn) in enumerate(CATEGORIES):
                var = tk.BooleanVar(value=False)
                self.vars[key] = var
                cb = ttk.Checkbutton(grid, text="%s（%s）" % (key, cn), variable=var,
                                     command=self.app.mark_dirty_hint)
                cb.grid(row=i % 7, column=i // 7, sticky="w", padx=(0, 10), pady=1)
            self.lbl_extra = ttk.Label(self, text="", foreground="#777777", wraplength=370)
            self.lbl_extra.pack(anchor="w", pady=(6, 0))
            ttk.Label(self, text="提示：不想让删除操作被拦，就勾上 fileDelete。\n（改完记得按顶部说明：退出 IDE → 保存 → 重开 IDE）",
                      foreground="#C05000", wraplength=370, justify="left").pack(anchor="w", pady=(6, 0))

        def load_from(self, data):
            cats = read_ide_categories(data)
            known = set(k for k, _ in CATEGORIES)
            self.extra = [c for c in cats if c not in known]
            for key, var in self.vars.items():
                var.set(key in cats)
            self.snapshot = tuple(sorted(cats))
            if self.extra:
                self.lbl_extra.configure(text="（库里还有未知类别 %s，将原样保留）" % "、".join(self.extra))
            else:
                self.lbl_extra.configure(text="")

        def refresh_from(self, data):
            cats = read_ide_categories(data)
            if tuple(sorted(cats)) == self.snapshot:
                return
            self.load_from(data)

        def current_cats(self):
            out = [k for k, var in self.vars.items() if var.get()]
            out.extend(self.extra)
            return out

        def is_dirty(self):
            return tuple(sorted(self.current_cats())) != self.snapshot

        def mark_clean(self):
            self.snapshot = tuple(sorted(self.current_cats()))

        def save_into(self, data):
            write_ide_categories(data, self.current_cats())

        def render(self):
            pass

        def count(self):
            return None

    # ==================== IDE：运行模式 ====================
    class SwitchPane(ttk.Frame):
        def __init__(self, master, app):
            super().__init__(master)
            self.app = app
            self.snapshot = {}
            self._loading = False
            self.var_mode = tk.StringVar(value=MODE_LABELS[0][1])
            self._labels = [label for _, label in MODE_LABELS]

            ttk.Label(self, text="运行模式（写入 IDE 自有设置库）：", foreground="#777777").pack(anchor="w", pady=(6, 4))
            grid = ttk.Frame(self)
            grid.pack(anchor="w", fill="x")
            ttk.Label(grid, text="自动运行模式").grid(row=0, column=0, sticky="w", pady=(4, 0))
            self.cmb = ttk.Combobox(grid, textvariable=self.var_mode, values=self._labels,
                                    state="readonly", width=46)
            self.cmb.grid(row=1, column=0, sticky="w")
            ttk.Label(self, text="RunEverything：命令 / MCP / 文件写入都不再弹确认；\nAskEveryTime：每次都询问。黑名单与未禁用的安全检查仍生效。",
                      foreground="#C05000", wraplength=370, justify="left").pack(anchor="w", pady=(10, 0))
            self.var_mode.trace_add("write", lambda *a: self._changed())

        def _changed(self):
            if not self._loading:
                self.app.mark_dirty_hint()

        def load_from(self, data):
            self._loading = True
            try:
                chat = data.get("chat")
                cur = chat.get("autoRunMode") if isinstance(chat, dict) else None
                label = None
                for val, lb in MODE_LABELS:
                    if val == cur:
                        label = lb
                        break
                if label is None and isinstance(cur, str) and cur:
                    label = cur  # 未知取值原样保留
                    if label not in self.cmb["values"]:
                        self.cmb["values"] = list(self._labels) + [label]
                if label is None:
                    label = MODE_LABELS[0][1]
                self.var_mode.set(label)
                self.snapshot = self._values()
            finally:
                self._loading = False

        def refresh_from(self, data):
            chat = data.get("chat")
            cur = chat.get("autoRunMode") if isinstance(chat, dict) else None
            if cur == self.snapshot.get("autoRunMode"):
                return
            self.load_from(data)

        def _values(self):
            label = self.var_mode.get()
            val = None
            for v, lb in MODE_LABELS:
                if lb == label:
                    val = v
                    break
            if val is None:
                val = label  # 未知取值原样保留
            return {"autoRunMode": val}

        def validate(self):
            return None

        def is_dirty(self):
            return self._values() != self.snapshot

        def mark_clean(self):
            self.snapshot = self._values()

        def save_into(self, data):
            chat = _ensure_dict(data, "chat")
            chat["autoRunMode"] = self._values()["autoRunMode"]

        def render(self):
            pass

        def count(self):
            return None

    # ==================== 一栏（一个入口） ====================
    class TargetPane(ttk.LabelFrame):
        def __init__(self, master, app, spec):
            super().__init__(master, text=spec["title"], padding=8)
            self.app = app
            self.spec = spec
            self.comps = []
            self.lbl_path = ttk.Label(self, text="", foreground="#999999", wraplength=380)
            self.lbl_path.pack(anchor="w")
            self.lbl_time = ttk.Label(self, text="尚未读取", foreground="#777777")
            self.lbl_time.pack(anchor="w", pady=(0, 4))
            if spec.get("note"):
                ttk.Label(self, text=spec["note"], foreground="#C05000", wraplength=380,
                          justify="left").pack(anchor="w", pady=(0, 4))

            lists = spec.get("lists", [])
            specials = spec.get("specials", [])
            panes = []
            for lst in lists:
                panes.append(("list", lst))
            for sp in specials:
                panes.append(("special", sp))

            if len(panes) == 1 and panes[0][0] == "list":
                lst = panes[0][1]
                comp = ListPane(self, app, lst["kind"], lst["name"])
                comp.pack(fill="both", expand=True)
                self.comps.append(comp)
            else:
                nb = ttk.Notebook(self)
                nb.pack(fill="both", expand=True)
                for kind, item in panes:
                    if kind == "list":
                        comp = ListPane(nb, app, item["kind"], item["name"], hint=item.get("hint", ""))
                        nb.add(comp, text=" " + item["name"] + " ")
                    elif item == "ide_categories":
                        comp = CategoryPane(nb, app)
                        nb.add(comp, text=" 安全检查类别 ")
                    elif item == "ide_switches":
                        comp = SwitchPane(nb, app)
                        nb.add(comp, text=" 运行模式 ")
                    self.comps.append(comp)

        def _path(self):
            return PATHS.get(self.spec.get("path_key"), "") or ""

        def _load_data(self):
            if self.spec.get("store"):
                return ide_store_load()
            return load_json(self._path())

        def load(self):
            path = self._path()
            if path and os.path.exists(path):
                self.lbl_path.configure(text=path, foreground="#999999")
            elif path:
                self.lbl_path.configure(text=path + "（尚未生成/不存在）", foreground="#C05000")
            else:
                self.lbl_path.configure(text="（未检测到配置位置 —— 请点「设置…」手动指定）", foreground="#C05000")
            data = self._load_data()
            for c in self.comps:
                c.load_from(data)
            for c in self.comps:
                c.render()
            self.touch()

        def refresh(self):
            data = self._load_data()
            for c in self.comps:
                c.refresh_from(data)
            self.touch()

        def touch(self):
            total = 0
            has_count = False
            for c in self.comps:
                n = c.count()
                if n is not None:
                    total += n
                    has_count = True
            extra = (" · 共 %d 条" % total) if has_count else ""
            self.lbl_time.configure(text="已读取 %s%s" % (time.strftime("%H:%M:%S"), extra))

        def is_dirty(self):
            return any(c.is_dirty() for c in self.comps)

        def save(self):
            dirty = [c for c in self.comps if c.is_dirty()]
            if not dirty:
                return None
            for c in dirty:
                if hasattr(c, "validate"):
                    err = c.validate()
                    if err:
                        raise ValueError(err)

            if self.spec.get("store"):
                running = detect_running()
                if "CodeBuddy CN IDE" in running:
                    if not messagebox.askyesno(
                            "IDE 正在运行",
                            "检测到 CodeBuddy CN IDE 正在运行。\n\n"
                            "它只在启动时读取设置，而且可能把旧值写回，导致这次修改不生效或被覆盖。\n\n"
                            "建议：先完全退出 IDE → 再保存 → 保存后重新打开 IDE 即生效。\n\n"
                            "仍要继续保存吗？",
                            parent=self.app):
                        return False
                data = ide_store_load()
                for c in dirty:
                    c.save_into(data)
                bak = ide_store_save(data)
                for c in self.comps:
                    c.mark_clean()
                return bak

            path = self._path()
            if not path:
                raise ValueError("没有可写入的路径：请点「设置…」手动指定")
            data = load_json(path)  # 重新读取最新，只改脏的部分
            for c in dirty:
                c.save_into(data)
            bak = save_json(path, data)
            for c in self.comps:
                c.mark_clean()
            return bak

    # ==================== 主窗口 ====================
    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("命令名单管理器 · CommandGuard v3.0")
            self.geometry("1400x800")
            self.minsize(1180, 680)
            try:
                style = ttk.Style(self)
                if "vista" in style.theme_names():
                    style.theme_use("vista")
                style.configure(".", font=("Microsoft YaHei UI", 10))
                style.configure("Treeview", rowheight=25)
                style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
            except Exception:
                pass
            self.option_add("*Font", ("Microsoft YaHei UI", 10))

            # 记住窗口大小/位置
            try:
                st = load_config()
                g = st.get("geometry")
                if isinstance(g, str) and re.match(r"^\d+x\d+(?:[+-]\d+[+-]\d+)?$", g):
                    self.geometry(g)
            except Exception:
                pass

            self.auto_var = tk.BooleanVar(value=True)

            root = ttk.Frame(self, padding=(10, 8, 10, 8))
            root.pack(fill="both", expand=True)

            running = detect_running()
            hint = "每 5 秒自动读取一次；修改后点「保存全部」。列表里点右键有快捷操作；配置位置可在「设置…」里改。"
            if running:
                hint = "⚠ 正在运行：" + "、".join(running) + " —— 保存对应栏目之前，建议先退出对应的程序。"
            self.lbl_top = ttk.Label(root, text=hint, foreground=("#C05000" if running else "#777777"))
            self.lbl_top.pack(anchor="w", pady=(0, 6))

            grid = ttk.Frame(root)
            grid.pack(fill="both", expand=True)
            specs = [
                {"title": "① WorkBuddy 桌面版", "short": "桌面版", "path_key": "wb",
                 "lists": [{"kind": "cmd_rules", "name": "沙盒命令规则"},
                           {"kind": "perms", "name": "Bash 权限"}]},
                {"title": "② CodeBuddy 命令行", "short": "命令行版", "path_key": "cb",
                 "lists": [{"kind": "perms", "name": "Bash 权限"}]},
                {"title": "③ CodeBuddy CN IDE", "short": "CN IDE", "path_key": "ide_db", "store": True,
                 "note": "⚠ 它自己的设置库（已直连管理）：保存前请先完全退出 IDE；保存后重新打开 IDE 即生效。",
                 "lists": [{"kind": "ide_blacklist", "name": "命令黑名单"}],
                 "specials": ["ide_categories", "ide_switches"]},
            ]
            self.targets = []
            for i, spec in enumerate(specs):
                tp = TargetPane(grid, self, spec)
                tp.grid(row=0, column=i, sticky="nsew", padx=6, pady=2)
                grid.columnconfigure(i, weight=1, uniform="col")
                self.targets.append(tp)
            grid.rowconfigure(0, weight=1)

            bar = ttk.Frame(root)
            bar.pack(fill="x", pady=(8, 0))
            ttk.Button(bar, text="保存全部", command=self.save_all, width=14).pack(side="left")
            ttk.Button(bar, text="刷新（重新读取）", command=self.refresh_all, width=18).pack(side="left", padx=6)
            ttk.Button(bar, text="设置…", command=self.open_settings, width=10).pack(side="left")
            ttk.Button(bar, text="说明", command=self.show_help, width=10).pack(side="left", padx=6)
            ttk.Checkbutton(bar, text="自动刷新（5 秒）", variable=self.auto_var).pack(side="left", padx=10)
            self.lbl_status = ttk.Label(bar, text="", foreground="#333333")
            self.lbl_status.pack(side="left", padx=10)

            self.load_all()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(5000, self.tick)

        # ---- 载入与实时刷新 ----
        def load_all(self):
            problems = []
            for t in self.targets:
                try:
                    t.load()
                except Exception as e:
                    problems.append("%s：%s" % (t.spec["title"], e))
            if problems:
                self.set_status("有栏目读取失败：" + "；".join(problems))
            else:
                self.set_status("已读取三个入口的配置。")

        def refresh_all(self):
            if self.any_dirty() and not messagebox.askyesno(
                    "重新读取", "有未保存的修改，重新读取会丢弃它们。继续吗？", parent=self):
                return
            resolve_paths()
            self.load_all()

        def tick(self):
            if self.auto_var.get():
                for t in self.targets:
                    if t.is_dirty():
                        continue  # 有未保存修改的栏暂停刷新
                    try:
                        t.refresh()
                    except Exception:
                        pass
            self.after(5000, self.tick)

        def any_dirty(self):
            return any(t.is_dirty() for t in self.targets)

        def mark_dirty_hint(self):
            self.set_status("有未保存的修改 —— 点「保存全部」写入。")

        def set_status(self, s):
            self.lbl_status.configure(text=s)

        # ---- 跨栏复制 ----
        def copy_options(self, src):
            """给 src 列表里当前选中的那条规则，生成「复制到哪些栏」的选项。"""
            idx = src.selected_index()
            if idx is None:
                return []
            item = src.items[idx]
            if not isinstance(item, dict):
                return []  # 黑名单不参与复制
            opts = []
            for t in self.targets:
                for c in t.comps:
                    if not isinstance(c, ListPane) or c is src:
                        continue
                    if convert_rule(item, src.kind, c.kind) is None:
                        continue
                    label = "「%s · %s」" % (t.spec.get("short", t.spec["title"]), c.list_name)
                    opts.append((label, self._make_copy_fn(item, src.kind, c)))
            return opts

        def _make_copy_fn(self, item, src_kind, dst):
            def _do():
                self.do_copy(item, src_kind, dst)
            return _do

        def do_copy(self, item, src_kind, dst):
            conv = convert_rule(item, src_kind, dst.kind)
            if conv is None:
                self.set_status("这条规则没法自动转换到目标栏。")
                return
            if dst.kind == "cmd_rules" and isinstance(conv, dict) and not conv.get("id"):
                conv["id"] = self.next_rule_id()
            existing = set(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in dst.items)
            if json.dumps(conv, ensure_ascii=False, sort_keys=True) in existing:
                self.set_status("目标栏里已经有这条了，跳过。")
                return
            dst.items.append(conv)
            dst.dirty = True
            dst.render()
            self.set_status("已复制 1 条到「%s」——记得点「保存全部」。" % dst.list_name)

        # ---- 保存 ----
        def save_all(self):
            results = []
            skipped = []
            errors = []
            for t in self.targets:
                try:
                    bak = t.save()
                    if bak is False:
                        skipped.append("%s：已跳过（请先退出它再保存）" % t.spec["title"])
                    elif bak:
                        results.append("%s：已更新（备份 %s）" % (t.spec["title"], os.path.basename(bak)))
                except Exception as e:
                    errors.append("%s：%s" % (t.spec["title"], e))
            if errors:
                messagebox.showerror("保存失败", "\n".join(errors) +
                                     "\n\n若对应程序正在运行，请先退出它再试。", parent=self)
            if results:
                self.set_status("已保存 ✔ " + "；".join(results))
                extra = ("\n\n已跳过：\n" + "\n".join(skipped)) if skipped else ""
                messagebox.showinfo("保存成功",
                                    "已保存：\n\n" + "\n".join(results) + extra +
                                    "\n\n生效：桌面版 → 重启 WorkBuddy；命令行 → 下次新会话；IDE → 现在重新打开 IDE 即可（它的设置要重开才读）。",
                                    parent=self)
            elif skipped:
                self.set_status("；".join(skipped))
            elif not errors:
                self.set_status("没有需要保存的修改。")

        # ---- 杂项 ----
        def open_settings(self):
            SettingsDialog(self)

        def show_help(self):
            top = tk.Toplevel(self)
            top.title("说明 / 帮助")
            top.geometry("680x660")
            txt = tk.Text(top, wrap="word", relief="flat", background="#FFFFFF")
            txt.pack(fill="both", expand=True, padx=10, pady=10)
            txt.insert("1.0", HELP_TEXT)
            txt.configure(state="disabled")

        def _save_ui_state(self):
            try:
                cfg = load_config()
                cfg["geometry"] = self.geometry()
                save_config(cfg)
            except Exception:
                pass

        def on_close(self):
            if self.any_dirty():
                r = messagebox.askyesnocancel("退出", "还有未保存的修改，要先保存吗？", parent=self)
                if r is None:
                    return
                if r:
                    self.save_all()
                    if self.any_dirty():
                        return
            self._save_ui_state()
            self.destroy()

        # 供 ListPane 用：生成新规则 ID
        def next_rule_id(self):
            used = set()
            mx = 0
            for t in self.targets:
                for c in t.comps:
                    if isinstance(c, ListPane) and c.kind == "cmd_rules":
                        for r in c.items:
                            if isinstance(r, dict):
                                used.add(r.get("id", ""))
                                rid = r.get("id", "")
                                if rid.startswith("command-user-"):
                                    try:
                                        mx = max(mx, int(rid.split("-")[-1]))
                                    except ValueError:
                                        pass
            n = mx + 1
            while ("command-user-%d" % n) in used:
                n += 1
            return "command-user-%d" % n

    app = None
    try:
        app = App()
    except Exception:
        import traceback
        try:
            messagebox.showerror("CommandGuard 出错（界面初始化失败）", traceback.format_exc())
        except Exception:
            pass
        raise

    if "--smoke" in sys.argv:
        result = "SMOKE OK"
        try:
            app.update_idletasks()
            app.update()
            app.after(200, app.destroy)
            app.mainloop()
        except Exception:
            import traceback
            result = "SMOKE FAIL\n" + traceback.format_exc()
        try:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
            with open(os.path.join(base, "smoke-report.txt"), "w", encoding="utf-8") as f:
                f.write(result + "\n")
        except Exception:
            pass
        return

    try:
        app.mainloop()
    except Exception:
        import traceback
        try:
            messagebox.showerror("CommandGuard 出错", traceback.format_exc())
        except Exception:
            pass
        raise


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run_gui()


if __name__ == "__main__":
    main()
