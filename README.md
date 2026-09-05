<div align="center">
  <img src="assets/AppIcon.png" width="128" alt="应用图标">
  <h1>联通 IBOS 批量查询助手</h1>
  <p><strong>macOS 屏幕级自动化批量查单工具 · 零注入 · 零接口劫持</strong></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Platform-macOS%2011%2B-lightgrey" alt="macOS 11+">
    <img src="https://img.shields.io/badge/Automation-PyAutoGUI-green" alt="PyAutoGUI">
  </p>
</div>

---

## 这是什么

一个在 macOS 上运行的桌面工具,用于在联通公众运营平台(IBOS)批量查询订单,并把结果自动解析成结构化 Excel。

**核心约束:只做外部操作。** 工具不向页面注入任何脚本、不改写或代理任何接口,而是复用你已登录的 Edge 会话,通过操作系统层面模拟真实鼠标点击与键盘输入来完成查询。这意味着它绕开了前端注入检测与 API 劫持检测两类风控——浏览器插件与 Playwright 方案均已被证明会触发风控。

> ⚠️ **合规提示**
> 本工具仅用于操作你本人拥有合法访问权限的内部业务系统。请遵守所在单位的 IT 使用规范,控制查询频率(工具已内置随机延时)。请勿用于未授权的数据抓取。

---

## 核心特性

| 类别 | 能力 |
|------|------|
| **查询方式** | 按姓名 / 按订单号 / 按订购号码(手机号)三种模式 |
| **自动化** | 输入 → 查询 → 另存为 MHTML → 下一单,全流程无人值守 |
| **断点续跑** | 进度写入 `进度.txt`,中断后可从上次位置继续(显示已完成的查询键) |
| **坐标自愈** | Edge 窗口被意外移动时,自动检测位移并平移全部坐标,无需重新标定 |
| **失败重试** | 单条保存失败自动完整重查;若文件其实已保存成功则自动识别跳过,绝不重复保存 |
| **保存校验** | 保存后校验文件大小与内容是否包含本次查询值,异常即告警 |
| **智能等待** | 开启屏幕录制权限并标定结果区域后,可改用画面变化检测,结果一出即继续 |
| **结果导出** | MHTML 离线多线程解析 → Excel(18 列业务模板 + 「查询报告」工作表) + 文本报告 |
| **界面** | macOS 原生风格置顶悬浮窗、实时日志、迷你控制条(运行时可拖动、可停止) |

---

## 工作原理

```
┌────────────────────┐                        ┌────────────────────┐
│  Python 悬浮窗      │   PyAutoGUI 系统级      │  Edge(已登录 IBOS)  │
│  (GUI 主线程)       │ ── 鼠标/键盘事件 ────▶  │  停留在查询页        │
│                    │ ◀── 只读观测 ──────────  │                    │
└────────────────────┘   AX 树 / CGWindowList  └────────────────────┘
                                                        │
                                                   另存为 MHTML
                                                        ▼
                                              ┌────────────────────┐
                                              │  导出结果/ (本机落盘) │
                                              └────────────────────┘
                                                        │
                                                   离线解析(多线程)
                                                        ▼
                                              ┌────────────────────┐
                                              │  查询结果汇总.xlsx   │
                                              └────────────────────┘
```

工具对浏览器的观测全部走 macOS 官方只读接口(Accessibility AX 树、Quartz CGWindowList),不读取页面 JavaScript 上下文,不触碰网络层。

---

## 环境要求

- **macOS 11.0+**
- **Python 3.10+**(推荐 python.org 官方 Framework 构建——非 Framework 版 Python 在 macOS 下无法为 Tk 窗口分配独立 Dock 图标)
- **Microsoft Edge**,已登录 IBOS 并停留在查询页

---

## 安装

