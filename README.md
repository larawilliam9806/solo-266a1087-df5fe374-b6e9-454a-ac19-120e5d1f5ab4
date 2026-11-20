# 旧报纸价目表录入

上传单页 OCR CSV（框号、文字、矩形坐标），在页面上对照文字框划定行线与
**品名 / 规格 / 批发价** 三列，逐个人工核对跨格或出界的框，最后下载三列 CSV。

## 规则

- **自动归位**：文字框完整落在一格内（四边都在格线内，贴线也算）时自动进入该格。
- **待核**：跨格、出界、与任何格子都没有面积重叠的框进入待核。人工只能：
  - 选择一个与该框**有面积重叠**的格子（下拉框只列出这些格），或
  - 排除该框（文字为空或与价目表无关的内容）。
- **决定沿用**：每个待核框的人工决定会记下当时与之相交的全部格子坐标。
  改格线后重新计算，只有相交格集合完全相同（相交格及其边界都未变）的决定才沿用；
  其余自动回到待核（状态“待重新确认”）。改一条与某框无关的行线不会影响该框的决定。
- **格线无效**：竖线必须恰好 4 条、横线至少 2 条。提交无效格线会被拒绝，
  原来保存的布局与归位结果保持不变。
- **导出**：没有待核框且格线有效时才可下载。同格内的文字按**从上到下**、
  同一高度内**从左到右**拼接。输出 UTF-8 BOM 的 `品名,规格,批发价` CSV。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DATABASE=./pricing.sqlite3 flask --app run:app run --port 5000
# 或 python run.py
```

打开 <http://localhost:5000>。

## Docker Compose

```bash
docker compose up --build
```

- 默认映射 `5000:5000`，可用 `PORT=8080 docker compose up` 改端口。
- SQLite 数据保存在命名卷 `pricing-data`（容器内 `/data/pricing.sqlite3`）。
- 生产环境请设置 `SECRET_KEY`。

## OCR CSV 格式

表头可用中文或英文，列顺序不限：

```csv
框号,文字,x1,y1,x2,y2
1,白砂糖,120,80,260,110
2,一级,270,80,360,110
```

支持的其他写法：

| 形式 | 示例 |
| --- | --- |
| 矩形坐标单列 | `框号,文字,矩形坐标` → `1,白砂糖,"120,80,260,110"` |
| 英文表头 | `box,text,left,top,right,bottom` |
| 左上角 + 宽高 | `框号,文字,x,y,宽,高` |
| 无表头 | 按 `框号,文字,x1,y1,x2,y2` 读取 |

编码自动识别 UTF-8（含 BOM）、GB18030、Big5；分隔符支持逗号、分号、制表符。
单行坐标损坏会跳过并提示，不会导致整页失败。

## 测试

```bash
# 逻辑与接口测试（60 个）
python3 -m unittest discover -s tests

# 页面交互测试（jsdom，需先启动服务；会自行上传样例并断言归位与导出）
pip install flask gunicorn          # 若尚未安装
DATABASE=/tmp/ui.sqlite3 python3 -c "
from pricing import create_app
create_app({'DATABASE':'/tmp/ui.sqlite3'}).run(port=5057)"
npm install jsdom
node tests/ui_smoke.js http://127.0.0.1:5057
node tests/ui_lines.js http://127.0.0.1:5057
```

`sample/` 内有两份可直接上传的示例 OCR CSV。

## 代码结构

```
pricing/
  app.py        Flask 路由、状态组装、JSON API、导出
  layout.py     纯逻辑：格线网格、归位判定、决定有效性、阅读顺序、行拼接
  csvparse.py   OCR CSV 解析（多表头/多编码/单列坐标）
  db.py         SQLite 读写
  schema.sql    表结构
  templates/    任务列表 / 编辑页 / 错误页
  static/       页面样式与前端交互（SVG 画框、拖拽格线、归位操作）
sample/         示例 OCR CSV
tests/          Python 单元/集成测试 + jsdom 页面测试
```
