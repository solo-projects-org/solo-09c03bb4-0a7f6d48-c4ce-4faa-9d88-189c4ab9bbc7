# 公交清分数仓 · 字段口径调整影响分析

公交清分数仓要调整「换乘标志」「卡状态」等字段的口径，工程师需要先找出**需要复核的明细列**。
本工具用 [SQLGlot](https://github.com/tobymao/sqlglot) 解析 DuckDB SQL，
从选中的源表字段出发，沿模型血缘传播，给出：

- 受影响的**模型**与每个模型的**受影响输出列**；
- 每列**一条可回溯到源表字段的依赖路径**，逐跳标注是「值传递」还是「条件污染」，
  经过谓词子查询时逐层标明实际经过的 `IN` / `EXISTS`。

纯本地运行，**不需要连接数仓**；网页可一键载入清分样例。

## 分析口径

### 支持的 SQL 范围

- 普通 `SELECT`、**非递归** CTE（`WITH`）、`INNER JOIN`、`WHERE`；
- 禁止星号（`SELECT *` / `t.*`），所有列引用必须带表名或别名；
- 支持 `WHERE ... IN (SELECT ...)` / `WHERE EXISTS (SELECT ...)` 形式的**非关联**谓词子查询
  （含 `NOT IN` / `NOT EXISTS` 与多层嵌套）、FROM 派生表（需别名）；
  `IN` 仅支持**单列**形式（左操作数单列、子查询返回单列）；
  投影中允许任意函数、运算、`CASE`、类型转换等表达式；
- 不支持递归 CTE、关联子查询、SELECT 投影中的标量子查询、多列 `IN`、`UNION` 等集合运算、
  `GROUP BY`、窗口函数、`LEFT/RIGHT/FULL/CROSS JOIN`、逗号隐式连接、DML 等，
  遇到即报错指出所在模型并停止分析。

### 传播规则

1. **取值依赖按列引用传递**：输出表达式引用了某列，该列的污染传到这个输出列。
2. **条件污染整表扩散**：字段一旦进入某查询的 `JOIN ON`、`WHERE`，该查询的
   **全部输出列**都标记受影响。
3. **谓词子查询按语义判定**：
   - `EXISTS` 只看子查询**有没有记录**——其子查询的筛选条件（`WHERE`/`JOIN ON`，
     含嵌套谓词）受影响，外层查询的条件才算受影响；
     **仅出现在 `EXISTS` 返回列中的字段不影响外层查询**；
   - 单列 `IN` 还看**返回列的值**——子查询返回列或其筛选条件受影响，
     外层查询的条件都算受影响；
   - 嵌套谓词逐层归属：内层谓词子查询只属于直接包含它的那一层查询。
4. **跨模型只走真实读取**：下游模型实际 `FROM/JOIN` 读取了上游模型的哪些输出列，
   才接收对应污染；模型可读取上游模型，模型依赖必须是有向无环图。
5. 任一列引用无法解析（未知表/别名、源表或上游模型上不存在的列、未限定列名等）时，
   指出所在模型与具体位置并**停止分析**。

### 依赖路径

每个受影响输出列给出一条回溯到源表字段的路径，逐层标明实际经过的谓词：

- `IN` 步骤标明取子查询的哪一**返回列**；
- `EXISTS` 步骤注明**检查哪个子查询是否有记录**（`existence: true`，不写成取列）；
- 子查询内部条件受影响的步骤，`output` 为 `null`，前端展示为「〈筛选条件〉」。

## 目录结构

```
.
├── docker-compose.yml          # 一键启动
└── app/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                  # Flask 路由（/、/api/sample、/api/validate、/api/analyze）
    ├── lineage.py              # SQLGlot 解析、血缘构建、污染传播与路径构造
    ├── data/
    │   └── sample_clearing.json  # 清分样例（3 源表 + 7 模型，含 CTE、EXISTS/IN 与嵌套谓词）
    ├── templates/
    │   └── index.html          # 单页前端（无外部 CDN 依赖）
    └── tests/
        ├── test_lineage.py     # 传播规则、谓词语义与错误处理测试
        └── test_api.py         # Flask 路由与样例端到端测试
```

## 用 Docker Compose 启动

```bash
docker compose up --build
```

浏览器打开 <http://localhost:8000>：

1. 点击「载入清分样例」，或上传自己的 JSON；
2. 在左侧选择源表与待调整字段，例如：
   - `ods_card_info.status`（卡状态）：`ads_status_probe_settlement` 中它只出现在
     `EXISTS` 子查询的返回列里，**不会**误报；`ads_active_card_settlement` /
     `ads_city_in_settlement` 中它参与嵌套筛选，全部输出列被标出；
   - `ods_card_transaction.transfer_flag`（换乘标志）：沿 CTE 链路传播，并在
     `ads_nested_exists_in_settlement` 中展示 `IN → EXISTS → 主查询` 的嵌套谓词路径；
3. 点击「分析受影响模型列」，右侧展示受影响模型、输出列与逐跳依赖路径。

停止：`docker compose down`。

## 输入 JSON 格式

```json
{
  "source_tables": [
    { "table": "ods_card_transaction",
      "columns": ["transaction_id", "card_id", "transfer_flag"] }
  ],
  "models": [
    { "name": "dwd_card_transaction_detail",
      "description": "可选说明",
      "sql": "SELECT t.transaction_id AS transaction_id, t.transfer_flag AS transfer_flag FROM ods_card_transaction t WHERE t.transfer_flag = 0" }
  ]
}
```

- `source_tables[].table` 为源表名，`columns` 为该表全部字段；
- `models[].name` 为模型名，模型 SQL 中 `FROM/JOIN` 同名关系即视为读取该上游模型；
- 其余名字按源表处理，必须在 `source_tables` 中声明。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sample` | 返回内置清分样例 JSON |
| POST | `/api/validate` | 校验 JSON 与全部 SQL，返回源表字段清单和模型读取关系 |
| POST | `/api/analyze?table=<源表>&column=<字段>` | 返回受影响模型、输出列与依赖路径 |

错误统一返回 HTTP 400 与 `{"ok": false, "error": "中文原因（含模型名）"}`。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin
pip install -r app/requirements.txt
python app/app.py            # 监听 8000
python app/tests/test_lineage.py   # 血缘传播与谓词语义测试
python app/tests/test_api.py       # API 与样例端到端测试
```
