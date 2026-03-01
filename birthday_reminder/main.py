import datetime
import logging
import os
from zoneinfo import ZoneInfo

from google import genai
from telegram import Update
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
app: Application = ApplicationBuilder().token(BOT_TOKEN).defaults(defaults).build()
db: Database = Database()

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

generation_config = genai.types.GenerateContentConfig(
    temperature=1,
    top_p=0.95,
    top_k=40,
    max_output_tokens=8192,
    response_mime_type="text/plain",
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    await update.message.reply_text("hi!")


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
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=generation_config,
    )
    text = (
        escape_markdown("Today is the birthday of:", version=2)
        + "\n*"
        + escape_markdown(", ".join(bday), version=2).strip()
        + "*\n"
        + escape_markdown(f"\n{response.text}", version=2)
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


def main() -> None:
    log.info("setting up handlers...")
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
    app.add_handler(CommandHandler("list", listbday, block=False))
    app.add_handler(CommandHandler("refresh", refresh, block=False))
    log.info("done")
    app.run_polling(drop_pending_updates=True)
