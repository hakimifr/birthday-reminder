import calendar
import datetime
import logging
import os
from collections import Counter
from zoneinfo import ZoneInfo

from google import genai
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    Defaults,
)
from telegram.helpers import escape_markdown

from birthday_reminder import BOT_TOKEN, TZ
from birthday_reminder.database import Database

defaults: Defaults = Defaults(tzinfo=ZoneInfo(TZ))
log: logging.Logger = logging.getLogger(__name__)
db: Database = Database()

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

generation_config = genai.types.GenerateContentConfig(
    temperature=1,
    top_p=0.95,
    top_k=40,
    max_output_tokens=8192,
    response_mime_type="text/plain",
)

AUTHORIZED_USER_IDS: frozenset[int] = frozenset({1024853832, 7439953392})

BOT_COMMANDS: list[BotCommand] = [
    BotCommand("start", "say hi"),
    BotCommand("help", "list all available commands"),
    BotCommand("list", "show all birthdays"),
    BotCommand("refresh", "reload the birthday database"),
    BotCommand("today", "check today's birthdays"),
    BotCommand("upcoming", "birthdays in the next N days (default 7)"),
    BotCommand("month", "birthdays for a given month (default current)"),
    BotCommand("search", "search for a name"),
    BotCommand("stats", "aggregate stats (total, busiest month, etc.)"),
    BotCommand("daily", "manually run the daily birthday trigger"),
    BotCommand("monthly", "manually run the monthly birthday trigger"),
]

HELP_TEXT = (
    "Available commands:\n"
    "/start — say hi\n"
    "/list — show all birthdays\n"
    "/refresh — reload the birthday database\n"
    "/today — check today's birthdays\n"
    "/upcoming [days] — birthdays in the next N days (default 7)\n"
    "/month [month] — birthdays for a given month (default current)\n"
    "/search <name> — search for a name\n"
    "/stats — aggregate stats (total, busiest month, etc.)\n"
    "/daily — manually run the daily birthday trigger\n"
    "/monthly — manually run the monthly birthday trigger\n"
    "/help — list all available commands"
)


async def post_init(application: Application) -> None:
    """Sync the bot's command list with Telegram after the app initialises."""
    await application.bot.set_my_commands(BOT_COMMANDS)
    log.info("bot commands synced with Telegram")


