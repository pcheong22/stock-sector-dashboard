"""
Generates docs/index.html from the latest stored data.
Writes HTML in pieces to avoid Python/JS string escaping conflicts.
"""
from __future__ import annotations
import json, base64
from pathlib import Path
import pandas as pd
from momentum_model import aggregate_sector_breadth
import storage

SECTORS = [
    "Communication Services","Consumer Discretionary","Consumer Staples",
    "Energy","Financials","Health Care","Industrials",
    "Materials","Real Estate","Technology","Utilities",
]
SECTOR_ETFS = {
    "Technology":"XLK","Financials":"XLF","Health Care":"XLV",
    "Consumer Discretionary":"XLY","Consumer Staples":"XLP","Energy":"XLE",
    "Industrials":"XLI","Materials":"XLB","Utilities":"XLU",
    "Real Estate":"XLRE","Communication Services":"XLC",
}
REPO = "pcheong22/stock-sector-dashboard"

def build_payload():
    csv = storage.CSV_MIRROR
    if not Path(csv).exists():
        raise RuntimeError("No history.csv found -- run run_daily.py first.")
    hist = pd.read_csv(csv)
    hist["Date"] = pd.to_datetime(hist["Date"], format="mixed")
    hist = hist.drop_duplicates(subset=["Date", "Ticker"], keep="first")
    available_dates = sorted(hist["Date"].dt.strftime("%Y-%m-%d").unique(), reverse=True)
    latest = available_dates[0]
    prior  = available_dates[1] if len(available_dates) > 1 else None

    dates_data = {}
    for date_str in available_dates:
        day = hist[hist["Date"].dt.strftime("%Y-%m-%d") == date_str].copy()
        global_cols = ["Ticker","Sector","GlobalScore","SectorScore","Price",
                       "ret_1M","ret_3M","ret_6M","ret_12M","rsi_14"]
        global_rows = (day.sort_values("GlobalScore", ascending=False)[global_cols]
                          .round({"GlobalScore":1,"SectorScore":1,"Price":2,
                                  "ret_1M":4,"ret_3M":4,"ret_6M":4,"ret_12M":4,"rsi_14":1})
                          .values.tolist())
        st_df = aggregate_sector_breadth(day)
        if prior:
            prior_day = hist[hist["Date"].dt.strftime("%Y-%m-%d") == prior]
            prior_st  = aggregate_sector_breadth(prior_day)[["Sector","AvgGlobalScore","BreadthPct"]]
            prior_st  = prior_st.rename(columns={"AvgGlobalScore":"_PA","BreadthPct":"_PB"})
            st_df = st_df.merge(prior_st, on="Sector", how="left")
            st_df["ScoreChange"]   = (st_df["AvgGlobalScore"] - st_df["_PA"]).round(1)
            st_df["BreadthChange"] = (st_df["BreadthPct"]     - st_df["_PB"]).round(1)
            st_df = st_df.drop(columns=["_PA","_PB"])
        sector_rows = st_df.round({"AvgGlobalScore":1,"MedianGlobalScore":1,"BreadthPct":1}).values.tolist()
        sector_cols = list(st_df.columns)
        drill_cols = ["Ticker","GlobalScore","SectorScore","Price",
                      "ret_1M","ret_3M","ret_6M","ret_12M",
                      "dist_from_52w_high","dist_from_52w_low","realized_vol_21d",
                      "trend_r2_63d","rsi_14","rel_ret_3M_vs_market","rel_ret_3M_vs_sector"]
        drill = {}
        for sector, grp in day.groupby("Sector"):
            drill[sector] = (grp.sort_values("SectorScore", ascending=False)[drill_cols]
                               .round(4).values.tolist())
        dates_data[date_str] = {
            "global_cols": global_cols, "global_rows": global_rows,
            "sector_cols": sector_cols, "sector_rows": sector_rows,
            "drill_cols":  drill_cols,  "drill":       drill,
        }
    payload = {"dates": available_dates, "latest": latest, "prior": prior, "data": dates_data}
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=True).encode()).decode("ascii")
    assert '"' not in b64 and "'" not in b64 and "\\" not in b64
    return b64

