#!/usr/bin/env python3
"""人评打分界面：把「填 reviews.json」这件事从手改 JSON 变成看片点分。

对应用户第 2 点：「效果由我评判」。判定权留给用户，但**门槛要降到用户愿意做**——
手改 8 条 × 6 维度的 JSON 极易卡住，于是这条链永远闭不上。

做法：起一个只绑 127.0.0.1 的本地小服务，浏览器里并排放「成片 + 自动指标 + 打分表」，
点完「保存」直接写回 `results/human_review/reviews.json`（原子写）。

    python eval/human_review/review_ui.py            # 起服务并自动开浏览器
    python eval/human_review/review_ui.py --port 8765 --no-open

安全边界：只监听 127.0.0.1；写入路径固定（不接受请求里带路径）；不外传任何数据。
"""
import argparse
import json
import os
import re
import sys
import tempfile
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.human_review.build_packet import DIMENSIONS, OVERALL_KEY  # noqa: E402

ITEMS_DIR = ROOT / "results" / "human_review" / "items"
REVIEWS_PATH = ROOT / "results" / "human_review" / "reviews.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_items(items_path: Path) -> list:
    """与 build_packet.build 同口径：三种形态都支持。"""
    d = load_json(items_path, [])
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict) and isinstance(d.get("items"), list):
        return [x for x in d["items"] if isinstance(x, dict)]
    if isinstance(d, dict):
        return [d]
    return []


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>人评打分台 · 译制 Pipeline</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; background:#f5f6f8; color:#1d2129; }
  header { position: sticky; top:0; z-index:10; background:#fff; border-bottom:1px solid #e3e5e8;
           padding:10px 20px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  .bar { flex:1; }
  button { font:inherit; padding:6px 14px; border:1px solid #c9cdd4; background:#fff; border-radius:6px; cursor:pointer; }
  button.primary { background:#165dff; color:#fff; border-color:#165dff; }
  button:hover { filter:brightness(.96); }
  .card { background:#fff; border:1px solid #e3e5e8; border-radius:10px; margin:16px 20px; padding:16px; }
  .card h2 { font-size:15px; margin:0 0 4px; }
  .grid { display:flex; gap:20px; flex-wrap:wrap; }
  video { width:520px; max-width:100%; background:#000; border-radius:6px; }
  .meta { flex:1; min-width:290px; }
  table { border-collapse:collapse; font-size:13px; }
  td, th { border:1px solid #e3e5e8; padding:3px 10px; text-align:left; }
  th { background:#fafbfc; font-weight:600; }
  .scores { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:8px 16px; margin-top:10px; }
  .scores label { display:block; font-size:12px; color:#4e5969; }
  select { width:100%; padding:4px 6px; font:inherit; border:1px solid #c9cdd4; border-radius:5px; background:#fff; }
  select.filled { border-color:#165dff; background:#eef3ff; }
  textarea { width:100%; min-height:44px; padding:6px; font:inherit; border:1px solid #c9cdd4; border-radius:5px; margin-top:8px; }
  .hint { color:#86909c; font-size:12px; }
  .pill { display:inline-block; padding:1px 8px; border-radius:10px; background:#eef3ff; color:#165dff; font-size:12px; }
  #toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%); background:#1d2129; color:#fff;
           padding:10px 18px; border-radius:8px; opacity:0; transition:.25s; pointer-events:none; z-index:99; }
  #toast.on { opacity:1; }
  .tag { font-size:12px; padding:1px 7px; border-radius:4px; background:#f2f3f5; color:#4e5969; margin-left:6px; }
</style></head><body>
<header>
  <h1>人评打分台</h1>
  <span class="pill" id="prog">已评 0 / 0</span>
  <span class="hint" id="draftTag"></span>
  <span class="bar"></span>
  <button id="reload">重新载入</button>
  <button id="copy">复制 JSON</button>
  <button class="primary" id="save">保存到 reviews.json</button>
</header>
<div id="list"></div>
<div id="toast"></div>
<script>
const DATA = __DATA__;
const DIMS = __DIMS__;
const OVERALL_KEY = "__OVERALL__";
const DRAFT_KEY = "vd_review_draft_v1";
let reviews = DATA.reviews || {};

function toast(msg){ var t=document.getElementById('toast'); t.textContent=msg; t.classList.add('on');
  setTimeout(function(){ t.classList.remove('on'); }, 2200); }
function draft(){ try { return JSON.parse(localStorage.getItem(DRAFT_KEY)||"null"); } catch(e){ return null; } }
function saveDraft(){ localStorage.setItem(DRAFT_KEY, JSON.stringify(reviews)); }
function fmt(v){ return (v===null||v===undefined) ? "—" : (typeof v==="number" ? Math.round(v*1000)/1000 : v); }
function isFilled(it){ var r=reviews[it.id]||{}; return !!r[OVERALL_KEY]; }
function opts(cur){
  var s = '<option value="">未评</option>';
  for (var v=1; v<=5; v++){ s += '<option value="'+v+'"'+(String(cur)===String(v)?' selected':'')+'>'+v+'</option>'; }
  return s;
}

function render(){
  var list=document.getElementById('list'); list.innerHTML='';
  DATA.items.forEach(function(it){
    if(!reviews[it.id]) reviews[it.id]={erase:null,break:null,lip:null,style:null,sub:null,overall:null,note:""};
    var r=reviews[it.id], m=it.metrics||{}, c=it.cost||{};
    var rows='';
    Object.keys(m).forEach(function(k){ rows += '<tr><th>'+k+'</th><td>'+fmt(m[k])+'</td></tr>'; });
    var scoreHtml='';
    DIMS.forEach(function(d){
      scoreHtml += '<div><label title="'+d.hint+'">'+d.label+'</label>'+
        '<select data-id="'+it.id+'" data-key="'+d.key+'" class="'+(r[d.key]!=null?'filled':'')+'">'+
        opts(r[d.key])+'</select></div>';
    });
    scoreHtml += '<div><label title="整体分决定这条算达标还是不可接受，阈值由它反推">整体（判定权）</label>'+
      '<select data-id="'+it.id+'" data-key="overall" class="'+(r.overall!=null?'filled':'')+'">'+
      opts(r.overall)+'</select></div>';
    var title = (it.title && it.title!==it.id) ? '<span class="hint"> · '+it.title+'</span>' : '';
    var card=document.createElement('div'); card.className='card';
    card.innerHTML='<h2>'+it.id+'<span class="tag">'+(it.difficulty_type||'')+'</span>'+title+'</h2>'+
      '<div class="grid">'+
      '<video controls preload="metadata" src="/media/'+encodeURIComponent(it.id)+'.mp4"></video>'+
      '<div class="meta">'+
      '<div class="hint">'+(it.auto_note||"自动指标只作参考列，判定权在 overall。")+'</div>'+
      '<table>'+rows+'<tr><th>成本 USD</th><td>'+fmt(c.usd)+'</td></tr></table>'+
      '<div class="scores">'+scoreHtml+'</div>'+
      '<textarea data-id="'+it.id+'" data-key="note" placeholder="备注（可选）"></textarea>'+
      '</div></div>';
    list.appendChild(card);
    var ta=card.querySelector('textarea'); ta.value = r.note||"";
  });
  list.querySelectorAll('select').forEach(function(sel){
    sel.addEventListener('change', function(){
      var id=sel.dataset.id, k=sel.dataset.key;
      reviews[id][k] = sel.value==="" ? null : Number(sel.value);
      sel.classList.toggle('filled', sel.value!=="");
      saveDraft(); progress();
    });
  });
  list.querySelectorAll('textarea').forEach(function(ta){
    ta.addEventListener('input', function(){
      reviews[ta.dataset.id][ta.dataset.key]=ta.value; saveDraft(); progress();
    });
  });
  progress();
}

function progress(){
  var n=DATA.items.filter(isFilled).length;
  document.getElementById('prog').textContent='已评 '+n+' / '+DATA.items.length;
  document.getElementById('draftTag').textContent = draft() ? '有本地草稿（尚未写入文件）' : '';
}

function payload(){
  var out={};
  DATA.items.forEach(function(it){
    var r=reviews[it.id]||{};
    out[it.id]={erase:r.erase==null?null:r.erase, break:r.break==null?null:r.break,
                lip:r.lip==null?null:r.lip, style:r.style==null?null:r.style,
                sub:r.sub==null?null:r.sub, overall:r.overall==null?null:r.overall,
                note:r.note||""};
  });
  return out;
}

document.getElementById('save').addEventListener('click', function(){
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviews:payload()})})
   .then(function(r){ return r.json(); })
   .then(function(j){ if(j.ok){ localStorage.removeItem(DRAFT_KEY); toast('已写入 '+j.path); progress(); }
                     else toast('写入失败：'+(j.error||'未知错误')); })
   .catch(function(e){ toast('写入失败：'+e); });
});
document.getElementById('copy').addEventListener('click', function(){
  var t=JSON.stringify(payload(),null,2);
  if(navigator.clipboard){ navigator.clipboard.writeText(t).then(function(){ toast('已复制'); },
                                                          function(){ console.log(t); toast('剪贴板不可用，已打到控制台'); }); }
  else { console.log(t); toast('剪贴板不可用，已打到控制台'); }
});
document.getElementById('reload').addEventListener('click', function(){ location.reload(); });
render();
</script></body></html>
"""


def build_html(items: list, reviews: dict) -> str:
    payload = {
        "items": [
            {k: it.get(k) for k in ("id", "title", "difficulty_type", "metrics", "cost", "auto_note")}
            for it in items
        ],
        "reviews": reviews,
    }
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    html = html.replace("__DIMS__", json.dumps(list(DIMENSIONS), ensure_ascii=False))
    return html.replace("__OVERALL__", OVERALL_KEY)


class Handler(SimpleHTTPRequestHandler):
    """静态页面 + 带 Range 的视频 + 一个固定路径的写回接口。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    # --- 视频要支持 Range，否则浏览器不能拖进度条 ---
    def send_head(self):
        path = self.path.split("?")[0]
        if path.startswith("/media/"):
            fp = ITEMS_DIR / os.path.basename(path)
            if fp.is_file():
                return self._serve_media(fp)
        return super().send_head()

    def _serve_media(self, fp: Path):
        size = fp.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if not m:
                self.send_error(400)
                return None
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            end = min(end, size - 1)
            partial = True
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        f = open(fp, "rb")
        f.seek(start)
        self._range_left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = getattr(self, "_range_left", None)
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            chunk = source.read(min(64 * 1024, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)
        self._range_left = None

    # --- 写回：路径固定，不接受请求里带路径 ---
    def do_POST(self):
        if self.path.split("?")[0] != "/save":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 2_000_000:
                raise ValueError("请求体大小异常")
            raw = self.rfile.read(n)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk")   # 某些 Windows 客户端会按本地代码页发
            body = json.loads(text)
            reviews = body["reviews"]
            if not isinstance(reviews, dict):
                raise ValueError("reviews 必须是对象")
            for k, v in reviews.items():
                if not isinstance(v, dict):
                    raise ValueError(f"{k} 的打分格式不对")
            REVIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = REVIEWS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, REVIEWS_PATH)
            resp = {"ok": True, "path": str(REVIEWS_PATH.relative_to(ROOT))}
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        out = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass  # 安静一点，别刷屏


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default=str(ITEMS_DIR / "items.json"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    items = collect_items(Path(args.items))
    if not items:
        sys.exit(f"没读到条目：{args.items}（先跑 make_items.py）")
    reviews = load_json(REVIEWS_PATH, {})
    html = build_html(items, reviews)

    class H(Handler):
        def do_GET(self):
            if self.path.split("?")[0] == "/":
                out = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"人评打分台：{url}")
    print(f"  条目 {len(items)} 条 · 评分维度 {len(DIMENSIONS)} + overall")
    print(f"  保存目标：{REVIEWS_PATH.relative_to(ROOT)}")
    print("  Ctrl+C 退出")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
