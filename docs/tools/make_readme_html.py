"""Builds README.html: the README as one self-contained page (pictures inside), to open with a double-click."""
import base64
import re
from pathlib import Path

import markdown

root = Path(__file__).resolve().parents[2]
md = (root / "README.md").read_text(encoding="utf-8")
body = markdown.markdown(md, extensions=["tables", "fenced_code"])


def inline(m):
    src = m.group(1)
    p = root / src
    if p.suffix == ".svg" and p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'src="data:image/svg+xml;base64,{data}"'
    return m.group(0)


body = re.sub(r'src="([^"]+)"', inline, body)
css = """
:root{--bg:#fff;--fg:#1d1f24;--muted:#5d6370;--line:#e3e5ea;--code:#f3f4f6;--accent:#E8252B;--hl:#FFD400}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e8e9ec;--muted:#9aa0ab;--line:#2a2d35;--code:#1a1d23}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:34px;line-height:1.2;margin:0 0 8px;border-bottom:4px solid var(--hl);padding-bottom:10px}
h2{font-size:24px;margin:48px 0 12px;padding-left:12px;border-left:6px solid var(--accent)}
h3{font-size:18px;margin:28px 0 8px}
img{max-width:100%;height:auto;display:block;margin:16px auto;border-radius:14px}
table{border-collapse:collapse;width:100%;margin:12px 0 20px;font-size:14.5px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:var(--code)}tr:nth-child(even) td{background:color-mix(in srgb,var(--code) 45%,transparent)}
code{background:var(--code);padding:1px 6px;border-radius:5px;font-size:.92em}
pre{background:var(--code);padding:14px 16px;border-radius:10px;overflow-x:auto}pre code{padding:0;background:none}
td:first-child{font-weight:700;white-space:nowrap}
hr{border:0;border-top:1px solid var(--line);margin:36px 0}
@media print{body{font-size:12px}h2{break-before:auto}img{max-width:80%}}
"""
html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>HRC Guide</title>
<style>{css}</style></head><body><main>{body}</main></body></html>"""
(root / "README.html").write_text(html, encoding="utf-8")
print("README.html", len(html) // 1024, "KB")
