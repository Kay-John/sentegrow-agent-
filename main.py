import os
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, session, redirect, render_template, jsonify, send_from_directory
from anthropic import Anthropic
from supabase import create_client

from client_config import BUSINESS_NAME, SYSTEM_PROMPT, FOLLOW_UP_MESSAGE, RISK_DISCLAIMER

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sentegrow-secret-2026")

ai = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

CLIENT_ID        = os.environ["CLIENT_ID"]
DASH_USER        = os.environ.get("DASH_USERNAME", "Jusper001")
DASH_PASS        = os.environ.get("DASH_PASSWORD", "admin256")

# ── WAHA config ───────────────────────────────────────────────────────────────
WAHA_URL         = os.environ.get("WAHA_URL", "").rstrip("/")  # https://waha-xxx.railway.app
WAHA_API_KEY     = os.environ.get("WAHA_API_KEY", "")
BOT_SESSION      = os.environ.get("BOT_SESSION", "default")
OUTREACH_SESSION = os.environ.get("OUTREACH_SESSION", "default")
OWNER_PHONE      = os.environ.get("OWNER_PHONE", "256793482095")   # notification target

OUTREACH_MESSAGE = (
    "👋 Hi! I came across your number and wanted to share something exciting.\n\n"
    "I'm reaching out from *SenteGrow* — an investment platform where you can earn "
    "daily returns on your investment. 💰\n\n"
    "We have levels starting from just *UGX 10,000*.\n\n"
    "Interested to learn more? Just reply and I'll walk you through everything! 😊"
)


# ── WAHA helpers ──────────────────────────────────────────────────────────────

def wa_id(phone):
    """Convert +256771234567 → 256771234567@c.us"""
    return phone.lstrip("+").replace(" ", "") + "@c.us"


