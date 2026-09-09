
import os, re, calendar
from datetime import date, timedelta
from collections import defaultdict
import psycopg
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN=os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL=os.environ["DATABASE_URL"]
ALLOWED=os.getenv("ALLOWED_TELEGRAM_USER_ID","").strip()

KEYBOARD=ReplyKeyboardMarkup(
    [["📊 This week","🗓 This month"],["🚨 Alerts","💡 Suggestions"],["🧾 Recent","❓ Help"]],
    resize_keyboard=True
)

ALIASES={
"g":"Grocery","grocery":"Grocery","groceries":"Grocery",
"e":"Eating Out","eat":"Eating Out","eating":"Eating Out","food":"Eating Out",
"p":"Petrol","petrol":"Petrol","fuel":"Petrol",
"sub":"Subscription","airport":"Airport","ed":"Education","education":"Education",
"medicine":"Medicine","med":"Medicine","donation":"Donation","gift":"Gift",
"tax":"Tax","card":"Card","car":"Car","parking":"Parking","training":"Training",
"haircut":"Personal Care","sephora":"Personal Care","flower":"Gift","committee":"Committee"
}

def conn():
    return psycopg.connect(DATABASE_URL)

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS expenses(
            id BIGSERIAL PRIMARY KEY,
            expense_date DATE NOT NULL,
            category TEXT NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            merchant TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        defaults={"overall":"525","food":"220","grocery":"160","eating":"60","petrol":"175"}
        for k,v in defaults.items():
            c.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO NOTHING",(k,v))

def setting(k):
    with conn() as c:
        r=c.execute("SELECT value FROM settings WHERE key=%s",(k,)).fetchone()
    return float(r[0])

def set_setting(k,v):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(k,str(v)))

def allowed(update):
    return not ALLOWED or str(update.effective_user.id)==ALLOWED

def add_exp(d,cat,amt,merchant=""):
    with conn() as c:
        r=c.execute("INSERT INTO expenses(expense_date,category,amount,merchant) VALUES(%s,%s,%s,%s) RETURNING id",(d,cat,amt,merchant)).fetchone()
    return r[0]

def between(s,e):
    with conn() as c:
        return c.execute("SELECT id,expense_date,category,amount,merchant FROM expenses WHERE expense_date BETWEEN %s AND %s ORDER BY expense_date,id",(s,e)).fetchall()

def summary(s,e):
    rows=between(s,e); cats=defaultdict(float); total=0
    for _,_,cat,amt,_ in rows:
        amt=float(amt); cats[cat]+=amt; total+=amt
    return total,dict(cats),len(rows)

def week_bounds(d=None):
    d=d or date.today(); s=d-timedelta(days=d.weekday()); return s,s+timedelta(days=6)

def month_bounds(y,m):
    return date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])

def quarter_bounds(d=None):
    d=d or date.today(); q=(d.month-1)//3; sm=q*3+1
    return date(d.year,sm,1),date(d.year,sm+2,calendar.monthrange(d.year,sm+2)[1])

def fmt(title,s,e):
    total,cats,n=summary(s,e)
    lines=[f"📊 {title}",f"Total: ${total:,.2f}",f"Transactions: {n}",""]
    for k,v in sorted(cats.items(),key=lambda x:x[1],reverse=True):
        pct=(v/total*100) if total else 0
        lines.append(f"• {k}: ${v:,.2f} ({pct:.0f}%)")
    return "\n".join(lines)

def alert_text():
    s,e=week_bounds(); total,cats,_=summary(s,e)
    food=cats.get("Grocery",0)+cats.get("Eating Out",0)
    checks=[("Overall",total,setting("overall")),("Food",food,setting("food")),
            ("Grocery",cats.get("Grocery",0),setting("grocery")),
            ("Eating Out",cats.get("Eating Out",0),setting("eating")),
            ("Petrol",cats.get("Petrol",0),setting("petrol"))]
    out=[]
    for name,spent,lim in checks:
        if spent>=lim: out.append(f"🚨 {name}: ${spent:.2f}/${lim:.2f} — over by ${spent-lim:.2f}")
        elif spent>=lim*.8: out.append(f"⚠️ {name}: ${spent:.2f}/${lim:.2f} — {spent/lim*100:.0f}% used")
    return "\n".join(out) if out else "✅ No budget alerts right now."

def suggestions():
    s,e=week_bounds(); _,cats,_=summary(s,e); tips=[]
    food=cats.get("Grocery",0)+cats.get("Eating Out",0)
    if food>setting("food"): tips.append("Food is above target. Keep the next meals low-cost and avoid another large grocery top-up.")
    if cats.get("Eating Out",0)>setting("eating"): tips.append("Eating out is above target. Prefer home-prepared meals for the rest of the week.")
    if cats.get("Grocery",0)>setting("grocery"): tips.append("Groceries are above target. Use a planned list and avoid extra top-up shops.")
    if cats.get("Petrol",0)>setting("petrol"): tips.append("Petrol is above target. If work-related, compare it with your driving income before treating it as overspending.")
    if not tips: tips=["Your main tracked categories are within target. Keep the same pace."]
    return "💡 Suggestions\n"+"\n".join("• "+x for x in tips)

