import streamlit as st
import pandas as pd
import time
import os
import json
import psutil
import subprocess
import plotly.graph_objects as go
from pathlib import Path
from streamlit_autorefresh import st_autorefresh
import omega_db

# --- PRO APP CONFIGURATION ---
st.set_page_config(
    page_title="Omega Studio Pro",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Refresh every 2 seconds for "Real-time" feel
st_autorefresh(interval=2000, limit=None, key="pro_refresh")

BASE_DIR = Path(os.getcwd())
LOGS_DIR = BASE_DIR / "logs"

# --- ENTERPRISE CSS SYSTEM (FCPX / VS CODE STYLE) ---
st.markdown("""
    <style>
        /* 1. RESET & BASE THEME */
        .stApp {
            background-color: #121212; /* Material Dark Background */
            color: #E0E0E0;
            font-family: 'SF Pro Display', 'Inter', 'Segoe UI', sans-serif;
        }
        
        /* 2. REMOVE STREAMLIT BLOAT */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1rem;
            max-width: 98% !important;
        }

        /* 3. SIDEBAR (The "Tool Panel") */
        section[data-testid="stSidebar"] {
            background-color: #1E1E1E; /* VS Code Sidebar Grey */
            border-right: 1px solid #333;
        }

        /* 4. METRIC CARDS (HUD Style) */
        div[data-testid="stMetric"] {
            background-color: #1E1E1E;
            border: 1px solid #333;
            border-radius: 4px; /* Tighter corners */
            padding: 10px;
        }
        div[data-testid="stMetric"] label {
            font-size: 12px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            font-size: 24px;
            font-weight: 600;
            color: #fff;
        }

        /* 5. KANBAN COLUMNS (The "Bin" Look) */
        div[data-testid="column"] {
            background-color: #181818;
            border-right: 1px solid #252525;
            padding: 8px;
            min-height: 80vh;
        }

        /* 6. JOB CARDS (Pro Asset Look) */
        .job-card {
            background-color: #252526; /* Surface 1 */
            border-left: 3px solid #444;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 2px;
            transition: all 0.1s;
        }
        .job-card:hover {
            background-color: #2D2D30; /* Hover State */
            border-left: 3px solid #007ACC; /* VS Code Blue */
            cursor: pointer;
        }
        
        /* 7. TEXT UTILS */
        .status-text { font-size: 11px; color: #888; font-family: 'JetBrains Mono', monospace; }
        .job-title { font-size: 13px; font-weight: 600; color: #E0E0E0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .section-header { 
            font-size: 11px; 
            font-weight: 700; 
            text-transform: uppercase; 
            color: #666; 
            margin-bottom: 10px; 
            border-bottom: 1px solid #333; 
            padding-bottom: 4px;
        }

        /* 8. BUTTONS (Flat Pro Style) */
        .stButton button {
            background-color: #333;
            color: white;
            border: none;
            border-radius: 2px;
            font-size: 12px;
            padding: 4px 12px;
        }
        .stButton button:hover {
            background-color: #007ACC;
            color: white;
        }

        /* LOG CONSOLE */
        .console-container {
            background-color: #0d0d0d;
            border: 1px solid #333;
            border-radius: 4px;
            padding: 10px;
            font-family: 'JetBrains Mono', 'Consolas', monospace;
            font-size: 11px;
            color: #00FF00;
            height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            margin-top: 10px;
        }
        .console-line {
            border-bottom: 1px solid #1a1a1a;
            padding: 2px 0;
        }
    </style>
""", unsafe_allow_html=True)

# --- BACKEND LOGIC ---

def get_log_tail(script_name, lines=50):
    """Reads the last N lines of the log file."""
    log_map = {
        "auto_skeleton.py": "ear.log",
        "cloud_brain.py": "brain.log",
        "finalize.py": "hand.log",
        "publisher.py": "publisher.log",
        "archivist.py": "archivist.log",
        "process_watchdog.py": "watchdog.log"
    }
    log_file = LOGS_DIR / log_map.get(script_name, "sys.log")
    
    if not log_file.exists():
        return ["Waiting for logs..."]
    
    try:
        # Efficient tailing for larger files
        with open(log_file, "rb") as f:
            try:
                f.seek(-10000, os.SEEK_END) # Go back ~10KB
            except OSError:
                f.seek(0) # File is smaller than 10KB
            
            last_lines = f.readlines()
            decoded = [line.decode('utf-8', errors='ignore') for line in last_lines]
            return decoded[-lines:]
    except Exception as e:
        return [f"Error reading log: {e}"]

def get_service_status(script_name):
    # Simulates service check
    log_map = {
        "auto_skeleton.py": "ear.log",
        "cloud_brain.py": "brain.log",
        "finalize.py": "hand.log",
        "publisher.py": "publisher.log",
        "archivist.py": "archivist.log"
    }
    log_file = LOGS_DIR / log_map.get(script_name, "sys.log")
    
    # Check if process is running
    is_running = False
    for proc in psutil.process_iter(['cmdline']):
        try:
            if proc.info['cmdline'] and script_name in ' '.join(proc.info['cmdline']):
                is_running = True
                break
        except: pass

    # Check log freshness
    freshness = 0
    if log_file.exists():
        freshness = time.time() - log_file.stat().st_mtime
    
    if is_running:
        if freshness < 120: return "RUNNING", "#4CAF50" # Material Green
        return "IDLE", "#FFC107" # Material Amber
    return "STOPPED", "#F44336" # Material Red

def restart_service(script_name):
    log_map = {
        "auto_skeleton.py": "ear.log",
        "cloud_brain.py": "brain.log",
        "finalize.py": "hand.log",
        "publisher.py": "publisher.log",
        "archivist.py": "archivist.log"
    }
    log_file = log_map.get(script_name, "sys.log")
    subprocess.run(["pkill", "-f", script_name])
    time.sleep(0.5)
    cmd = f"nohup python3 -u {script_name} > logs/{log_file} 2>&1 &"
    subprocess.Popen(cmd, shell=True)

# --- UI COMPONENTS ---

def render_sidebar():
    st.sidebar.markdown("### 🎛 SYSTEM MONITOR")
    
    # 1. Micro-Charts (Sparklines style)
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    
    st.sidebar.progress(cpu/100)
    st.sidebar.caption(f"CPU: {cpu}%")
    
    st.sidebar.progress(ram/100)
    st.sidebar.caption(f"RAM: {ram}%")
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔌 SERVICES")
    
    services = [
        ("EAR", "auto_skeleton.py"),
        ("BRAIN", "cloud_brain.py"),
        ("HAND", "finalize.py"),
        ("PUB", "publisher.py"),
        ("ARC", "archivist.py")
    ]
    
    for label, script in services:
        status, color = get_service_status(script)
        c1, c2, c3 = st.sidebar.columns([0.5, 2, 1])
        c1.markdown(f"<div style='background-color:{color}; width:8px; height:8px; border-radius:50%; margin-top:10px;'></div>", unsafe_allow_html=True)
        c2.caption(f"{label} ({status})")
        if c3.button("↺", key=f"rst_{label}"):
            restart_service(script)
            st.rerun()

def render_log_console():
    from datetime import datetime
    now_str = datetime.now().strftime("%H:%M:%S")
    st.sidebar.markdown(f"### 📟 LIVE TERMINAL <span style='font-size:10px; color:#666; float:right; margin-top:5px;'>{now_str}</span>", unsafe_allow_html=True)
    
    # Service Selector
    service_options = {
        "EAR": "auto_skeleton.py",
        "BRAIN": "cloud_brain.py",
        "HAND": "finalize.py",
        "PUB": "publisher.py",
        "ARC": "archivist.py",
        "DOG": "process_watchdog.py"
    }
    
    selected_service = st.sidebar.selectbox("Select Feed", list(service_options.keys()), index=1)
    script_name = service_options[selected_service]
    
    # Fetch Logs
    logs = get_log_tail(script_name, lines=30)
    
    # Render Console
    log_html = "".join([f"<div class='console-line'>{line}</div>" for line in logs])
    st.sidebar.markdown(f"""
    <div class="console-container">
        {log_html}
    </div>
    """, unsafe_allow_html=True)

def render_ingest_section():
    with st.sidebar.expander("📥 INGEST MEDIA", expanded=False):
        # Initialize Uploader Key
        if 'uploader_key' not in st.session_state:
            st.session_state['uploader_key'] = 0
            
        uploaded_file = st.file_uploader(
            "Drop Video/Audio Here", 
            type=['mp4', 'mov', 'mp3', 'wav', 'mkv'],
            key=f"uploader_{st.session_state['uploader_key']}"
        )
        
        if uploaded_file is not None:
            # Save to VIP_REVIEW
            save_dir = BASE_DIR / "1_INBOX" / "VIP_REVIEW"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            save_path = save_dir / uploaded_file.name
            
            # OVERWRITE LOGIC (User Request: No Duplicates)
            if save_path.exists():
                st.warning(f"⚠️ Overwriting existing file: {uploaded_file.name}")
            
            # Save with progress
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            st.success(f"✅ Uploaded: {save_path.name}")
            st.caption("System will detect shortly...")
            
            # FORCE RESET: Increment key to clear uploader on rerun
            st.session_state['uploader_key'] += 1
            time.sleep(2)
            st.rerun()

def render_job_card(job):
    """Renders a dense, pro-app style card."""
    stem = job['file_stem']
    stage = job['stage']
    status = job['status']
    progress = job.get('progress', 0)
    
    # Status Color Coding
    if "Error" in status:
        border_color = "#FF4B4B" # Red
    elif stage == "COMPLETED":
        border_color = "#00CC96" # Green
    elif stage == "VIP_REVIEW":
        border_color = "#FFAA00" # Amber
    else:
        border_color = "#29B5E8" # Blue

    # Card Container
    st.markdown(f"""
    <div style="
        background-color: #1E1E1E;
        border-radius: 4px;
        border-left: 4px solid {border_color};
        padding: 10px;
        margin-bottom: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    ">
        <div style="font-weight: 600; font-size: 13px; color: #E0E0E0; margin-bottom: 4px;">
            {stem}
        </div>
        <div style="font-size: 11px; color: #AAAAAA; margin-bottom: 8px;">
            {status}
        </div>
        <div style="background-color: #333; height: 4px; border-radius: 2px; width: 100%;">
            <div style="background-color: {border_color}; height: 4px; border-radius: 2px; width: {progress}%;"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("OPEN", key=f"btn_{stem}"):
        st.session_state['selected_job'] = job
        st.rerun()
    
    # Context Menu (Actions)
    # with st.expander("ACTIONS", expanded=False): # This was removed by the user's instruction
    #     c1, c2 = st.columns(2)
    #     if c1.button("OPEN", key=f"open_{stem}"):
    #         st.session_state['selected_job'] = job
    #         st.rerun()
    #     if c2.button("RETRY", key=f"retry_{stem}"):
    #         omega_db.update(stem, status="Retry Requested", progress=0)
    #         st.rerun()

def get_related_files(stem):
    """Finds all files related to a job stem."""
    found = []
    # Directories to search
    dirs = [
        BASE_DIR / "1_INBOX",
        BASE_DIR / "2_READY_FOR_CLOUD",
        BASE_DIR / "3_TRANSLATED_DONE",
        BASE_DIR / "4_OUTPUT",
        BASE_DIR / "99_ERRORS"
    ]
    
    for d in dirs:
        if d.exists():
            for f in d.glob(f"{stem}*"):
                found.append(f)
    return sorted(found, key=lambda x: x.name)

def review_interface(job):
    """The 'Edit Window' - mimicking a modal dialog."""
    st.markdown(f"## 🎞️ REVIEW: {job['file_stem']}")
    
    # Toolbar
    c1, c2, c3 = st.columns([1, 1, 6])
    if c1.button("← BACK"):
        del st.session_state['selected_job']
        st.rerun()
        
    st.markdown("---")
    
    # Determine File to Edit
    json_path = BASE_DIR / "3_TRANSLATED_DONE" / f"{job['file_stem']}_ICELANDIC.json"
    if not json_path.exists():
         json_path = BASE_DIR / "2_READY_FOR_CLOUD" / f"{job['file_stem']}_SKELETON.json"
    
    # TABS FOR INSPECTOR
    tab_editor, tab_files, tab_meta = st.tabs(["📝 TRANSLATION", "📂 ASSETS", "ℹ️ METADATA"])
    
    with tab_editor:
        col_editor, col_actions = st.columns([3, 1])
        with col_editor:
            if json_path.exists():
                with open(json_path, 'r', encoding='utf-8') as f:
                    raw_data = f.read()
                
                # Using text area as a raw code editor
                new_data = st.text_area("JSON EDITOR", value=raw_data, height=600)
                
                if st.button("💾 SAVE & APPROVE"):
                    try:
                        # Validate JSON
                        parsed = json.loads(new_data)
                        
                        # Save back
                        with open(json_path, 'w', encoding='utf-8') as f:
                            f.write(new_data)
                        
                        # Create Approval Flag
                        approve_path = json_path.parent / f"{job['file_stem']}_APPROVED.json"
                        with open(approve_path, 'w', encoding='utf-8') as f:
                            f.write(new_data) # Copy data to approved file
                            
                        omega_db.update(job['file_stem'], stage="FINALIZING", status="User Approved", progress=90)
                        st.success("Approved! Moving to render queue.")
                        time.sleep(1)
                        del st.session_state['selected_job']
                        st.rerun()
                    except Exception as e:
                        st.error(f"JSON Error: {e}")
            else:
                st.warning("File not found. System may be processing.")
        
        with col_actions:
             st.markdown("### ACTIONS")
             if st.button("✅ QUICK APPROVE"):
                 # Just rename/copy
                 if json_path.exists():
                     approve_path = json_path.parent / f"{job['file_stem']}_APPROVED.json"
                     import shutil
                     shutil.copy(json_path, approve_path)
                     omega_db.update(job['file_stem'], stage="FINALIZING", status="Quick Approved", progress=90)
                     st.success("Approved!")
                     time.sleep(1)
                     del st.session_state['selected_job']
                     st.rerun()

    with tab_files:
        st.markdown("### 🗂️ ASSET BROWSER")
        files = get_related_files(job['file_stem'])
        
        if not files:
            st.info("No files found on disk.")
        
        for f in files:
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{f.name}**")
            c1.caption(f"{f.parent.name} • {f.stat().st_size / 1024:.1f} KB")
            
            # Download Button
            with open(f, "rb") as file_content:
                c2.download_button("⬇️ DL", file_content, file_name=f.name)
            
            # Preview Button (if applicable)
            if f.suffix.lower() in ['.mp4', '.mov', '.mp3', '.wav']:
                if c3.checkbox(f"▶️ Play", key=f"play_{f.name}"):
                    st.video(str(f)) if f.suffix in ['.mp4', '.mov'] else st.audio(str(f))
            elif f.suffix.lower() in ['.json', '.srt', '.txt']:
                if c3.checkbox(f"👁️ Peek", key=f"peek_{f.name}"):
                    with open(f, 'r', encoding='utf-8', errors='ignore') as txt:
                        st.code(txt.read(), language='json' if f.suffix == '.json' else 'text')

    with tab_meta:
        st.markdown("### ℹ️ JOB DETAILS")
        st.json(job)
        
        st.markdown("### 🛠 ADMIN ACTIONS")
        action = st.selectbox("Force State / Danger", [
            "Select Action...",
            "Force: Retry Transcription (Reset to Audio)",
            "Force: Retry Translation (Reset to Cloud Ready)",
            "Force: Burn-In (Reset to Finalizing)",
            "NUKE: Delete Job & Files"
        ])
        
        if st.button("EXECUTE ACTION"):
            if "Transcription" in action:
                omega_db.update(job['file_stem'], stage="AUDIO", status="Forced Retry", progress=10)
                st.success("Reset to AUDIO stage.")
                time.sleep(1)
                st.rerun()
            elif "Translation" in action:
                omega_db.update(job['file_stem'], stage="CLOUD_READY", status="Forced Retry", progress=30)
                st.success("Reset to CLOUD_READY stage.")
                time.sleep(1)
                st.rerun()
            elif "Burn-In" in action:
                omega_db.update(job['file_stem'], stage="FINALIZING", status="Forced Retry", progress=90)
                st.success("Reset to FINALIZING stage.")
                time.sleep(1)
                st.rerun()
            elif "NUKE" in action:
                omega_db.delete_job(job['file_stem'])
                # Optional: Delete files? Maybe too dangerous for now.
                # Let's just remove from DB so it can be re-imported.
                st.warning("Job removed from Database. Files remain on disk.")
                time.sleep(1)
                del st.session_state['selected_job']
                st.rerun()

# --- MAIN LAYOUT ---

render_sidebar()
render_ingest_section() # Add Ingest Section
render_log_console() # Add Log Console to Sidebar

if 'selected_job' in st.session_state:
    review_interface(st.session_state['selected_job'])
else:
    # Top Stats Bar (The "Header")
    active_jobs = omega_db.get_all_jobs()
    active_jobs = [j for j in active_jobs if j['stage'] != "ARCHIVED"]
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("QUEUE DEPTH", len(active_jobs))
    k2.metric("PROCESSING", len([j for j in active_jobs if "Translating" in j['status']]))
    k3.metric("ATTN REQUIRED", len([j for j in active_jobs if "Review" in j['stage'] or "VIP" in j['stage']]))
    errors = len([j for j in active_jobs if "Error" in j['status']])
    k4.metric("SYSTEM ALERTS", errors, delta="-CRITICAL" if errors > 0 else "Normal", delta_color="inverse")

    st.markdown("---")
    
    # PRO KANBAN LAYOUT (5 Equal Columns with Headers)
    stages = ["INBOX", "AUDIO", "BRAIN", "REVIEW", "OUTPUT"]
    cols = st.columns(5)
    
    # Headers
    for i, stage in enumerate(stages):
        cols[i].markdown(f"<div class='section-header'>{stage}</div>", unsafe_allow_html=True)
    
    # Fill Columns
    for job in active_jobs:
        col_idx = 0
        s = job['stage']
        st_txt = job['status']
        
        if "Error" in st_txt: col_idx = 0 # Keep errors visible on left
        elif s in ["INBOX", "DETECTED"]: col_idx = 0
        elif s in ["AUDIO", "TRANSCRIPTION"]: col_idx = 1
        elif s in ["CLOUD_READY", "TRANSLATION", "TRANSLATING"]: col_idx = 2
        elif s in ["REVIEW", "VIP"]: col_idx = 3
        elif s in ["FINALIZING", "BURNING", "PUBLISHING", "COMPOSITING"]: col_idx = 4
        
        with cols[col_idx]:
            render_job_card(job)
