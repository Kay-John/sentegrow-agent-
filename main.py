import os
import threading
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, session, redirect, render_template, jsonify
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from anthropic import Anthropic
from supabase import create_client

from client_config import BUSINESS_NAME, SYSTEM_PROMPT, FOLLOW_UP_MESSAGE, RISK_DISCLAIMER

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sentegrow-secret-2026")

ai = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
twilio = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
CLIENT_ID = os.environ["CLIENT_ID"]
DASH_USER = os.environ.get("DASH_USERNAME", "Jusper001")
DASH_PASS = os.environ.get("DASH_PASSWORD", "admin256")

OUTREACH_MESSAGE = (
    "👋 Hi! I came across your number and wanted to share something exciting.\n\n"
    "I'm reaching out from *SenteGrow* — an investment platform where you can earn "
    "daily returns on your investment. 💰\n\n"
    "We have levels starting from just *UGX 10,000*.\n\n"
    "Interested to learn more? Just reply and I'll walk you through everything! 😊"
)


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


# ── WhatsApp Webhook ──────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From", "").replace("whatsapp:", "")
    body = request.form.get("Body", "").strip()
    num_media = int(request.form.get("NumMedia", 0))

    resp = MessagingResponse()

    if not body and num_media == 0:
        return str(resp)
    if num_media > 0 and not body:
        return str(resp)

    lead = get_or_create_lead(phone)
    history = lead.get("conversation_history") or []
    status = lead.get("status", "new")
    risk_accepted = lead.get("risk_accepted", False)

    # Opt-out
    if body.lower() in ["stop", "unsubscribe", "quit"]:
        update_lead(phone, {"opted_in": False, "status": "opted_out"})
        resp.message(f"You've been unsubscribed from {BUSINESS_NAME} messages. Reply START to re-subscribe anytime.")
        return str(resp)

    # Re-subscribe
    if body.lower() == "start" and status == "opted_out":
        update_lead(phone, {"opted_in": True, "status": "new", "risk_accepted": False})
        resp.message(RISK_DISCLAIMER)
        return str(resp)

    # Risk disclaimer gate
    if not risk_accepted:
        if body.upper() == "I AGREE":
            update_lead(phone, {"risk_accepted": True, "status": "interested"})
            resp.message(
                f"Thank you for acknowledging the risks. 😊\n\n"
                f"Welcome! I'm here to tell you all about *{BUSINESS_NAME}*.\n\n"
                f"We have 3 active investment levels starting from *UGX 10,000*. "
                f"Earn daily income for 90 days plus referral commissions.\n\n"
                f"What would you like to know?\n"
                f"1️⃣ Investment levels & returns\n"
                f"2️⃣ How withdrawals work\n"
                f"3️⃣ How to join\n"
                f"4️⃣ Referral commissions"
            )
        else:
            update_lead(phone, {"status": "new"})
            resp.message(RISK_DISCLAIMER)
        return str(resp)

    # Normal AI conversation
    reply = get_ai_reply(history, body)
    history = (history + [
        {"role": "user", "content": body},
        {"role": "assistant", "content": reply}
    ])[-20:]

    if status == "interested":
        status = "engaged"

    update_lead(phone, {"conversation_history": history, "status": status})
    resp.message(reply)
    return str(resp)


# ── Subscription page ─────────────────────────────────────────────────────────

@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "POST":
        sb.table("payment_submissions").insert({
            "name": request.form.get("name"),
            "phone": request.form.get("phone"),
            "method": request.form.get("method"),
            "amount": request.form.get("amount"),
            "reference": request.form.get("reference"),
            "message": request.form.get("message", ""),
            "client_id": CLIENT_ID,
            "submitted_at": datetime.now(timezone.utc).isoformat()
        }).execute()
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


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    leads = sb.table("bot_leads").select("*").eq("client_id", CLIENT_ID).order("last_message_at", desc=True).execute().data or []
    payments = sb.table("payment_submissions").select("*").eq("client_id", CLIENT_ID).order("submitted_at", desc=True).execute().data or []

    stats = {
        "total": len(leads),
        "interested": sum(1 for l in leads if l.get("status") == "interested"),
        "engaged": sum(1 for l in leads if l.get("status") == "engaged"),
        "payments": len(payments)
    }

    return render_template("dashboard.html", leads=leads, payments=payments, stats=stats, message=request.args.get("msg"), error=request.args.get("err"))


@app.route("/dashboard/send", methods=["POST"])
@login_required
def dashboard_send():
    phone = request.form.get("phone", "").strip().lstrip("+")
    if not phone:
        return redirect("/dashboard?err=Phone+number+is+required&tab=send")
    try:
        twilio.messages.create(
            body=OUTREACH_MESSAGE,
            from_=WHATSAPP_FROM,
            to=f"whatsapp:+{phone}"
        )
        # Create lead record so conversation is tracked
        r = sb.table("bot_leads").select("id").eq("phone", f"+{phone}").eq("client_id", CLIENT_ID).execute()
        if not r.data:
            sb.table("bot_leads").insert({
                "phone": f"+{phone}",
                "client_id": CLIENT_ID,
                "status": "new",
                "conversation_history": [],
                "opted_in": True,
                "risk_accepted": False
            }).execute()
        return redirect(f"/dashboard?msg=Message+sent+to+%2B{phone}")
    except Exception as e:
        return redirect(f"/dashboard?err={str(e)[:80]}")


@app.route("/dashboard/send-bulk", methods=["POST"])
@login_required
def dashboard_send_bulk():
    raw = request.form.get("phones", "")
    phones = [p.strip().lstrip("+") for p in raw.splitlines() if p.strip()][:50]
    sent, failed = 0, 0
    for phone in phones:
        try:
            twilio.messages.create(
                body=OUTREACH_MESSAGE,
                from_=WHATSAPP_FROM,
                to=f"whatsapp:+{phone}"
            )
            r = sb.table("bot_leads").select("id").eq("phone", f"+{phone}").eq("client_id", CLIENT_ID).execute()
            if not r.data:
                sb.table("bot_leads").insert({
                    "phone": f"+{phone}",
                    "client_id": CLIENT_ID,
                    "status": "new",
                    "conversation_history": [],
                    "opted_in": True,
                    "risk_accepted": False
                }).execute()
            sent += 1
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
                try:
                    twilio.messages.create(
                        body=FOLLOW_UP_MESSAGE,
                        from_=WHATSAPP_FROM,
                        to=f"whatsapp:{phone}"
                    )
                    sb.table("bot_leads").update({"followed_up": True}).eq("phone", phone).eq("client_id", CLIENT_ID).execute()
                    print(f"Follow-up sent to {phone}")
                except Exception as e:
                    print(f"Follow-up failed for {phone}: {e}")
        except Exception as e:
            print(f"Follow-up engine error: {e}")
        time.sleep(3600)


threading.Thread(target=follow_up_engine, daemon=True).start()


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return f"{BUSINESS_NAME} WhatsApp Agent is live. ✅ | <a href='/subscribe'>Subscribe</a> | <a href='/dashboard'>Dashboard</a>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
