import os, json, re, sqlite3, tempfile, uuid
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from sarvamai import SarvamAI
except Exception:
    SarvamAI = None

DB = Path(os.getenv("RELIEFMESH_DB", Path(__file__).parent / "reliefmesh.db"))
SEVERITIES = ["Critical", "High", "Medium", "Low"]
STATUSES = ["Needs verification", "Resource matching", "Assigned", "In progress", "Resolved"]

st.set_page_config(page_title="ReliefMesh", page_icon="🛟", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
html,body,[class*="css"]{font-family:'DM Sans',sans-serif} h1,h2,h3{font-family:'Space Grotesk',sans-serif}
.block-container{padding-top:1.2rem;max-width:1450px}
.hero{padding:1.5rem;border-radius:20px;background:linear-gradient(120deg,#102b35,#17534d);color:white;margin-bottom:1rem}
.kicker{font-size:11px;letter-spacing:2px;color:#a8d7c9;font-weight:700}
div[data-testid="stMetric"]{background:#f4f8f6;border:1px solid #e0ebe6;padding:13px 16px;border-radius:14px}
.stButton>button{border-radius:10px;font-weight:600}
</style>
""", unsafe_allow_html=True)

def conn():
    c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def q(sql,args=()):
    with conn() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]
def run(sql,args=()):
    with conn() as c: c.execute(sql,args); c.commit()
def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, created_at TEXT, location TEXT, incident_type TEXT, severity TEXT, people INTEGER, needs TEXT, status TEXT, source TEXT, transcript TEXT, summary TEXT, location_verified INTEGER, priority INTEGER, notes TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS resources(id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT, available INTEGER, unit TEXT, location TEXT, category TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS offline_queue(id TEXT PRIMARY KEY, created_at TEXT, payload TEXT, synced INTEGER DEFAULT 0)""")
        if c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]==0:
            samples=[
            ("RM-1042","2026-10-16 19:22","Kondapur, Hyderabad","Medical assistance","Critical",3,"First aid kits, medical team","Needs verification","Sample","Three people reportedly injured after a wall collapse.","Confirm exact lane and number of injured.",0,100,"FICTIONAL SAMPLE"),
            ("RM-1041","2026-10-16 19:16","Miyapur, Hyderabad","Shelter","High",18,"Blankets, dry food packs, drinking water","Resource matching","Sample","Families displaced after water entered homes.","Verify households and supplies.",1,75,"FICTIONAL SAMPLE"),
            ("RM-1040","2026-10-16 19:05","Patancheru, Hyderabad","Drinking water","Medium",42,"Drinking water cans","Assigned","Sample","Community centre reports limited drinking water.","Confirm current stock.",1,55,"FICTIONAL SAMPLE")]
            # Explicit column list avoids mismatch if schema is changed later.
            c.executemany("""INSERT INTO incidents
                (id,created_at,location,incident_type,severity,people,needs,status,source,transcript,summary,location_verified,priority,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [row[:14] for row in samples])
        if c.execute("SELECT COUNT(*) FROM resources").fetchone()[0]==0:
            inv=[("First aid kits",12,"kits","Miyapur Relief Hub","Medical"),("Blankets",80,"blankets","Kondapur School Store","Shelter"),("Dry food packs",65,"packs","Miyapur Relief Hub","Food"),("Drinking water cans",30,"cans","Patancheru Community Hall","Water"),("Portable lights",14,"units","Kondapur Relief Hub","Power"),("First aid kits",5,"kits","Patancheru Community Hall","Medical"),("ORS packets",100,"packets","Miyapur Relief Hub","Medical"),("Tarpaulins",24,"sheets","Kondapur School Store","Shelter")]
            c.executemany("INSERT INTO resources(resource,available,unit,location,category,updated_at) VALUES(?,?,?,?,?,?)",
                          [(a,b,d,e,f,datetime.now().isoformat(timespec="minutes")) for a,b,d,e,f in inv])
init()

def client():
    key=os.getenv("SARVAM_API_KEY","").strip()
    return SarvamAI(api_subscription_key=key) if key and SarvamAI else None

def transcribe(upload):
    api=client()
    if not api: raise RuntimeError("Set SARVAM_API_KEY to enable live transcription.")
    suffix=Path(upload.name).suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False,suffix=suffix) as f:
        f.write(upload.getvalue()); name=f.name
    try:
        with open(name,"rb") as audio:
            r=api.speech_to_text.transcribe(file=audio,model="saaras:v3",mode="codemix")
        if not getattr(r,"transcript",None): raise RuntimeError("No transcript returned.")
        return r.transcript, getattr(r,"language_code","auto-detected")
    finally:
        try: os.unlink(name)
        except OSError: pass

def fallback(text,hint):
    t=text.lower()
    if any(x in t for x in ["injur","bleeding","hurt","medical","ambulance","घायल","चोट","గాయం"]): kind,sev,needs="Medical assistance","Critical",["First aid kits","Medical team"]
    elif any(x in t for x in ["flood","water entered","వరద","बाढ़"]): kind,sev,needs="Flooding / water","High",["Drinking water","Dry food packs","Blankets"]
    elif any(x in t for x in ["water","నీరు","पानी"]): kind,sev,needs="Drinking water","Medium",["Drinking water cans"]
    elif any(x in t for x in ["food","hungry","ఆహారం","भोजन"]): kind,sev,needs="Food assistance","High",["Dry food packs","Drinking water"]
    elif any(x in t for x in ["shelter","displaced","आश्रय"]): kind,sev,needs="Shelter","High",["Blankets","Tarpaulins"]
    elif any(x in t for x in ["light","power","electricity","बिजली"]): kind,sev,needs="Power outage","Medium",["Portable lights"]
    else: kind,sev,needs="General assistance","Medium",["Needs assessment"]
    nums=re.findall(r"\b(\d{1,4})\b",text)
    return {"location":hint.strip() or "Location needs confirmation","incident_type":kind,"severity":sev,"people_affected":int(nums[0]) if nums else 1,"resources_needed":needs,"summary":text,"requires_location_confirmation":not bool(hint.strip()),"uncertainties":["Demo parser used; verify all fields."]}

def extract(text,hint):
    api=client()
    if not api: return fallback(text,hint),"Demo fallback"
    prompt = f"""Extract an emergency-relief report from this multilingual or code-mixed message. Return ONLY JSON with keys: location (string), incident_type (string), severity (Critical/High/Medium/Low), people_affected (integer), resources_needed (array of strings), summary (string), requires_location_confirmation (boolean), uncertainties (array of strings).
Never invent facts or claim help has been dispatched. Critical means reported immediate life threat, serious injury or trapped people; High means urgent shelter/water/food; Medium means important without clear immediate danger; Low means non-urgent. If location is unclear use "Location needs confirmation" and set requires_location_confirmation true. If people count is unknown use 1 and add an uncertainty. No medical diagnosis.
Location hint: {hint or "not provided"}
Report: {text}"""
    # Ask Sarvam for constrained structured output instead of relying on prompt-only JSON.
    incident_schema = {
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "incident_type": {"type": "string"},
            "severity": {"type": "string", "enum": ["Critical", "High", "Medium", "Low"]},
            "people_affected": {"type": "integer"},
            "resources_needed": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
            "requires_location_confirmation": {"type": "boolean"},
            "uncertainties": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["location", "incident_type", "severity", "people_affected", "resources_needed", "summary", "requires_location_confirmation", "uncertainties"],
        "additionalProperties": False
    }
    r=api.chat.completions(
        model="sarvam-105b",
        messages=[
            {"role":"system","content":"Extract incident facts faithfully. Do not invent details. Return the requested structured object."},
            {"role":"user","content":prompt}
        ],
        response_format={"type":"json_schema", "json_schema":{"name":"reliefmesh_incident", "strict":True, "schema":incident_schema}},
        temperature=0.1,
        max_tokens=900,
        reasoning_effort=None
    )
    raw=r.choices[0].message.content or ""
    raw=re.sub(r"^```(?:json)?\s*|\s*```$","",raw.strip())
    try: data=json.loads(raw)
    except Exception:
        m=re.search(r"\{.*\}",raw,re.S)
        if not m:
            preview = raw[:240].replace("\n", " ")
            raise RuntimeError(f"Sarvam did not return a JSON object. Response preview: {preview or '[empty response]'}. Please retry.")
        data=json.loads(m.group(0))
    required=["location","incident_type","severity","people_affected","resources_needed","summary","requires_location_confirmation","uncertainties"]
    if any(k not in data for k in required): raise RuntimeError("Sarvam output missed required fields.")
    if data["severity"] not in SEVERITIES: data["severity"]="Medium"
    if hint.strip() and data["location"]=="Location needs confirmation": data["location"]=hint.strip(); data["requires_location_confirmation"]=False
    return data,"Sarvam-105B"

def score(severity,people,medical=False):
    return min(100,round({"Critical":90,"High":70,"Medium":45,"Low":20}.get(severity,30)+min(max(int(people or 0),0),50)*.25+(10 if medical else 0)))
def inventory(): return q("SELECT * FROM resources ORDER BY category,resource,location")
def incidents(): return q("SELECT * FROM incidents ORDER BY priority DESC,created_at DESC")
def matches(data):
    terms=(data.get("incident_type","")+" "+" ".join(data.get("resources_needed",[]))).lower()
    keys={"Medical":["medical","first aid","ors","injur","ambulance"],"Water":["water","drink","flood"],"Food":["food","meal","hungry"],"Shelter":["shelter","blanket","tarpaulin","displaced"],"Power":["power","light","electricity"]}
    wanted={cat for cat,words in keys.items() if any(w in terms for w in words)}
    return [x for x in inventory() if x["category"] in wanted and x["available"]>0]
def save(data,transcript,source,verified,status):
    ident="RM-"+datetime.now().strftime("%y%m%d")+"-"+str(uuid.uuid4())[:4].upper()
    med=any(w in (data["incident_type"]+" "+" ".join(data["resources_needed"])).lower() for w in ["medical","injur","first aid","ambulance"])
    p=score(data["severity"],data["people_affected"],med)
    run("""INSERT INTO incidents(id,created_at,location,incident_type,severity,people,needs,status,source,transcript,summary,location_verified,priority,notes)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(ident,datetime.now().isoformat(timespec="minutes"),data["location"],data["incident_type"],data["severity"],int(data["people_affected"]),", ".join(data["resources_needed"]),status,source,transcript,data["summary"],int(verified),p,"Uncertainties: "+"; ".join(data.get("uncertainties",[]))))
    return ident

with st.sidebar:
    st.markdown("## 🛟 ReliefMesh")
    st.caption("Multilingual field-to-response coordination")
    page=st.radio("WORKSPACE",["Operations overview","Report an incident","Resource inventory","Offline queue","Activity & exports"],label_visibility="collapsed")
    st.divider()
    online=st.toggle("Network available",value=True,help="Simulates connectivity. Reports are stored in SQLite.")
    ready=bool(os.getenv("SARVAM_API_KEY","").strip()) and SarvamAI is not None
    st.caption("Sarvam API: "+("Configured" if ready else "Not configured — demo fallback"))
    st.caption("Prototype only; sample data is fictional.")

st.markdown('<div class="hero"><div class="kicker">FIELD RESPONSE NETWORK</div><h1 style="color:white;margin:.2rem 0;">ReliefMesh</h1><div style="font-size:16px;color:#d6e9e5;">From a field report to a coordinated response.</div><div style="font-size:11px;color:#b8d2d0;margin-top:12px;">VOICE INTAKE · SARVAM AI · HUMAN VERIFICATION · RESOURCE MATCHING · OFFLINE QUEUE</div></div>',unsafe_allow_html=True)
if not online: st.warning("Offline mode is on. New reports will be saved to the local queue until reconnection is simulated.")

if page=="Operations overview":
    ins=incidents(); openi=[i for i in ins if i["status"]!="Resolved"]; crit=sum(i["severity"]=="Critical" for i in openi); verify=sum(not i["location_verified"] for i in openi); queued=len(q("SELECT * FROM offline_queue WHERE synced=0"))
    a,b,c,d=st.columns(4); a.metric("Open incidents",len(openi)); b.metric("Critical reports",crit); c.metric("Needs verification",verify); d.metric("Offline queue",queued)
    left,right=st.columns([1.55,1])
    with left:
        st.subheader("Incident command")
        if ins:
            df=pd.DataFrame(ins)[["id","created_at","location","incident_type","severity","people","status","priority"]].copy()
            df.columns=["ID","Reported","Location","Incident","Severity","People","Status","Priority"]
            st.dataframe(df,width="stretch",hide_index=True)
        st.subheader("Priority queue")
        for i in openi[:5]:
            st.markdown(f"""<div style="border:1px solid #e4e7ec;border-left:5px solid {'#b42318' if i['severity']=='Critical' else '#b54708' if i['severity']=='High' else '#175cd3'};border-radius:12px;padding:12px;margin:8px 0"><b>{i['id']} · {i['incident_type']}</b><span style="float:right;font-weight:700">{i['severity'].upper()} · {i['priority']}/100</span><div style="color:#667085;font-size:13px;margin-top:4px">{i['location']} · {i['people']} people reported</div><div style="margin-top:6px">{i['needs']}</div><div style="color:#667085;font-size:12px;margin-top:5px">Status: {i['status']}</div></div>""",unsafe_allow_html=True)
    with right:
        st.subheader("Resource availability")
        iv=pd.DataFrame(inventory())
        if not iv.empty: st.dataframe(iv[["resource","available","unit","location"]],width="stretch",hide_index=True)
        st.subheader("Response flow")
        st.markdown("**01 Capture** — voice or text\n\n**02 Understand** — Sarvam transcription + extraction\n\n**03 Verify** — human review\n\n**04 Prioritize** — transparent score\n\n**05 Match** — local stock suggestions\n\n**06 Track** — status and exports")
        st.info("Priority scores are illustrative aids, not validated emergency triage.")

elif page=="Report an incident":
    st.subheader("Report an incident")
    st.write("Upload a short voice note for Sarvam Saaras transcription, or enter a report directly.")
    audio_tab,text_tab=st.tabs(["Voice report","Text report"])
    with audio_tab:
        up=st.file_uploader("Upload voice note",type=["wav","mp3","m4a","aac","ogg","opus","flac","webm","mp4"],help="Use a short recording (up to 30 seconds for synchronous transcription).")
        if up: st.audio(up)
        if up and st.button("Transcribe with Sarvam",type="primary"):
            try:
                with st.spinner("Sarvam Saaras is transcribing..."):
                    transcript,lang=transcribe(up)
                st.session_state["rm_transcript"]=transcript; st.session_state["rm_lang"]=lang
                st.success("Transcribed · "+str(lang))
            except Exception as e: st.error("Transcription failed: "+str(e))
        if st.session_state.get("rm_transcript"): st.text_area("Transcript (editable)",key="rm_transcript")
    with text_tab:
        st.text_area("Field worker report",key="rm_text",height=130,placeholder="Kondapur mein 3 log injured hain, first aid aur ambulance support chahiye...")
    text=st.session_state.get("rm_text","").strip() or st.session_state.get("rm_transcript","").strip()
    source="Text report" if st.session_state.get("rm_text","").strip() else "Voice report"
    hint=st.text_input("Location hint (optional)",placeholder="e.g., Kondapur, Hyderabad")
    if st.button("Analyze report",type="primary",width="stretch"):
        if not text: st.error("Enter a text report or transcribe a voice note first.")
        else:
            try:
                with st.spinner("Extracting incident details..."): data,model=extract(text,hint)
                st.session_state["rm_review"]={"data":data,"text":text,"source":source,"model":model}
            except Exception as e: st.error("Extraction failed: "+str(e))
    if st.session_state.get("rm_review"):
        rev=st.session_state["rm_review"]; data=rev["data"]
        st.divider(); st.subheader("Review before saving"); st.caption("Extraction: "+rev["model"])
        if data.get("uncertainties"): st.warning("Uncertainties: "+"; ".join(data["uncertainties"]))
        with st.form("review"):
            x,y=st.columns(2)
            with x:
                loc=st.text_input("Location",value=data.get("location","")); kind=st.text_input("Incident type",value=data.get("incident_type","General assistance"))
                sev=st.selectbox("Severity",SEVERITIES,index=SEVERITIES.index(data.get("severity","Medium")) if data.get("severity") in SEVERITIES else 2)
            with y:
                people=st.number_input("People affected",min_value=0,max_value=100000,value=max(0,int(data.get("people_affected",1) or 0)))
                needs=st.text_input("Resources needed (comma-separated)",value=", ".join(data.get("resources_needed",[])))
                verified=st.checkbox("I have verified the location",value=not data.get("requires_location_confirmation",True))
            summary=st.text_area("Summary",value=data.get("summary",rev["text"]),height=80)
            save_btn=st.form_submit_button("Confirm and create incident",type="primary",width="stretch")
        if save_btn:
            final={"location":loc.strip() or "Location needs confirmation","incident_type":kind.strip() or "General assistance","severity":sev,"people_affected":int(people),"resources_needed":[n.strip() for n in needs.split(",") if n.strip()],"summary":summary,"uncertainties":data.get("uncertainties",[])}
            status="Needs verification" if not verified else "Resource matching"
            payload={"data":final,"text":rev["text"],"source":rev["source"],"verified":verified,"status":status}
            if not online:
                qid="OFF-"+str(uuid.uuid4())[:8].upper()
                run("INSERT INTO offline_queue VALUES(?,?,?,0)",(qid,datetime.now().isoformat(timespec="minutes"),json.dumps(payload,ensure_ascii=False)))
                st.success("Saved to local offline queue: "+qid)
            else:
                ident=save(final,rev["text"],rev["source"],verified,status); st.success("Incident created: "+ident)
                mm=matches(final)
                if mm: st.markdown("**Suggested resource matches**"); st.dataframe(pd.DataFrame(mm)[["resource","available","unit","location","category"]],width="stretch",hide_index=True)
                else: st.info("No obvious inventory match. Coordinator review needed.")
            st.session_state.pop("rm_review",None); st.session_state.pop("rm_transcript",None); st.rerun()

elif page=="Resource inventory":
    st.subheader("Resource inventory"); st.caption("Inventory persists in local SQLite.")
    data=inventory()
    if data:
        df=pd.DataFrame(data)[["id","resource","available","unit","location","category"]]
        edited=st.data_editor(df,width="stretch",hide_index=True,num_rows="dynamic",column_config={"id":None,"available":st.column_config.NumberColumn(min_value=0,step=1)})
        if st.button("Save inventory",type="primary"):
            for _,r in edited.iterrows():
                name=str(r.get("resource","")).strip()
                if not name: continue
                amount=max(0,int(r.get("available",0) or 0)); rid=r.get("id")
                if pd.notna(rid) and str(rid).isdigit():
                    run("UPDATE resources SET resource=?,available=?,unit=?,location=?,category=?,updated_at=? WHERE id=?",(name,amount,str(r.get("unit","")),str(r.get("location","")),str(r.get("category","")),datetime.now().isoformat(timespec="minutes"),int(rid)))
                else: run("INSERT INTO resources(resource,available,unit,location,category,updated_at) VALUES(?,?,?,?,?,?)",(name,amount,str(r.get("unit","")),str(r.get("location","")),str(r.get("category","")),datetime.now().isoformat(timespec="minutes")))
            st.success("Saved."); st.rerun()
    with st.expander("Add a resource"):
        with st.form("add"):
            a,b=st.columns(2); name=a.text_input("Resource name"); cat=b.selectbox("Category",["Medical","Water","Food","Shelter","Power","Transport","Other"])
            amount=a.number_input("Quantity",min_value=0,step=1); unit=b.text_input("Unit",value="units"); loc=st.text_input("Storage location")
            if st.form_submit_button("Add resource") and name.strip():
                run("INSERT INTO resources(resource,available,unit,location,category,updated_at) VALUES(?,?,?,?,?,?)",(name.strip(),int(amount),unit,loc,cat,datetime.now().isoformat(timespec="minutes"))); st.success("Added."); st.rerun()

elif page=="Offline queue":
    st.subheader("Offline queue")
    waiting=q("SELECT * FROM offline_queue WHERE synced=0 ORDER BY created_at")
    if not waiting: st.success("No queued reports.")
    else:
        st.dataframe(pd.DataFrame([{"Queue ID":x["id"],"Created":x["created_at"],"Status":"Waiting to sync"} for x in waiting]),width="stretch",hide_index=True)
        with st.expander("Inspect reports"):
            for x in waiting:
                p=json.loads(x["payload"]); st.markdown(f"**{x['id']}** · {p['data']['incident_type']} · {p['data']['location']}"); st.caption(p["text"])
        if online and st.button("Sync queued reports",type="primary"):
            for x in waiting:
                p=json.loads(x["payload"]); d=p["data"]
                save(d,p["text"],p["source"],p["verified"],"Resource matching" if p["verified"] else "Needs verification")
                run("UPDATE offline_queue SET synced=1 WHERE id=?",(x["id"],))
            st.success(f"Synced {len(waiting)} report(s)."); st.rerun()
        if not online: st.info("Turn Network available on to simulate reconnection.")
    done=q("SELECT * FROM offline_queue WHERE synced=1 ORDER BY created_at DESC LIMIT 10")
    if done:
        with st.expander("Recently synced"): st.dataframe(pd.DataFrame([{"Queue ID":x["id"],"Created":x["created_at"],"Status":"Synced"} for x in done]),width="stretch",hide_index=True)

elif page=="Activity & exports":
    st.subheader("Activity, status updates & exports")
    data=incidents()
    if data:
        st.dataframe(pd.DataFrame(data),width="stretch",hide_index=True)
        st.download_button("Download incidents CSV",pd.DataFrame(data).to_csv(index=False).encode("utf-8"),"reliefmesh_incidents.csv","text/csv",type="primary")
        iv=pd.DataFrame(inventory())
        st.download_button("Download inventory CSV",iv.to_csv(index=False).encode("utf-8"),"reliefmesh_inventory.csv","text/csv")
        ids=[x["id"] for x in data]; selected=st.selectbox("Select incident",ids,format_func=lambda x:next((f"{i['id']} · {i['incident_type']} · {i['location']}" for i in data if i["id"]==x),x))
        current=next(i["status"] for i in data if i["id"]==selected)
        new=st.selectbox("New status",STATUSES,index=STATUSES.index(current) if current in STATUSES else 0)
        if st.button("Update status"):
            run("UPDATE incidents SET status=? WHERE id=?",(new,selected)); st.success("Updated."); st.rerun()

st.divider()
st.caption("ReliefMesh prototype. Seed data is fictional. Not connected to emergency services. Verify reports, locations, and inventory before acting. For real emergencies, contact local emergency services.")