def _is_authorized(update: Update) -> bool:
    """Return True if the message sender is in AUTHORIZED_USER_IDS."""
    if update.effective_user is None:
        return False
    return update.effective_user.id in AUTHORIZED_USER_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    await update.message.reply_text("hi!")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available commands with brief descriptions. Usage: /help"""
    assert update.message is not None
    await update.message.reply_text(
        escape_markdown(HELP_TEXT, version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def listbday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    bday: dict[str, dict[str, str]] = db.get_raw_data()
    text = "```\n"
    for key, value in bday.items():
        text += (
            escape_markdown(
                f"{' '.join(key.split(' ')[:3])} {value.get('day')}/{value.get('month')}/{value.get('year')}",
                version=2,
            )
            + "\n"
        )

    text += "\n```"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    db.prepare_database()
    await update.message.reply_text("refreshed db")


async def daily_trigger(context: ContextTypes.DEFAULT_TYPE) -> None:
    dt = datetime.datetime.now(ZoneInfo(TZ))
    bday: list[str] = db.get_birthday_for_day(dt.month, dt.day)
    if len(bday) == 0:
        return

    # chat_session = model.start_chat(history=[])
    prompt: str = "Generate a short birthday wish (20-50 words). Just one. Send it directly without any markdown formatting. Also wish for good stuff, gen-z style."

    # Special request
    if any(
        name in bday
        for name in ("AMMAR FAIZ BIN MOHD KAMAL", "MUHAMMAD HAZRIL HAZIM BIN NOORFARIQ")
    ):
        prompt += " Your output must be in Malay (Bahasa Melayu)."

    # response = await chat_session.send_message_async(prompt)
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=generation_config,
        )
        wish = response.text
    except Exception:
        log.exception("Gemini response generation failed")
        wish = "[cannot generate wish, check bot log. happy birthday!]"

    text = (
        escape_markdown("Today is the birthday of:", version=2)
        + "\n*"
        + escape_markdown(", ".join(bday), version=2).strip()
        + "*\n"
        + escape_markdown(f"\n{wish}", version=2)
        + escape_markdown(" ✨🥳✨", version=2)
    )

    msg = await context.bot.send_message(
        -1001264770246, text, parse_mode=ParseMode.MARKDOWN_V2
    )
    await msg.pin(disable_notification=False)


async def monthly_trigger(context: ContextTypes.DEFAULT_TYPE) -> None:
    dt = datetime.datetime.now(ZoneInfo(TZ))
    text: str = "People with their birthday this month:\n"
    bday: dict[str, dict[str, str]] = db.get_birthday_for_month(dt.month)

    for person, date_data in bday.items():
        text += f"{person} - {date_data.get('day')}-{date_data.get('month')}-{date_data.get('year')}\n"

    msg = await context.bot.send_message(
        -1001264770246,
        escape_markdown(text, version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    await msg.pin()


async def trigger_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    if not _is_authorized(update):
        await update.message.reply_text("unauthorized.")
        return
    await daily_trigger(context)
    await update.message.reply_text("daily trigger executed")


async def trigger_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    if not _is_authorized(update):
        await update.message.reply_text("unauthorized.")
        return
    await monthly_trigger(context)
    await update.message.reply_text("monthly trigger executed")


async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List birthdays in the next N days (default 7). Usage: /upcoming [days]"""
    assert update.message is not None

    days: int = 7
    if context.args:
        try:
            days = int(context.args[0])
            if days < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Usage: /upcoming [days] — days must be a positive integer."
            )
            return

    dt = datetime.datetime.now(ZoneInfo(TZ))
    results: list[tuple[datetime.date, list[str]]] = []

    for offset in range(1, days + 1):
        target = (dt + datetime.timedelta(days=offset)).date()
        people = db.get_birthday_for_day(target.month, target.day)
        if people:
            results.append((target, people))

    if not results:
        await update.message.reply_text(f"No birthdays in the next {days} day(s).")
        return

    lines: list[str] = [f"Upcoming birthdays (next {days} day(s)):"]
    for date, people in results:
        date_str = date.strftime("%d %b")
        lines.append(f"{date_str}: {', '.join(people)}")

    await update.message.reply_text(
        escape_markdown("\n".join(lines), version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Case-insensitive substring search for a name. Usage: /search <name>"""
    assert update.message is not None

    if not context.args:
        await update.message.reply_text("Usage: /search <name>")
        return

    query = " ".join(context.args).lower()
    all_data: dict[str, dict[str, str]] = db.get_raw_data()

    matches = {name: data for name, data in all_data.items() if query in name.lower()}

    if not matches:
        await update.message.reply_text(
            f'No results found for "{" ".join(context.args)}".'
        )
        return

    lines: list[str] = [f'Search results for "{" ".join(context.args)}":']
    for name, data in matches.items():
        lines.append(
            f"{name} — {data.get('day')}/{data.get('month')}/{data.get('year')}"
        )

    await update.message.reply_text(
        escape_markdown("\n".join(lines), version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read-only check for today's birthdays. Usage: /today"""
    assert update.message is not None

    dt = datetime.datetime.now(ZoneInfo(TZ))
    bday: list[str] = db.get_birthday_for_day(dt.month, dt.day)

    if not bday:
        await update.message.reply_text("No birthdays today.")
        return

    lines: list[str] = ["🎂 Today's birthdays:", *bday]
    await update.message.reply_text(
        escape_markdown("\n".join(lines), version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List birthdays for a given month (default: current). Usage: /month [month_number]"""
    assert update.message is not None

    dt = datetime.datetime.now(ZoneInfo(TZ))
    month_num: int = dt.month

    if context.args:
        try:
            month_num = int(context.args[0])
            if not 1 <= month_num <= 12:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Usage: /month [month_number] — month must be 1–12."
            )
            return

    bday: dict[str, dict[str, str]] = db.get_birthday_for_month(month_num)
    month_name = calendar.month_name[month_num]

    if not bday:
        await update.message.reply_text(f"No birthdays in {month_name}.")
        return

    lines: list[str] = [f"Birthdays in {month_name}:"]
    # Sort by day
    for person, date_data in sorted(
        bday.items(), key=lambda x: int(x[1].get("day", 0))
    ):
        lines.append(f"{date_data.get('day')} — {' '.join(person.split()[:3])}")

    await update.message.reply_text(
        escape_markdown("\n".join(lines), version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return aggregate birthday statistics. Usage: /stats"""
    assert update.message is not None

    all_data: dict[str, dict[str, str]] = db.get_raw_data()
    total = len(all_data)

    if total == 0:
        await update.message.reply_text("No data available.")
        return

    month_counts: Counter[int] = Counter(
        int(data.get("month", 0)) for data in all_data.values()
    )
    busiest_month_num, busiest_count = month_counts.most_common(1)[0]
    busiest_month_name = calendar.month_name[busiest_month_num]

    day_counts: Counter[str] = Counter(
        data.get("day", "?") for data in all_data.values()
    )
    busiest_day, busiest_day_count = day_counts.most_common(1)[0]

    lines = [
        "📊 Birthday stats:",
        f"Total entries: {total}",
        f"Busiest month: {busiest_month_name} ({busiest_count} birthdays)",
        f"Most common day-of-month: {busiest_day} ({busiest_day_count} people)",
        f"Months with no birthdays: {12 - len(month_counts)}",
    ]

    await update.message.reply_text(
        escape_markdown("\n".join(lines), version=2),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


def main() -> None:
    log.info("setting up handlers...")
    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .defaults(defaults)
        .post_init(post_init)
        .build()
    )
    assert app.job_queue is not None
    app.job_queue.run_monthly(
        callback=monthly_trigger,
        when=datetime.time(hour=0, minute=0, second=0, tzinfo=ZoneInfo(TZ)),
        day=1,
    )
    app.job_queue.run_daily(
        daily_trigger, datetime.time(hour=0, minute=0, second=1, tzinfo=ZoneInfo(TZ))
    )
    app.add_handler(CommandHandler("start", start, block=False))
    app.add_handler(CommandHandler("help", help_command, block=False))
    app.add_handler(CommandHandler("list", listbday, block=False))
    app.add_handler(CommandHandler("refresh", refresh, block=False))
    app.add_handler(CommandHandler("daily", trigger_daily, block=False))
    app.add_handler(CommandHandler("monthly", trigger_monthly, block=False))
    app.add_handler(CommandHandler("upcoming", upcoming, block=False))
    app.add_handler(CommandHandler("search", search, block=False))
    app.add_handler(CommandHandler("today", today, block=False))
    app.add_handler(CommandHandler("month", month, block=False))
    app.add_handler(CommandHandler("stats", stats, block=False))
    log.info("done")
    app.run_polling(drop_pending_updates=True)
