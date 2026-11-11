"""Flask API 层测试：路由、错误码与样例端到端。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


client = app.test_client()
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "sample_clearing.json")
with open(SAMPLE, encoding="utf-8") as fh:
    sample = json.load(fh)

# 首页
r = client.get("/")
check("首页 200", r.status_code == 200 and "换乘标志".encode() in r.data)

# 样例接口
r = client.get("/api/sample")
check("样例接口", r.status_code == 200 and r.get_json()["models"])

# 校验通过
r = client.post("/api/validate", json=sample)
body = r.get_json()
check("validate 通过", r.status_code == 200 and body["ok"] is True)
check("validate 返回源表", len(body["source_tables"]) == 3)
check("validate 返回模型依赖",
      {m["name"]: m["upstream"] for m in body["models"]}["ads_transfer_settlement"]
      == ["dwd_card_transaction_detail", "dws_line_transfer_summary"])

# 分析
r = client.post("/api/analyze?table=ods_card_transaction&column=transfer_flag", json=sample)
body = r.get_json()
check("analyze 通过", r.status_code == 200 and body["ok"] is True)
check("analyze 命中 4 模型", body["result"]["model_count"] == 4)
check("analyze 命中 21 列", body["result"]["column_count"] == 21)

# 卡状态：只出现在 EXISTS 返回列的模型不误报，参与嵌套筛选的模型正常命中
r = client.post("/api/analyze?table=ods_card_info&column=status", json=sample)
body = r.get_json()
names = {m["model"] for m in body["result"]["affected_models"]}
check("卡状态 analyze 通过", r.status_code == 200 and body["ok"] is True)
check("卡状态命中 5 模型 23 列",
      body["result"]["model_count"] == 5 and body["result"]["column_count"] == 23)
check("卡状态-EXISTS返回列模型不误报", "ads_status_probe_settlement" not in names)
check("卡状态-嵌套筛选模型命中",
      {"ads_active_card_settlement", "ads_city_in_settlement"} <= names)
# EXISTS 步骤在路径中注明存在性检查、不取列
probe_paths = [s for m in body["result"]["affected_models"] for c in m["columns"] for s in c["path"]]
exists_steps = [s for s in probe_paths if s.get("predicate") == "EXISTS"]
check("卡状态-EXISTS步骤注明存在性",
      bool(exists_steps) and all(s.get("existence") is True and s.get("input_column") is None
                                 for s in exists_steps))

# 发卡城市：EXISTS 内层 WHERE / IN 返回列 / EXISTS 内嵌 IN 左操作数均命中
r = client.post("/api/analyze?table=ods_card_info&column=issue_city", json=sample)
body = r.get_json()
names = {m["model"] for m in body["result"]["affected_models"]}
check("发卡城市命中 3 模型 6 列",
      body["result"]["model_count"] == 3 and body["result"]["column_count"] == 6)
check("发卡城市-探针模型经EXISTS条件命中", "ads_status_probe_settlement" in names)

# 缺参数
r = client.post("/api/analyze", json=sample)
check("缺参数 400", r.status_code == 400 and r.get_json()["ok"] is False)

# 坏 JSON
r = client.post("/api/validate", data="{oops", content_type="application/json")
check("坏 JSON 400", r.status_code == 400 and "JSON" in r.get_json()["error"])

# 空 body
r = client.post("/api/validate", data="", content_type="application/json")
check("空 body 400", r.status_code == 400 and "空" in r.get_json()["error"])

# 无法解析的引用 -> 指出模型并停止
bad = {
    "source_tables": [{"table": "t", "columns": ["a"]}],
    "models": [{"name": "boom", "sql": "SELECT z.a AS a FROM t x"}],
}
r = client.post("/api/validate", json=bad)
check("未知别名 400 带模型名",
      r.status_code == 400 and "boom" in r.get_json()["error"] and "z" in r.get_json()["error"])

# 非字典顶层
r = client.post("/api/validate", json=[1, 2, 3])
check("顶层非对象 400", r.status_code == 400)

# 目标字段不存在
r = client.post("/api/analyze?table=ods_card_transaction&column=ghost", json=sample)
check("目标列不存在 400", r.status_code == 400 and "ghost" in r.get_json()["error"])

# 未被任何模型读取的字段 -> 零影响
r = client.post("/api/analyze?table=ods_card_info&column=last_active_date", json=sample)
body = r.get_json()
check("未读取字段零影响",
      r.status_code == 200 and body["result"]["model_count"] == 0)

print()
if FAILURES:
    print(f"{len(FAILURES)} 项失败：{FAILURES}")
    sys.exit(1)
print("API 测试全部通过。")