def cat(x): return ALIASES.get(x.lower(),x.title())

def parse_entry(t):
    m=re.match(r'^([A-Za-z]+)\s+\$?(\d+(?:\.\d+)?)(?:\s+(.*))?$',t.strip())
    if m: return cat(m.group(1)),float(m.group(2)),(m.group(3) or "").strip()
    m=re.match(r'^\$?(\d+(?:\.\d+)?)\s+([A-Za-z]+)(?:\s+(.*))?$',t.strip())
    if m: return cat(m.group(2)),float(m.group(1)),(m.group(3) or "").strip()
    return None

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return await update.message.reply_text("This SpendSense bot is private.")
    await update.message.reply_text(
        f"👋 SpendSense is ready.\nYour Telegram user ID: {update.effective_user.id}\n\n"
        "Try:\nG 45 Woolworths\n87 petrol\nE 14.50 lunch\n\n"
        "Commands: /week /month /quarter /alerts /recent /undo",
        reply_markup=KEYBOARD)

async def week(update,context):
    if not allowed(update): return
    s,e=week_bounds()
    await update.message.reply_text(fmt("This week",s,e)+"\n\n"+alert_text()+"\n\n"+suggestions())

async def month(update,context):
    if not allowed(update): return
    t=date.today(); s,e=month_bounds(t.year,t.month)
    await update.message.reply_text(fmt("This month",s,e))

async def quarter(update,context):
    if not allowed(update): return
    s,e=quarter_bounds()
    await update.message.reply_text(fmt("This quarter",s,e))

async def alerts(update,context):
    if not allowed(update): return
    await update.message.reply_text(alert_text()+"\n\n"+suggestions())

async def recent(update,context):
    if not allowed(update): return
    with conn() as c:
        rows=c.execute("SELECT id,expense_date,category,amount,merchant FROM expenses ORDER BY id DESC LIMIT 10").fetchall()
    if not rows: return await update.message.reply_text("No expenses yet.")
    await update.message.reply_text("\n".join(["🧾 Recent"]+[f"#{r[0]} {r[1]} • {r[2]} • ${float(r[3]):.2f}"+(f" — {r[4]}" if r[4] else "") for r in rows]))

async def undo(update,context):
    if not allowed(update): return
    with conn() as c:
        r=c.execute("SELECT id,category,amount FROM expenses ORDER BY id DESC LIMIT 1").fetchone()
        if not r: return await update.message.reply_text("Nothing to undo.")
        c.execute("DELETE FROM expenses WHERE id=%s",(r[0],))
    await update.message.reply_text(f"↩️ Deleted #{r[0]}: {r[1]} ${float(r[2]):.2f}")

async def text(update,context):
    if not allowed(update): return await update.message.reply_text("This SpendSense bot is private.")
    t=update.message.text.strip(); low=t.lower()
    if t=="📊 This week" or "this week" in low: return await week(update,context)
    if t=="🗓 This month" or "this month" in low: return await month(update,context)
    if t=="🚨 Alerts" or "alert" in low: return await alerts(update,context)
    if t=="💡 Suggestions" or "suggest" in low or "overspend" in low:
        return await update.message.reply_text(suggestions()+"\n\n"+alert_text())
    if t=="🧾 Recent" or "recent" in low: return await recent(update,context)
    if t=="❓ Help" or low=="help":
        return await update.message.reply_text("Examples:\nG 45 Woolworths\nP 87\nE 14.50 lunch\nSet grocery budget to 160\n/week /month /quarter /alerts /recent /undo")

    m=re.search(r'set\s+(grocery|eating out|eating|petrol|food|weekly|overall)\s+(?:weekly\s+)?budget\s+to\s+\$?(\d+(?:\.\d+)?)',low)
    if m:
        keys={"grocery":"grocery","eating out":"eating","eating":"eating","petrol":"petrol","food":"food","weekly":"overall","overall":"overall"}
        set_setting(keys[m.group(1)],float(m.group(2)))
        return await update.message.reply_text(f"✅ Budget updated to ${float(m.group(2)):.2f} per week.")

    item=parse_entry(t)
    if item:
        category,amount,merchant=item
        d=date.today()-timedelta(days=1) if "yesterday" in merchant.lower() else date.today()
        merchant=re.sub(r'(?i)\byesterday\b','',merchant).strip()
        eid=add_exp(d,category,amount,merchant)
        return await update.message.reply_text(f"✅ Added #{eid}\n{category}: ${amount:.2f}"+(f" — {merchant}" if merchant else "")+"\n\n"+alert_text())
    await update.message.reply_text("I couldn't understand that yet. Try `G 45 Woolworths`, `P 87`, or `/week`.")

def main():
    init_db()
    app=Application.builder().token(TOKEN).build()
    for cmd,fn in [("start",start),("week",week),("month",month),("quarter",quarter),("alerts",alerts),("recent",recent),("undo",undo)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