```bash
git clone https://github.com/XINKEJU/ibos.git
cd ibos

# 建议先建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

首次运行直接使用内置默认配置;当你修改设置或完成坐标标定后,才会写入 `config.json`。若想提前定制,先复制模板:

```bash
cp config.example.json config.json
```

---

## 启动

### 方式一:双击 `.app`(推荐)

打开 `dist/IBOS查询助手.app`。程序坞、访达、⌘Tab 均显示专属图标。

> 若 macOS 提示「无法打开,因为它来自身份不明的开发者」:在访达中 **右键该 App → 打开**,确认一次即可。

### 方式二:命令行 / `.command`

```bash
python3 gui/app.py
# 或双击 启动查询工具.command
```

启动器按以下顺序探测 Python:Framework Python → 项目 `.venv` → 用户 venv → 系统 `python3`。

---

## 权限配置(首次使用必做)

| 权限 | 路径 | 用途 | 是否必需 |
|------|------|------|----------|
| **辅助功能** | 系统设置 → 隐私与安全性 → 辅助功能 | 模拟鼠标点击与键盘输入 | ✅ 必需,缺失则点击/输入完全无效 |
| **屏幕录制** | 系统设置 → 隐私与安全性 → 屏幕录制 | 保存面板检测、画面变化检测、截图 | ⚠️ 可选,开启后可用智能等待(每单省约 1 秒) |

**步骤**

1. 打开悬浮窗,点「**检查环境**」——会直接跳转到对应的系统设置面板并高亮状态。
2. 勾选运行工具的应用:
   - 用 `.app` 启动 → 勾选「IBOS查询助手」
   - 用 `.command` / 终端启动 → 勾选「终端」(Terminal)
3. **完全退出并重新启动工具**(权限变更需重启进程才生效)。

悬浮窗左上角的状态点会实时反映环境:🟢 权限齐全且 Edge 已运行 / 🟠 权限不全或 Edge 未启动。

---

## 坐标标定流程

坐标是按 Edge 窗口的**当前位置**标定的。首次使用、更换显示器、或调整窗口大小后都需要标定。

**前置条件**:Edge 已登录 IBOS 并停在查询页,窗口放在**主显示器**且位置固定。

点击悬浮窗「**标定坐标**」,进入全屏标定,依次点击:

| 步骤 | 目标 | 必需 | 说明 |
|------|------|------|------|
| ① | 姓名输入框中心 | ✅ | — |
| ② | 订单号输入框中心 | ⬜ | 位于姓名框上方;仅「按订单号查询」时用到,未标定时自动按偏移量推算 |
| ③ | 「查询」按钮中心 | ✅ | — |
| ④ | 结果中「详情」按钮中心 | ⬜ | 仅 `enable_detail=true` 时用到 |
| ⑤ | 「返回/关闭详情」按钮中心 | ⬜ | 详情在同一页面跳转时需要 |
| ⑥ | 结果区域左上角 + 右下角 | ⬜ | 两点确定区域;配合屏幕录制权限启用画面变化检测 |

`Enter` 跳过任意可选步骤。完成后坐标自动写入 `config.json`,并生成标定参考截图。

> 标定后若窗口被意外移动,工具会在运行时自动检测位移并平移坐标(日志提示 Δx/Δy),无需重新标定。但**窗口尺寸或显示器变更**必须重新标定。

---

## 使用流程

1. **选查询方式**:按姓名 / 按订单号 / 按订购号码。
2. **填查询列表**:在输入框粘贴(每行一条,`#` 开头为注释),或点「从文件…」选择 txt。
   - 按订单号:每行一个 16 位纯数字
   - 按订购号码:每行一个 11 位手机号
3. **点「开始查询」**。运行期间请勿移动鼠标或切换窗口,保持 Edge 在前台。悬浮窗自动最小化,屏幕左上角出现迷你控制条,可随时停止。
4. **结果自动落盘**:`导出结果/<姓名或订单号>__查询结果.mhtml`
5. **自动导出**:查询结束后自动解析全部页面,生成
   - `导出结果/查询结果汇总.xlsx`(每订单一行,含「查询报告」工作表)
   - `导出结果/查询报告_<时间戳>.txt`

### 导出列集

业务模板共 18 列:

```
日期 / 姓名 / 开卡人 / 团队长 / 归属地 / 学校 / 手机号 / 联系电话 / 订单编号 /
订单状态是否通过审核 / 运单号 / 短链 / 用户办理方式 / 是否新生 / 套餐 /
发出日期 / 激活日期 / 宽带
```

其中自动填充:日期←成交时间、姓名←联系人、手机号←号码、联系电话←联系人电话、订单编号←订单ID、订单状态←审核状态、运单号←物流单号、短链←短链详情(去重)、套餐←产品信息。其余列因订单数据本身不含,保持留空以便人工补录。

**按订单号查询时的两个特殊行为**(便于对账):

- 首列额外输出「查询订单号」标识列;Excel 行序 = 名单顺序。
- 查不到结果的订单**保留空行**(仅标识列有值),方便直接对照名单定位漏查项。

---

## 配置说明

`config.json` 常用项(完整模板见 `config.example.json`):

