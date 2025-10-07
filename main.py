import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from datetime import datetime, timedelta
import pytz
import json
import os
from flask import Flask, request, render_template_string, jsonify
import threading
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# ---------------- LOAD ENV ----------------
load_dotenv()

# ---------------- CONFIG ----------------
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = "rifat_session"  # Telegram session file
SCHEDULE_FILE = "schedule.json"
TIMEZONE = "Asia/Dhaka"
PORT = int(os.getenv("PORT", 10000))
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- FLASK ----------------
app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ---------------- LOGS ----------------
LOG_HISTORY = []

def add_log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_HISTORY.append(f"[{now}] {msg}")
    if len(LOG_HISTORY) > 300:
        LOG_HISTORY.pop(0)
    print(msg)

# ---------------- GLOBALS ----------------
client = None
scheduler_running = False
login_state = {"stage": "none", "phone": None, "code_sent": False}

# ---------------- HTML DASHBOARD ----------------
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Telegram Scheduler Dashboard</title>
<style>
body{font-family:sans-serif;padding:20px;color:#333;}
h2{text-align:center;}
textarea{width:100%;height:200px;font-family:monospace;border-radius:8px;padding:10px;border:1px solid #ccc;}
button{padding:10px 15px;margin:5px;cursor:pointer;border-radius:6px;border:none;background:#007bff;color:white;}
pre{background:#000;color:#0f0;padding:10px;height:200px;overflow-y:scroll;border-radius:8px;}
input[type="text"], input[type="password"]{width:100%;padding:8px;margin:5px 0;border-radius:6px;border:1px solid #ccc;}
</style>
<script>
async function reloadLogs(){const res=await fetch('/logs');const data=await res.json();document.getElementById('logs').innerText=data.logs.join("\\n");}
setInterval(reloadLogs,3000);

async function startScheduler(){await fetch('/start',{method:'POST'});reloadLogs();}
async function stopScheduler(){await fetch('/stop',{method:'POST'});reloadLogs();}
async function reloadScheduler(){await fetch('/reload',{method:'POST'});reloadLogs();}
</script>
</head>
<body>
<h2>📅 Telegram Scheduler Dashboard</h2>

{% if login_required %}
<form method="POST" action="/login">
<h3>🔑 Telegram Login</h3>
{% if login_state.stage=="code" %}
<label>Enter code sent to {{login_state.phone}}</label>
<input type="text" name="code" required>
{% elif login_state.stage=="password" %}
<label>Two-factor password:</label>
<input type="password" name="password" required>
{% else %}
<label>Phone number (with country code, e.g., +8801xxxxxx)</label>
<input type="text" name="phone" required>
{% endif %}
<button type="submit">Login</button>
</form>
{% else %}
<form method="POST" action="/update" enctype="multipart/form-data">
<h3>📝 Edit schedule.json & Upload Files</h3>
<textarea name="data">{{ data }}</textarea><br>
<label>Upload files (per task):</label>
<input type="file" name="files" multiple><br>
<button type="submit">💾 Save Schedule</button>
</form>

<h3>⚙️ Controls</h3>
<button onclick="startScheduler()">▶️ Start Scheduler</button>
<button onclick="stopScheduler()">⏹ Stop Scheduler</button>
<button onclick="reloadScheduler()">🔁 Reload Schedule</button>

<h3>📜 Live Logs</h3>
<pre id="logs">Loading logs...</pre>
{% endif %}
</body>
</html>
"""

# ---------------- TELEGRAM ----------------
async def ensure_client():
    global client
    if client is None:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await client.connect()
    elif not client.is_connected():
        await client.connect()
    return client

async def send_message(to, message, file_path=None):
    c = await ensure_client()
    try:
        if file_path and os.path.exists(file_path):
            await c.send_file(to, file_path, caption=message)
        else:
            await c.send_message(to, message)
        add_log(f"✅ Sent to {to}: {message} {'with file '+file_path if file_path else ''}")
    except Exception as e:
        add_log(f"❌ Failed to send to {to}: {e}")

# ---------------- SCHEDULER ----------------
def load_schedules():
    if not os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE,"w",encoding="utf-8") as f: json.dump([],f)
        return []
    with open(SCHEDULE_FILE,"r",encoding="utf-8") as f:
        return json.load(f)

async def schedule_task_runner(task):
    tz = pytz.timezone(TIMEZONE)
    while scheduler_running:
        now = datetime.now(tz)
        send_time = None

        if task["type"]=="date":
            send_time = tz.localize(datetime.strptime(task["when"], "%Y-%m-%d %H:%M"))
        elif task["type"]=="cron":
            hh, mm = map(int, task.get("time","00:00").split(":"))
            send_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if send_time < now: send_time += timedelta(days=1)
        else:
            break

        delta = (send_time - now).total_seconds()
        if delta>0: await asyncio.sleep(delta)

        file_path = task.get("file")
        if file_path: file_path = os.path.join(UPLOAD_FOLDER,file_path)
        await send_message(task["to"], task["message"], file_path)

        if task["type"]=="date": break
        await asyncio.sleep(60)

def start_scheduler():
    global scheduler_running
    if scheduler_running:
        add_log("⚠️ Scheduler already running.")
        return
    scheduler_running = True

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_scheduler_tasks())

    threading.Thread(target=run_loop, daemon=True).start()

async def run_scheduler_tasks():
    tasks = load_schedules()
    runners = [asyncio.create_task(schedule_task_runner(task)) for task in tasks]
    add_log("✅ Scheduler started.")
    if runners: await asyncio.gather(*runners)
    add_log("🛑 Scheduler finished.")

def stop_scheduler():
    global scheduler_running
    scheduler_running = False
    add_log("🛑 Stop signal sent.")

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def dashboard():
    login_required = not os.path.exists(SESSION_NAME + ".session")
    return render_template_string(HTML_DASHBOARD,
                                  data=open(SCHEDULE_FILE).read() if os.path.exists(SCHEDULE_FILE) else "",
                                  login_required=login_required,
                                  login_state=login_state)

@app.route("/login", methods=["POST"])
def login_route():
    global login_state, client
    phone = request.form.get("phone")
    code = request.form.get("code")
    password = request.form.get("password")

    async def login_async():
        global client, login_state
        if client is None:
            client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
            await client.connect()
        try:
            # Step 1: send code
            if login_state["stage"]=="none" and phone:
                await client.send_code_request(phone)
                login_state = {"stage":"code","phone":phone, "code_sent": True}
                add_log(f"📩 Code sent to {phone}")
                return

            # Step 2: enter code
            elif login_state["stage"]=="code" and code:
                try:
                    await client.sign_in(login_state["phone"], code)
                except SessionPasswordNeededError:
                    login_state["stage"]="password"
                    add_log("🔒 Two-factor password required.")
                    return
                add_log("✅ Logged in successfully!")
                login_state["stage"]="none"

            # Step 3: enter 2FA password
            elif login_state["stage"]=="password" and password:
                await client.sign_in(login_state["phone"], password=password)
                add_log("✅ Logged in with 2FA successfully!")
                login_state["stage"]="none"

        except PhoneCodeInvalidError:
            add_log("❌ Invalid code. Retry.")
        except Exception as e:
            add_log(f"❌ Login error: {e}")

    # Run async safely in background thread
    threading.Thread(target=lambda: asyncio.run(login_async()), daemon=True).start()
    return "✅ Login attempt done. Refresh dashboard."

@app.route("/update", methods=["POST"])
def update_schedule():
    try:
        data = json.loads(request.form["data"])
        files = request.files.getlist("files")
        for i,file in enumerate(files):
            if i<len(data):
                filename = secure_filename(file.filename)
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                data[i]["file"] = filename
        with open(SCHEDULE_FILE,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,ensure_ascii=False)
        add_log("💾 schedule.json updated.")
        return "✅ Saved successfully!"
    except Exception as e:
        add_log(f"❌ Save failed: {e}")
        return f"❌ {e}"

@app.route("/start",methods=["POST"])
def start_route(): start_scheduler(); return jsonify({"status":"started"})
@app.route("/stop",methods=["POST"])
def stop_route(): stop_scheduler(); return jsonify({"status":"stopped"})
@app.route("/reload",methods=["POST"])
def reload_route(): add_log("🔁 Reloaded."); return jsonify({"status":"reloaded"})
@app.route("/logs")
def logs_route(): return jsonify({"logs":LOG_HISTORY})

# ---------------- MAIN ----------------
if __name__=="__main__":
    add_log("🌐 Dashboard ready. Telegram login required.")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