def write_html(out_path: Path, payload_b64: str):
    sector_options = "\n".join(
        f'<option value="{s}">{s}</option>' for s in SECTORS
    )
    etf_map_json   = json.dumps(SECTOR_ETFS, ensure_ascii=True)
    repo_json      = json.dumps(REPO)

    f = out_path.open("w", encoding="utf-8")
    w = f.write

    w("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock &amp; Sector Rankings</title>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2d3147;--text:#e0e0e0;--muted:#888;--accent:#4f8ef7;--green:#2ecc71;--red:#e74c3c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:24px}
h1{font-size:1.4rem;margin-bottom:16px;color:var(--accent)}
.controls{display:flex;gap:12px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
select{background:var(--surface);color:var(--text);border:1px solid var(--border);padding:6px 10px;border-radius:6px;font-size:.9rem;cursor:pointer}
.tabs{display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.tab{padding:8px 16px;cursor:pointer;border-radius:6px 6px 0 0;font-size:.9rem;color:var(--muted);border:1px solid transparent;border-bottom:none}
.tab.active{color:var(--text);background:var(--surface);border-color:var(--border)}
.panel{display:none}.panel.active{display:block}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{background:var(--surface);padding:8px 10px;text-align:left;border-bottom:2px solid var(--border);color:var(--muted);font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--text)}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover td{background:var(--surface)}
.up{color:var(--green)}.down{color:var(--red)}
.bar-wrap{margin-top:20px}
.bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:.82rem}
.bar-label{width:180px;text-align:right;color:var(--muted);flex-shrink:0}
.bar-track{flex:1;background:var(--surface);border-radius:4px;height:18px}
.bar-fill{height:100%;background:var(--accent);border-radius:4px;transition:width .4s;display:flex;align-items:center;padding-left:6px;font-size:.78rem;color:#fff;white-space:nowrap}
.caption{color:var(--muted);font-size:.8rem;margin-bottom:10px}
.form-grid{display:grid;gap:14px;max-width:600px;margin-top:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
label.field{font-size:.85rem;color:var(--muted)}
input[type=password],input[type=date],textarea,select.field{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px 10px;font-size:.9rem;width:100%}
textarea{resize:vertical;min-height:72px}
.radio-group{display:flex;gap:16px}
.radio-group label{display:flex;align-items:center;gap:6px;font-size:.9rem;cursor:pointer;color:var(--text)}
.btn{background:var(--accent);color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:.9rem}
.btn:hover{opacity:.85}
.btn.muted{background:var(--border)}
.msg{font-size:.85rem;margin-top:8px;padding:8px 12px;border-radius:6px;display:none}
.msg.ok{background:#1a3a2a;color:var(--green);display:block}
.msg.err{background:#3a1a1a;color:var(--red);display:block}
.token-row{display:flex;gap:8px;align-items:center;margin-bottom:6px}
.token-row input{flex:1}
.token-note{font-size:.78rem;color:var(--muted);margin-bottom:14px}
details{margin-top:24px}
summary{cursor:pointer;color:var(--muted);font-size:.85rem;margin-bottom:10px}
</style>
</head>
<body>
<h1>&#128202; Daily Stock &amp; Sector Rankings</h1>
<div class="controls">
  <label style="color:var(--muted);font-size:.9rem">Date:</label>
  <select id="date-select"></select>
  <span id="meta" style="color:var(--muted);font-size:.82rem"></span>
</div>
<div class="tabs">
  <div class="tab active" data-tab="sector">Sector Rankings</div>
  <div class="tab" data-tab="global">Global Rankings</div>
  <div class="tab" data-tab="drill">Sector Drill-Down</div>
  <div class="tab" data-tab="journal">Log a Decision</div>
</div>
<div id="sector" class="panel active">
  <p class="caption" id="sector-caption"></p>
  <div class="table-wrap"><table id="sector-table"></table></div>
  <div class="bar-wrap" id="sector-bars"></div>
</div>
<div id="global" class="panel">
  <div class="table-wrap"><table id="global-table"></table></div>
</div>
<div id="drill" class="panel">
  <div class="controls">
    <label style="color:var(--muted);font-size:.9rem">Sector:</label>
    <select id="drill-sector"></select>
  </div>
  <div class="table-wrap"><table id="drill-table"></table></div>
</div>
<div id="journal" class="panel">
  <h2 style="font-size:1rem;margin-bottom:4px">Log a Decision</h2>
  <p class="caption" style="margin-bottom:14px">Sector-level relative-value calls. Outcome filled in at Phase 3 review.</p>
  <p class="field" style="font-size:.85rem;color:var(--muted);margin-bottom:4px">GitHub token (saved in your browser only):</p>
  <div class="token-row">
    <input type="password" id="gh-token" placeholder="github_pat_..." />
    <button class="btn" onclick="saveToken()">Save</button>
    <button class="btn muted" onclick="clearToken()">Clear</button>
  </div>
  <p class="token-note" id="token-status"></p>
  <div class="form-grid">
    <div class="form-group">
      <label class="field">Date</label>
      <input type="date" id="j-date" />
    </div>
    <div class="form-group">
      <label class="field">Decision type</label>
      <select class="field" id="j-type">
        <option>Open / increase overweight</option>
        <option>Trim / reduce overweight</option>
        <option>Rotate into this sector from another</option>
        <option>Watch only (no action)</option>
      </select>
    </div>
    <div class="form-group">
      <label class="field">Sector</label>
      <select class="field" id="j-sector">
""")
    w(sector_options)
    w("""
      </select>
    </div>
    <div class="form-group">
      <label class="field">What did you decide?</label>
      <textarea id="j-decision" placeholder="e.g. Overweighted Technology vs Energy"></textarea>
    </div>
    <div class="form-group">
      <label class="field">Did the sector rankings change this vs what you would have done otherwise?</label>
      <div class="radio-group">
        <label><input type="radio" name="influenced" value="Y" checked> Yes</label>
        <label><input type="radio" name="influenced" value="N"> No</label>
      </div>
    </div>
    <div class="form-group">
      <label class="field">Why? (cite the specific number)</label>
      <textarea id="j-reason"></textarea>
    </div>
    <div class="form-group">
      <label class="field">Notes (optional)</label>
      <textarea id="j-notes"></textarea>
    </div>
    <button class="btn" onclick="submitEntry()">Save entry</button>
    <div class="msg" id="j-msg"></div>
  </div>
  <details>
    <summary>View past entries</summary>
    <button class="btn muted" style="margin:8px 0" onclick="loadJournal()">Load entries</button>
    <div class="table-wrap"><table id="journal-table"></table></div>
  </details>
</div>
<script>
""")

    # Write JS variables using safe json.dumps (no triple-quote issues)
    w("const DB   = JSON.parse(atob('" + payload_b64 + "'));\n")
    w("const REPO = " + repo_json + ";\n")
    w("const ETFS = " + etf_map_json + ";\n")
    w('const JPATH = "data/decision_journal.csv";\n')

    w("""
// token
function saveToken(){
  var t=document.getElementById("gh-token").value.trim();
  if(!t) return;
  localStorage.setItem("gh_token",t);
  document.getElementById("gh-token").value="";
  updateTokenStatus();
}
function clearToken(){ localStorage.removeItem("gh_token"); updateTokenStatus(); }
function getToken(){ return localStorage.getItem("gh_token")||""; }
function updateTokenStatus(){
  var el=document.getElementById("token-status");
  if(getToken()){ el.textContent="Token saved in this browser."; document.getElementById("gh-token").placeholder="token saved"; }
  else { el.textContent="No token saved yet."; }
}

// GitHub API
async function ghGet(path){
  var r=await fetch("https://api.github.com/repos/"+REPO+"/contents/"+path,
    {headers:{"Authorization":"Bearer "+getToken(),"Accept":"application/vnd.github+json"}});
  if(r.status===404) return null;
  if(!r.ok) throw new Error("GitHub read error "+r.status);
  return r.json();
}
async function ghPut(path,content,sha,message){
  var body={message:message,content:btoa(unescape(encodeURIComponent(content))),branch:"main"};
  if(sha) body.sha=sha;
  var r=await fetch("https://api.github.com/repos/"+REPO+"/contents/"+path,
    {method:"PUT",headers:{"Authorization":"Bearer "+getToken(),"Accept":"application/vnd.github+json","Content-Type":"application/json"},body:JSON.stringify(body)});
  if(!r.ok) throw new Error("GitHub write error "+r.status+": "+await r.text());
}

// CSV helpers
function appendCSV(text,row){
  var cols=["Date","Decision","DashboardInfluenced","Reason","Outcome","Notes","Ticker"];
  function esc(v){ return (v.includes(",")||v.includes('"')||v.includes("\\n")) ? '"'+v.replace(/"/g,'""')+'"' : v; }
  var line=row.map(esc).join(",");
  if(!text.trim()) return cols.join(",")+("\\n")+line+("\\n");
  return text.trimEnd()+("\\n")+line+("\\n");
}

async function submitEntry(){
  var msg=document.getElementById("j-msg");
  msg.className="msg"; msg.textContent="";
  if(!getToken()){msg.className="msg err";msg.textContent="Save your GitHub token first.";return;}
  var decision=document.getElementById("j-decision").value.trim();
  if(!decision){msg.className="msg err";msg.textContent="Add a description before saving.";return;}
  var sector=document.getElementById("j-sector").value;
  var dtype=document.getElementById("j-type").value;
  var influenced=document.querySelector("input[name=influenced]:checked").value;
  var reason=document.getElementById("j-reason").value.trim();
  var notes=document.getElementById("j-notes").value.trim();
  var date=document.getElementById("j-date").value;
  var ticker=ETFS[sector]||sector;
  var row=[date,"["+dtype+"] "+decision,influenced,reason,"",notes,ticker];
  try{
    var existing=await ghGet(JPATH);
    var oldText=existing?decodeURIComponent(escape(atob(existing.content.replace(/\\n/g,"")))):"";
    var newText=appendCSV(oldText,row);
    await ghPut(JPATH,newText,existing?existing.sha:null,"Journal: "+date+" ("+sector+")");
    msg.className="msg ok"; msg.textContent="Saved!";
    ["j-decision","j-reason","j-notes"].forEach(function(id){document.getElementById(id).value="";});
  }catch(e){msg.className="msg err";msg.textContent=e.message;}
}

async function loadJournal(){
  if(!getToken()){alert("Save your GitHub token first.");return;}
  try{
    var existing=await ghGet(JPATH);
    var tbl=document.getElementById("journal-table");
    if(!existing){tbl.innerHTML="<tr><td>No entries yet.</td></tr>";return;}
    var text=decodeURIComponent(escape(atob(existing.content.replace(/\\n/g,""))));
    var lines=text.trim().split("\\n");
    var cols=lines[0].split(",");
    var rows=lines.slice(1).reverse().map(function(l){return l.split(",");});
    buildTable(tbl,cols,rows,0);
  }catch(e){alert(e.message);}
}

// formatting
var PCT_COLS={"ret_1M":1,"ret_3M":1,"ret_6M":1,"ret_12M":1,"dist_from_52w_high":1,"dist_from_52w_low":1,"realized_vol_21d":1,"rel_ret_3M_vs_market":1,"rel_ret_3M_vs_sector":1};
var DOLLAR_COLS={"Price":1};
var SCORE_COLS={"GlobalScore":1,"SectorScore":1,"AvgGlobalScore":1,"MedianGlobalScore":1};
var CHANGE_COLS={"ScoreChange":1,"BreadthChange":1};

function fmt(col,val){
  if(val===null||val===undefined||val==="") return "\u2014";
  if(DOLLAR_COLS[col]) return "$"+Number(val).toFixed(2);
  if(col==="BreadthPct") return Number(val).toFixed(0)+"%";
  if(PCT_COLS[col]) return (Number(val)*100).toFixed(1)+"%";
  if(col==="rsi_14") return Number(val).toFixed(0);
  if(col==="trend_r2_63d") return Number(val).toFixed(2);
  if(SCORE_COLS[col]) return Number(val).toFixed(1);
  if(CHANGE_COLS[col]){
    var n=Number(val);
    var suffix=col==="BreadthChange"?"pp":"";
    var arrow=n>0?"\u25b2":n<0?"\u25bc":"\u2192";
    var cls=n>0?"up":n<0?"down":"";
    return '<span class="'+cls+'">'+arrow+" "+(n>0?"+":"")+n.toFixed(1)+suffix+"</span>";
  }
  return val;
}

function buildTable(tableEl,cols,rows,rankOffset){
  var sortCol=-1,sortAsc=false;
  function render(data){
    var head="<thead><tr>"+(rankOffset?"<th>#</th>":"")+cols.map(function(c,i){return '<th data-i="'+i+'">'+c+"</th>";}).join("")+"</tr></thead>";
    var body="<tbody>"+data.map(function(row,ri){
      return "<tr>"+(rankOffset?'<td style="color:var(--muted)">'+(ri+rankOffset)+"</td>":"")+
        row.map(function(v,i){return "<td>"+fmt(cols[i],v)+"</td>";}).join("")+"</tr>";
    }).join("")+"</tbody>";
    tableEl.innerHTML=head+body;
    tableEl.querySelectorAll("th[data-i]").forEach(function(th){
      th.addEventListener("click",function(){
        var i=+th.dataset.i;
        if(sortCol===i) sortAsc=!sortAsc; else{sortCol=i;sortAsc=false;}
        render(data.slice().sort(function(a,b){
          var av=a[i],bv=b[i];
          if(av===null) return 1; if(bv===null) return -1;
          return sortAsc?(av>bv?1:-1):(av<bv?1:-1);
        }));
      });
    });
  }
  render(rows);
}

function buildBars(el,rows,cols){
  var si=cols.indexOf("Sector"),vi=cols.indexOf("AvgGlobalScore");
  var max=Math.max.apply(null,rows.map(function(r){return r[vi];}));
  el.innerHTML=rows.map(function(r){
    var pct=(r[vi]/max*100).toFixed(1);
    return '<div class="bar-row"><div class="bar-label">'+r[si]+'</div><div class="bar-track"><div class="bar-fill" style="width:'+pct+'%">'+Number(r[vi]).toFixed(1)+'</div></div></div>';
  }).join("");
}

function render(dateStr){
  var d=DB.data[dateStr];
  document.getElementById("meta").textContent=d.global_rows.length+" tickers scored";
  var hasPrior=d.sector_cols.indexOf("ScoreChange")>=0;
  document.getElementById("sector-caption").textContent=hasPrior?"Changes vs. prior stored date.":"No prior date for comparison yet.";
  buildTable(document.getElementById("sector-table"),d.sector_cols,d.sector_rows,1);
  buildBars(document.getElementById("sector-bars"),d.sector_rows,d.sector_cols);
  buildTable(document.getElementById("global-table"),d.global_cols,d.global_rows,1);
  var ds=document.getElementById("drill-sector"),prev=ds.value;
  ds.innerHTML=Object.keys(d.drill).sort().map(function(s){return '<option value="'+s+'">'+s+"</option>";}).join("");
  if(prev&&d.drill[prev]) ds.value=prev;
  renderDrill(d);
}
function renderDrill(d){
  var s=document.getElementById("drill-sector").value;
  if(!d||!d.drill[s]) return;
  buildTable(document.getElementById("drill-table"),d.drill_cols,d.drill[s],1);
}

// init
var dateSelect=document.getElementById("date-select");
DB.dates.forEach(function(d){var o=document.createElement("option");o.value=o.textContent=d;dateSelect.appendChild(o);});
dateSelect.value=DB.latest;
dateSelect.addEventListener("change",function(){render(dateSelect.value);});
document.getElementById("drill-sector").addEventListener("change",function(){renderDrill(DB.data[dateSelect.value]);});
document.querySelectorAll(".tab").forEach(function(tab){
  tab.addEventListener("click",function(){
    document.querySelectorAll(".tab,.panel").forEach(function(el){el.classList.remove("active");});
    tab.classList.add("active");
    document.getElementById(tab.dataset.tab).classList.add("active");
  });
});
document.getElementById("j-date").value=new Date().toISOString().slice(0,10);
updateTokenStatus();
render(DB.latest);
</script>
</body>
</html>
""")
    f.close()


if __name__ == "__main__":
    docs = Path(__file__).parent / "docs"
    docs.mkdir(exist_ok=True)
    payload_b64 = build_payload()
    out = docs / "index.html"
    write_html(out, payload_b64)
    print(f"Written {out} ({out.stat().st_size:,} bytes)")