| 键 | 默认 | 说明 |
|----|------|------|
| `query_by` | `name` | 查询方式:`name`(按姓名) / `order`(按订单号) / `order_phone`(按订购号码) |
| `enable_detail` | `false` | 是否额外查询并保存详情页 |
| `detail_mode` | `new_tab` | 详情打开方式:`new_tab` / `same_page` / `dialog` |
| `wait_mode` | `auto` | `auto`(智能:有屏幕录制权限且已标定结果区域则画面检测,否则固定等待) / `fixed` / `screen_change` |
| `result_wait_s` | `[1.2, 1.8]` | 结果加载等待区间(秒);保存校验告警「未找到查询值」时调大 |
| `action_delay_s` | `[0.25, 0.55]` | 操作随机停顿区间(秒);调大更保守 |
| `auto_retry` | `1` | 单条失败自动重查次数 |
| `save_folder_method` | `goto_folder` | `goto_folder`(⌘⇧G 自动跳目录,推荐) / `remember` |
| `verify_saved` | `true` | 保存后校验文件内容 |
| `auto_export_excel` | `true` | 查询结束自动生成 Excel |
| `stop_on_error` | `true` | 单条失败(重试后)即停止 |
| `log_max_mb` | `5` | 日志超过该大小自动轮转为 `.old` |

---

## 目录结构

```
ibos/
├── gui/
│   ├── app.py            # 悬浮窗主界面(启动自检、标定入口、导出、日志)
│   └── macbtn.py         # Canvas 自绘 macOS 风格按钮(macOS 下 tk.Button 配色不可靠)
├── core/
│   ├── ibos_auto.py      # 自动化主流程
│   ├── mac_perms.py      # 系统权限/窗口/保存面板检测(只读观测)
│   ├── calibrate.py      # 全屏坐标标定
│   ├── exporter.py       # MHTML 离线解析 → Excel
│   ├── config.py         # 配置读写
│   ├── human.py          # 拟人化输入(随机延时、粘贴覆盖)
│   └── logger.py         # 线程安全日志 + 轮转
├── tools/make_icon.py    # 程序化生成应用图标
├── assets/               # 应用图标(png / icns)
├── dist/IBOS查询助手.app  # 可直接双击运行的 macOS 应用包
├── tests/                # 解析逻辑单元测试
├── config.example.json   # 脱敏配置模板
└── 导出结果/              # 运行时生成: MHTML + Excel(含个人信息, 已被 .gitignore 排除)
```

---

## 常见问题

<details>
<summary><strong>点击没反应 / 输入无效</strong></summary>

辅助功能权限未开启,或授权后未重启工具。点「检查环境」核对状态点。
</details>

<details>
<summary><strong>Dock 里显示的是 Python / 终端图标,不是应用图标</strong></summary>

当前使用的是非 Framework 版 Python。改用 python.org 官方安装包,或直接使用 `dist/IBOS查询助手.app` 启动。
</details>

<details>
<summary><strong>日志警告「未找到查询值」</strong></summary>

结果等待过短,保存的可能还是上一页或未加载完的页面。把 `result_wait_s` 调大(如 `[2.0, 2.8]`)。
</details>

<details>
<summary><strong>文件被保存到其他位置</strong></summary>

确认 `save_folder_method` 为 `goto_folder`。工具会在检测到文件落到别处时自动 ⌘⇧G 修正目录并重存。
</details>

<details>
<summary><strong>查询结果汇总.xlsx 无法写入 / 提示被占用</strong></summary>

该文件正被 Excel 或 WPS 打开。关闭后点「导出 Excel」重新生成。
</details>

<details>
<summary><strong>保存的网页没有图片</strong></summary>

Edge「另存为」格式选择导致。`.html + _files` 与 `.mhtml` 单文件格式工具均可识别,无需改动。
</details>

---

## 安全与隐私

- 工具不注入脚本、不代理接口,仅模拟人工操作。
- 所有查询数据留在本机,`导出结果/` 中的 MHTML 含客户姓名、手机号、证件号、地址等个人信息。
- 仓库的 `.gitignore` 已排除以下文件,**请勿强制提交**:

  ```
  名单.txt        客户姓名
  订单号.txt      客户订单号
  导出结果/       查询结果(含个人信息)
  运行日志.log    完整查询轨迹
  进度.txt        断点状态
  config.json     本机路径与标定坐标
  ```

  配置文件请通过 `config.example.json` 模板共享。

---

## 开发

```bash
# 运行解析逻辑单元测试(31 项,覆盖 MHTML 解析与 Excel 生成)
python3 -m unittest discover -s tests -v
# 或
python3 -m pytest tests/test_exporter.py -v

# 重新生成应用图标(产出 assets/AppIcon.png 与 .icns)
python3 tools/make_icon.py
```

---

## 许可

项目当前未指定开源许可。如需对外授权,请补充 `LICENSE` 文件。
