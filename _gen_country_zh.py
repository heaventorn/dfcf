"""从 events.py 的 COUNTRY_ZH 生成全球眼要用的英文国名 -> 中文对照表。

为什么需要它：卫星底图和国界 GeoJSON 里的国家名是英文（Vietnam / China），而新闻
事件里的 country 字段是中文（越南 / 中国）。点国家要按国名筛新闻，两边必须能对上号。
events.py 里已有一份权威对照表，这里把它导成静态 JSON，让纯前端的全球眼页面直接用。
"""
import ast
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "godseye", "public", "dfcf-sample", "country_zh.json")
SAMPLE = os.path.join(BASE, "godseye", "public", "dfcf-sample")

# 1) 用 AST 取 COUNTRY_ZH，不 import（避免拉起整条抓取链）
tree = ast.parse(open(os.path.join(BASE, "events.py"), encoding="utf-8").read())
table = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        getattr(t, "id", None) == "COUNTRY_ZH" for t in node.targets
    ):
        table = ast.literal_eval(node.value)
assert isinstance(table, dict) and len(table) > 100, "没拿到 COUNTRY_ZH"

# GeoJSON 里的名字和 events.py 的键偶尔对不上，补几个别名
ALIAS = {
    "United States of America": "United States",
    "Russian Federation": "Russia",
    "Republic of Korea": "South Korea",
    "Korea": "South Korea",
    "Czech Republic": "Czechia",
    "Republic of the Congo": "Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "United Republic of Tanzania": "Tanzania",
    "Federated States of Micronesia": "Micronesia",
    "Kingdom of the Netherlands": "Netherlands",
    "Republic of Serbia": "Serbia",
    "The Bahamas": "Bahamas",
    "Macedonia": "North Macedonia",
    "Swaziland": "Eswatini",
    "Guinea Bissau": "Guinea-Bissau",
    "East Timor": "Timor-Leste",
    "West Bank": "Palestine",
    "Northern Cyprus": "Cyprus",
    "Somaliland": "Somalia",
}

geo = json.load(open(os.path.join(SAMPLE, "countries.geojson"), encoding="utf-8"))
names = sorted(
    {(f.get("properties") or {}).get("name", "").strip()
     for f in geo.get("features") or []} - {""}
)

out, missing = {}, []
for en in names:
    zh = table.get(en) or table.get(ALIAS.get(en, ""))
    if zh:
        out[en] = zh
    else:
        missing.append(en)
for en, zh in table.items():          # 国界文件没覆盖的国家也一并带上
    out.setdefault(en, zh)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=0)
    f.write("\n")

print("写入 %s：%d 条" % (OUT, len(out)))
print("对不上中文名的国界条目（点它只会显示英文）:", missing)
