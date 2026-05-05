import os
import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, request
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from anthropic import Anthropic
from supabase import create_client

from client_config import BUSINESS_NAME, SYSTEM_PROMPT, FOLLOW_UP_MESSAGE, RISK_DISCLAIMER

app = Flask(__name__)

ai = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
twilio = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
CLIENT_ID = os.environ["CLIENT_ID"]


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


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From", "").replace("whatsapp:", "")
    body = request.form.get("Body", "").strip()
    num_media = int(request.form.get("NumMedia", 0))

    resp = MessagingResponse()

    # Ignore empty messages
    if not body and num_media == 0:
        return str(resp)

    # Ignore images — this bot doesn't need screenshots
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
        resp.message(f"Welcome back! 😊 Let me start by sharing important information about {BUSINESS_NAME}.")
        update_lead(phone, {})
        resp.message(RISK_DISCLAIMER)
        return str(resp)

    # Risk disclaimer gate — show to all new users
    if not risk_accepted:
        if body.upper() == "I AGREE":
            update_lead(phone, {"risk_accepted": True, "status": "interested"})
            resp.message(
                f"Thank you for acknowledging the risks. 😊\n\n"
                f"Welcome! I'm here to tell you all about *{BUSINESS_NAME}* and how it works.\n\n"
                f"We have 3 active investment levels starting from as low as *UGX 10,000*. "
                f"You can earn daily income for 90 days and also earn referral commissions.\n\n"
                f"What would you like to know first?\n"
                f"1️⃣ Investment levels & returns\n"
                f"2️⃣ How withdrawals work\n"
                f"3️⃣ How to join\n"
                f"4️⃣ Referral commissions"
            )
        else:
            # Show disclaimer to new users regardless of what they said
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
    return f"{BUSINESS_NAME} WhatsApp Agent is live. ✅"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