def waha_send(phone, text, waha_session=None):
    if not WAHA_URL:
        print(f"[WAHA] WAHA_URL not set — cannot send to {phone}")
        return False
    if waha_session is None:
        waha_session = BOT_SESSION
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    try:
        r = requests.post(
            f"{WAHA_URL}/api/sendText",
            json={"chatId": wa_id(phone), "text": text, "session": waha_session},
            headers=headers,
            timeout=15
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[WAHA] Send failed to {phone}: {e}")
        return False


# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/dashboard/login")
        return f(*args, **kwargs)
    return decorated


# ── Database helpers ──────────────────────────────────────────────────────────

def get_or_create_lead(phone):
    r = sb.table("bot_leads").select("*").eq("phone", phone).eq("client_id", CLIENT_ID).execute()
    if r.data:
        return r.data[0]
    r = sb.table("bot_leads").insert({
        "phone": phone,
        "client_id": CLIENT_ID,
        "status": "new",
        "conversation_history": [],
        "opted_in": True,
        "risk_accepted": False
    }).execute()
    return r.data[0]


def update_lead(phone, data):
    data["last_message_at"] = datetime.now(timezone.utc).isoformat()
    sb.table("bot_leads").update(data).eq("phone", phone).eq("client_id", CLIENT_ID).execute()


# ── AI response ───────────────────────────────────────────────────────────────

def get_ai_reply(history, user_message):
    clean_history = [m for m in history if m.get("content", "").strip()]
    messages = clean_history + [{"role": "user", "content": user_message}]
    r = ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    return r.content[0].text


# ── WhatsApp Webhook (WAHA) ───────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if data.get("event") != "message":
        return jsonify({"status": "ignored"})

    payload = data.get("payload", {})

    # Ignore messages the bot sent
    if payload.get("fromMe", False):
        return jsonify({"status": "ignored"})

    raw_from = payload.get("from", "")

    # Skip group and broadcast messages
    if "@g.us" in raw_from or "@broadcast" in raw_from:
        return jsonify({"status": "ignored"})

    raw_phone = raw_from.replace("@c.us", "")
    if not raw_phone:
        return jsonify({"status": "ignored"})

    phone = "+" + raw_phone.lstrip("+")
    body = (payload.get("body") or "").strip()
    has_media = payload.get("hasMedia", False)
    # Reply through whichever number received the message
    reply_session = data.get("session", BOT_SESSION)

    if not body and not has_media:
        return jsonify({"status": "ignored"})

    lead = get_or_create_lead(phone)
    history      = lead.get("conversation_history") or []
    status       = lead.get("status", "new")
    risk_accepted = lead.get("risk_accepted", False)

    # Opt-out
    if body.lower() in ["stop", "unsubscribe", "quit"]:
        update_lead(phone, {"opted_in": False, "status": "opted_out"})
        waha_send(phone, f"You've been unsubscribed from {BUSINESS_NAME} messages. Reply START to re-subscribe anytime.", waha_session=reply_session)
        return jsonify({"status": "ok"})

    # Re-subscribe
    if body.lower() == "start" and status == "opted_out":
        update_lead(phone, {"opted_in": True, "status": "new", "risk_accepted": False})
        waha_send(phone, RISK_DISCLAIMER, waha_session=reply_session)
        return jsonify({"status": "ok"})

    # Risk disclaimer gate
    if not risk_accepted:
        if body.upper() == "I AGREE":
            update_lead(phone, {"risk_accepted": True, "status": "interested"})
            waha_send(phone,
                f"Thank you for acknowledging the risks. 😊\n\n"
                f"Welcome! I'm here to tell you all about *{BUSINESS_NAME}*.\n\n"
                f"We have 3 active investment levels starting from *UGX 10,000*. "
                f"Earn daily income for 90 days plus referral commissions.\n\n"
                f"What would you like to know?\n"
                f"1️⃣ Investment levels & returns\n"
                f"2️⃣ How withdrawals work\n"
                f"3️⃣ How to join\n"
                f"4️⃣ Referral commissions",
                waha_session=reply_session
            )
        else:
            update_lead(phone, {"status": "new"})
            waha_send(phone, RISK_DISCLAIMER, waha_session=reply_session)
        return jsonify({"status": "ok"})

    # Normal AI conversation
    reply = get_ai_reply(history, body)
    history = (history + [
        {"role": "user", "content": body},
        {"role": "assistant", "content": reply}
    ])[-20:]

    if status == "interested":
        status = "engaged"

    update_lead(phone, {"conversation_history": history, "status": status})
    waha_send(phone, reply, waha_session=reply_session)
    return jsonify({"status": "ok"})


# ── Subscribe page ────────────────────────────────────────────────────────────

@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "POST":
        name      = request.form.get("name", "")
        phone     = request.form.get("phone", "")
        method    = request.form.get("method", "")
        amount    = request.form.get("amount", "")
        reference = request.form.get("reference", "")
        message   = request.form.get("message", "")

        sb.table("payment_submissions").insert({
            "name": name, "phone": phone, "method": method,
            "amount": amount, "reference": reference,
            "message": message, "client_id": CLIENT_ID,
            "submitted_at": datetime.now(timezone.utc).isoformat()
        }).execute()

        waha_send(
            "+" + OWNER_PHONE.lstrip("+"),
            f"💰 *NEW PAYMENT SUBMISSION — {BUSINESS_NAME}*\n\n"
            f"Name: {name}\nPhone: {phone}\nMethod: {method}\n"
            f"Amount: {amount}\nReference: {reference}\n"
            f"Message: {message or '—'}\n\n"
            f"Action: Verify payment and activate subscription in Supabase.",
            waha_session=BOT_SESSION
        )

        return render_template("subscribe.html", success=True)
    return render_template("subscribe.html", success=False)


# ── Dashboard auth ────────────────────────────────────────────────────────────

@app.route("/dashboard/login", methods=["GET", "POST"])
def dash_login():
    if request.method == "POST":
        if request.form.get("username") == DASH_USER and request.form.get("password") == DASH_PASS:
            session["logged_in"] = True
            return redirect("/dashboard")
        return render_template("dash_login.html", error="Invalid username or password.")
    return render_template("dash_login.html", error=None)


@app.route("/dashboard/logout")
def dash_logout():
    session.clear()
    return redirect("/dashboard/login")


# ── WAHA session management ───────────────────────────────────────────────────

def waha_headers():
    return {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}

@app.route("/dashboard/waha/start-session", methods=["POST"])
@login_required
def waha_start_session():
    name = request.json.get("session", "default")
    try:
        # Try to start existing session
        r = requests.post(f"{WAHA_URL}/api/sessions/{name}/start",
                          headers=waha_headers(), timeout=30)
        if r.status_code in (200, 201):
            return jsonify({"message": f"Session started. Loading QR..."})

        # Session doesn't exist yet — create and start
        r = requests.post(f"{WAHA_URL}/api/sessions",
                          json={"name": name, "start": True},
                          headers=waha_headers(), timeout=30)
        if r.status_code in (200, 201):
            return jsonify({"message": f"Session created and started."})

        # Already exists and running — that's fine
        if r.status_code == 422:
            return jsonify({"message": "Session already running. Click Show QR Code."})

        return jsonify({"message": f"Response: {r.status_code} — {r.text[:200]}"})
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500


@app.route("/dashboard/waha/restart-session", methods=["POST"])
@login_required
def waha_restart_session():
    name = request.json.get("session", "default")
    webhook_url = request.host_url.rstrip("/") + "/webhook"
    try:
        import time as t
        requests.post(f"{WAHA_URL}/api/sessions/{name}/stop", headers=waha_headers(), timeout=15)
        t.sleep(1)
        requests.delete(f"{WAHA_URL}/api/sessions/{name}", headers=waha_headers(), timeout=15)
        t.sleep(1)
        r = requests.post(
            f"{WAHA_URL}/api/sessions",
            json={"name": name, "start": True},
            headers=waha_headers(), timeout=30
        )
        return jsonify({"message": f"Session recreated. Scan the QR code to reconnect."})
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500


@app.route("/dashboard/waha/qr/<session_name>")
@login_required
def waha_qr(session_name):
    try:
        # Check current status — never restart a working session
        status_r = requests.get(f"{WAHA_URL}/api/sessions/{session_name}",
                                headers=waha_headers(), timeout=10)
        if status_r.status_code == 200:
            info = status_r.json()
            if info.get("status") == "WORKING":
                return jsonify({"connected": True, "message": "✅ WhatsApp connected and running!"})

        # Session is stopped or scan needed — start it
        requests.post(f"{WAHA_URL}/api/sessions/{session_name}/start",
                      headers=waha_headers(), timeout=30)

        import time as t
        t.sleep(2)

        import base64
        r = requests.get(f"{WAHA_URL}/api/{session_name}/auth/qr?format=image",
                         headers=waha_headers(), timeout=30)
        if r.status_code == 200:
            img_b64 = base64.b64encode(r.content).decode()
            return jsonify({"qr": f"data:image/png;base64,{img_b64}"})
        return jsonify({"error": r.json().get("error", "Could not get QR — try again")}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/waha/sessions")
@login_required
def waha_sessions():
    try:
        r = requests.get(f"{WAHA_URL}/api/sessions", headers=waha_headers(), timeout=30)
        return jsonify(r.json() if r.status_code == 200 else [])
    except Exception as e:
        return jsonify([])


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    leads    = sb.table("bot_leads").select("*").eq("client_id", CLIENT_ID).order("last_message_at", desc=True).execute().data or []
    payments = sb.table("payment_submissions").select("*").eq("client_id", CLIENT_ID).order("submitted_at", desc=True).execute().data or []

    stats = {
        "total":      len(leads),
        "interested": sum(1 for l in leads if l.get("status") == "interested"),
        "engaged":    sum(1 for l in leads if l.get("status") == "engaged"),
        "payments":   len(payments)
    }

    return render_template(
        "dashboard.html",
        leads=leads, payments=payments, stats=stats,
        message=request.args.get("msg"),
        error=request.args.get("err")
    )


@app.route("/dashboard/send", methods=["POST"])
@login_required
def dashboard_send():
    phone = request.form.get("phone", "").strip()
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("+")
    if len(phone) < 8:
        return redirect("/dashboard?err=Phone+number+is+required")
    try:
        waha_send(phone, OUTREACH_MESSAGE, waha_session=OUTREACH_SESSION)
        r = sb.table("bot_leads").select("id").eq("phone", phone).eq("client_id", CLIENT_ID).execute()
        if not r.data:
            sb.table("bot_leads").insert({
                "phone": phone, "client_id": CLIENT_ID, "status": "new",
                "conversation_history": [], "opted_in": True, "risk_accepted": False
            }).execute()
        return redirect(f"/dashboard?msg=Outreach+sent+to+{phone}")
    except Exception as e:
        return redirect(f"/dashboard?err={str(e)[:80]}")


@app.route("/dashboard/send-bulk", methods=["POST"])
@login_required
def dashboard_send_bulk():
    raw    = request.form.get("phones", "")
    phones = [p.strip() for p in raw.splitlines() if p.strip()][:50]
    sent, failed = 0, 0
    for p in phones:
        if not p.startswith("+"):
            p = "+" + p.lstrip("+")
        try:
            waha_send(p, OUTREACH_MESSAGE, waha_session=OUTREACH_SESSION)
            r = sb.table("bot_leads").select("id").eq("phone", p).eq("client_id", CLIENT_ID).execute()
            if not r.data:
                sb.table("bot_leads").insert({
                    "phone": p, "client_id": CLIENT_ID, "status": "new",
                    "conversation_history": [], "opted_in": True, "risk_accepted": False
                }).execute()
            sent += 1
            time.sleep(45)  # 45s delay between each outreach to avoid spam detection
        except:
            failed += 1
    return redirect(f"/dashboard?msg=Sent+{sent}+messages.+{failed}+failed.")


# ── Follow-up engine ──────────────────────────────────────────────────────────

def follow_up_engine():
    while True:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            leads = (
                sb.table("bot_leads")
                .select("*")
                .eq("client_id", CLIENT_ID)
                .eq("followed_up", False)
                .eq("opted_in", True)
                .eq("risk_accepted", True)
                .in_("status", ["interested", "engaged"])
                .lt("last_message_at", cutoff)
                .execute()
            )
            for lead in leads.data:
                phone = lead["phone"]
                if waha_send(phone, FOLLOW_UP_MESSAGE, waha_session=BOT_SESSION):
                    sb.table("bot_leads").update({"followed_up": True}).eq("phone", phone).eq("client_id", CLIENT_ID).execute()
                    print(f"[follow-up] sent to {phone}")
        except Exception as e:
            print(f"[follow-up] engine error: {e}")
        time.sleep(3600)


threading.Thread(target=follow_up_engine, daemon=True).start()


# ── Static & home ─────────────────────────────────────────────────────────────

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


@app.route("/health")
def health():
    return "ok", 200


@app.route("/")
def home():
    return f"{BUSINESS_NAME} WhatsApp Agent is live. ✅ | <a href='/subscribe'>Subscribe</a> | <a href='/dashboard'>Dashboard</a>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
