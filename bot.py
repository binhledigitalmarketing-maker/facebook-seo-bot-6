"""
Telegram Bot: Facebook → Groq AI SEO → WordPress
Tác giả: Auto-generated
Phiên bản: 2.0 (Groq AI)
"""

import os
import re
import json
import logging
import requests
import base64
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from bs4 import BeautifulSoup
from groq import Groq

# ============================================================
# CẤU HÌNH LOGGING
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# BIẾN MÔI TRƯỜNG (đọc từ .env hoặc Railway Variables)
# ============================================================
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
WP_URL           = os.environ["WP_URL"].rstrip("/")          # https://yoursite.com
WP_USERNAME      = os.environ["WP_USERNAME"]
WP_APP_PASSWORD  = os.environ["WP_APP_PASSWORD"]
ALLOWED_USER_IDS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]

# Model Groq mặc định (có thể đổi sang llama-3.3-70b-versatile, gemma2-9b-it, v.v.)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

# Khởi tạo Groq client
groq_client = Groq(api_key=GROQ_API_KEY)

# Lưu trữ tạm thời (in-memory) cho preview
pending_posts: dict = {}

# ============================================================
# HELPER: Kiểm tra quyền user
# ============================================================
def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True  # Nếu không giới hạn → cho phép tất cả
    return user_id in ALLOWED_USER_IDS

# ============================================================
# SCRAPE NỘI DUNG FACEBOOK
# ============================================================
def scrape_facebook_post(url: str) -> dict:
    """
    Scrape nội dung từ link Facebook public.
    Trả về dict: { title, content, image_url }
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        og_title = ""
        og_desc  = ""
        og_image = ""

        og_t = soup.find("meta", property="og:title")
        og_d = soup.find("meta", property="og:description")
        og_i = soup.find("meta", property="og:image")

        if og_t:
            og_title = og_t.get("content", "")
        if og_d:
            og_desc = og_d.get("content", "")
        if og_i:
            og_image = og_i.get("content", "")

        body_text = ""
        for tag in soup.find_all(["p", "div", "span"]):
            text = tag.get_text(separator=" ", strip=True)
            if len(text) > 100:
                body_text += text + "\n\n"
                if len(body_text) > 2000:
                    break

        content = og_desc or body_text[:2000] or "Không lấy được nội dung."

        return {
            "title":     og_title or "Bài viết từ Facebook",
            "content":   content,
            "image_url": og_image,
            "source":    url,
        }

    except Exception as e:
        logger.error(f"Lỗi scrape: {e}")
        return {
            "title":     "Bài viết từ Facebook",
            "content":   "",
            "image_url": "",
            "source":    url,
        }

# ============================================================
# GROQ AI: Viết lại nội dung chuẩn SEO
# ============================================================
def rewrite_with_groq(raw_content: str, source_url: str = "") -> dict:
    """
    Gửi nội dung thô đến Groq AI, nhận về JSON chuẩn SEO.
    """
    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam. "
        "Nhiệm vụ của bạn là viết lại nội dung thành bài blog chuẩn SEO hoàn chỉnh bằng tiếng Việt. "
        "Chỉ trả về JSON thuần túy, không có markdown, không có backtick, không có text thêm vào."
    )

    user_prompt = f"""Viết lại nội dung dưới đây thành một bài viết blog chuẩn SEO hoàn chỉnh.

NỘI DUNG GỐC:
{raw_content}

YÊU CẦU:
- Tiêu đề SEO hấp dẫn (60-70 ký tự), chứa từ khóa chính
- Meta description (150-160 ký tự), tóm tắt bài viết
- Nội dung viết lại hoàn toàn bằng tiếng Việt, tự nhiên, không copy y chang
- Cấu trúc: Mở bài → 2-3 đoạn nội dung có tiêu đề H2 → Kết bài
- Từ khóa chính xuất hiện tự nhiên, không nhồi nhét
- Thêm call-to-action ở cuối bài
- Độ dài tối thiểu 400 từ

Trả về JSON theo đúng định dạng sau (không thêm bất kỳ text nào khác):
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1", "tag2", "tag3"],
  "category_suggestion": "..."
}}

Trong content_html, dùng thẻ HTML chuẩn: <h2>, <h3>, <p>, <ul>, <li>, <strong>"""

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=2048,
        )

        text = response.choices[0].message.content.strip()

        # Làm sạch markdown code block nếu model vẫn trả về
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)

        data = json.loads(text)
        return data

    except json.JSONDecodeError as e:
        logger.error(f"Groq trả về JSON lỗi: {e}\nText: {text[:500]}")
        return {
            "seo_title":           raw_content[:60],
            "meta_description":    raw_content[:155],
            "focus_keyword":       "",
            "content_html":        f"<p>{raw_content}</p>",
            "tags":                [],
            "category_suggestion": "Tin tức",
        }
    except Exception as e:
        logger.error(f"Lỗi Groq AI: {e}")
        raise

# ============================================================
# WORDPRESS: Upload ảnh
# ============================================================
def upload_image_to_wordpress(image_url: str) -> int | None:
    """Upload ảnh từ URL lên WordPress, trả về media ID."""
    if not image_url:
        return None
    try:
        img_resp = requests.get(image_url, timeout=15)
        if img_resp.status_code != 200:
            return None

        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        ext = "jpg" if "jpeg" in content_type else content_type.split("/")[-1]
        filename = f"fb-post-image.{ext}"

        auth = base64.b64encode(
            f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        }

        resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=img_resp.content,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        logger.error(f"Lỗi upload ảnh: {e}")
    return None

# ============================================================
# WORDPRESS: Đăng bài
# ============================================================
def post_to_wordpress(seo_data: dict, featured_media_id: int | None = None) -> dict:
    """Đăng bài lên WordPress, trả về thông tin bài đăng."""
    auth = base64.b64encode(
        f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type":  "application/json",
    }

    payload = {
        "title":   seo_data["seo_title"],
        "content": seo_data["content_html"],
        "status":  "publish",
        "excerpt": seo_data["meta_description"],
    }

    if featured_media_id:
        payload["featured_media"] = featured_media_id

    if seo_data.get("tags"):
        tag_ids = []
        for tag_name in seo_data["tags"]:
            tag_resp = requests.post(
                f"{WP_URL}/wp-json/wp/v2/tags",
                headers=headers,
                json={"name": tag_name},
                timeout=10,
            )
            if tag_resp.status_code in (200, 201):
                tag_ids.append(tag_resp.json()["id"])
        if tag_ids:
            payload["tags"] = tag_ids

    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/posts",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return

    text = (
        "👋 *Chào mừng đến với FB → WordPress Bot!*\n\n"
        "📌 *Cách dùng:*\n"
        "1️⃣ Gửi *link bài viết Facebook* → Bot tự scrape\n"
        "2️⃣ Hoặc gửi *nội dung text* trực tiếp\n\n"
        "⚡ Groq AI sẽ viết lại chuẩn SEO (cực nhanh!)\n"
        "✅ Xem preview rồi xác nhận đăng lên WordPress\n\n"
        "📎 Dùng /help để xem thêm lệnh"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 *Danh sách lệnh:*\n\n"
        "/start - Khởi động bot\n"
        "/help  - Xem hướng dẫn\n"
        "/status - Kiểm tra kết nối WordPress\n"
        "/model - Xem model Groq đang dùng\n\n"
        "💡 *Cách gửi nội dung:*\n"
        "• Gửi link Facebook: `https://www.facebook.com/...`\n"
        "• Gửi text trực tiếp: dán nội dung bài viết\n\n"
        "⚙️ Bot sẽ tự nhận dạng và xử lý phù hợp."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị model Groq đang được sử dụng."""
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"🤖 *Model Groq hiện tại:* `{GROQ_MODEL}`\n\n"
        f"💡 Thay đổi qua biến môi trường `GROQ_MODEL`.\n"
        f"Các model phổ biến:\n"
        f"• `llama-3.1-8b-instant` — Siêu nhanh\n"
        f"• `llama-3.3-70b-versatile` — Chất lượng cao\n"
        f"• `gemma2-9b-it` — Google Gemma 2\n"
        f"• `mixtral-8x7b-32768` — Mixtral",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("🔍 Đang kiểm tra kết nối WordPress...")

    try:
        auth = base64.b64encode(
            f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()
        ).decode()
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/posts?per_page=1",
            headers={"Authorization": f"Basic {auth}"},
            timeout=10,
        )
        if resp.status_code == 200:
            await update.message.reply_text(
                f"✅ *Kết nối WordPress thành công!*\n"
                f"🌐 URL: `{WP_URL}`\n"
                f"🤖 Groq Model: `{GROQ_MODEL}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ Lỗi kết nối: HTTP {resp.status_code}\n"
                f"Kiểm tra lại WP_URL và APP_PASSWORD"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Không kết nối được: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn nhận vào (link hoặc text)."""
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Không có quyền truy cập.")
        return

    user_input = update.message.text.strip()
    user_id    = update.effective_user.id

    is_fb_link = bool(re.match(
        r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/",
        user_input
    ))

    msg = await update.message.reply_text(
        "⏳ Đang xử lý..." if not is_fb_link
        else "🔍 Đang scrape bài Facebook..."
    )

    try:
        # Bước 1: Lấy nội dung
        if is_fb_link:
            scraped = scrape_facebook_post(user_input)
            raw_text = f"{scraped['title']}\n\n{scraped['content']}"
            image_url = scraped.get("image_url", "")
        else:
            raw_text  = user_input
            image_url = ""

        if len(raw_text.strip()) < 50:
            await msg.edit_text(
                "⚠️ Không lấy được nội dung đủ dài.\n\n"
                "📌 *Gợi ý:* Facebook hạn chế scrape, hãy thử:\n"
                "• Copy text bài viết và paste trực tiếp vào đây",
                parse_mode="Markdown"
            )
            return

        # Bước 2: Groq AI viết lại
        await msg.edit_text(f"⚡ Groq AI ({GROQ_MODEL}) đang viết lại nội dung chuẩn SEO...")
        seo_data = rewrite_with_groq(raw_text, user_input if is_fb_link else "")

        # Lưu vào pending
        pending_posts[user_id] = {
            "seo_data":  seo_data,
            "image_url": image_url,
            "source":    user_input if is_fb_link else "Text trực tiếp",
        }

        # Bước 3: Hiển thị preview
        preview = (
            f"📝 *PREVIEW BÀI VIẾT SEO*\n"
            f"{'━' * 30}\n\n"
            f"🏷️ *Tiêu đề SEO:*\n{seo_data['seo_title']}\n\n"
            f"📊 *Từ khóa chính:* `{seo_data.get('focus_keyword', 'N/A')}`\n\n"
            f"📋 *Meta Description:*\n_{seo_data['meta_description']}_\n\n"
            f"🏷️ *Tags:* {', '.join(seo_data.get('tags', []))}\n\n"
            f"📂 *Danh mục gợi ý:* {seo_data.get('category_suggestion', 'N/A')}\n\n"
            f"⚡ *Powered by:* Groq AI (`{GROQ_MODEL}`)\n\n"
            f"{'━' * 30}\n"
            f"📖 *Nội dung (rút gọn):*\n"
        )

        from html.parser import HTMLParser
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
            def handle_data(self, data):
                self.text.append(data)

        extractor = TextExtractor()
        extractor.feed(seo_data["content_html"])
        plain_text = " ".join(extractor.text)[:600]
        preview += f"{plain_text}...\n\n"

        if image_url:
            preview += f"🖼️ *Ảnh đại diện:* Có (từ Facebook)\n\n"

        preview += "👇 *Bạn muốn làm gì?*"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Đăng lên WordPress", callback_data=f"publish_{user_id}"),
                InlineKeyboardButton("❌ Huỷ", callback_data=f"cancel_{user_id}"),
            ],
            [
                InlineKeyboardButton("🔄 Viết lại khác", callback_data=f"rewrite_{user_id}"),
            ],
        ])

        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"handle_message error: {e}", exc_info=True)
        await msg.edit_text(
            f"❌ Có lỗi xảy ra: `{str(e)[:200]}`\n\nVui lòng thử lại.",
            parse_mode="Markdown"
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút bấm Inline Keyboard."""
    query   = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    data = query.data

    if data.startswith("cancel_"):
        pending_posts.pop(user_id, None)
        await query.edit_message_text("❌ Đã huỷ. Gửi link hoặc nội dung mới để tiếp tục.")

    elif data.startswith("rewrite_"):
        post_info = pending_posts.get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Vui lòng gửi lại.")
            return

        await query.edit_message_text(f"🔄 Groq AI đang viết lại với phong cách khác...")
        try:
            raw = post_info["seo_data"]["content_html"]
            seo_data = rewrite_with_groq(
                f"[Viết lại theo phong cách khác, sáng tạo hơn]\n{raw}"
            )
            pending_posts[user_id]["seo_data"] = seo_data

            preview = (
                f"🔄 *BẢN VIẾT LẠI MỚI*\n\n"
                f"🏷️ *Tiêu đề:* {seo_data['seo_title']}\n"
                f"📊 *Từ khóa:* `{seo_data.get('focus_keyword', '')}`\n"
                f"📋 *Meta:* _{seo_data['meta_description']}_\n\n"
                f"👇 Xác nhận đăng?"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Đăng lên WordPress", callback_data=f"publish_{user_id}"),
                    InlineKeyboardButton("❌ Huỷ", callback_data=f"cancel_{user_id}"),
                ],
            ])
            await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=keyboard)

        except Exception as e:
            await query.edit_message_text(f"❌ Lỗi viết lại: {e}")

    elif data.startswith("publish_"):
        post_info = pending_posts.get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Vui lòng gửi lại.")
            return

        await query.edit_message_text("📤 Đang đăng bài lên WordPress...")

        try:
            media_id = None
            if post_info.get("image_url"):
                await query.edit_message_text("🖼️ Đang upload ảnh đại diện...")
                media_id = upload_image_to_wordpress(post_info["image_url"])

            await query.edit_message_text("📝 Đang đăng bài...")
            result = post_to_wordpress(post_info["seo_data"], media_id)

            post_url  = result.get("link", "")
            post_id   = result.get("id", "")
            edit_url  = f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"

            pending_posts.pop(user_id, None)

            await query.edit_message_text(
                f"🎉 *Đăng bài thành công!*\n\n"
                f"📌 *Tiêu đề:* {post_info['seo_data']['seo_title']}\n"
                f"🔗 *Xem bài:* [Nhấn vào đây]({post_url})\n"
                f"✏️ *Chỉnh sửa:* [WP Admin]({edit_url})\n\n"
                f"⚡ *Viết bởi:* Groq AI (`{GROQ_MODEL}`)\n"
                f"✅ Bài đã được publish trên WordPress!",
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )

        except Exception as e:
            logger.error(f"Publish error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Lỗi đăng bài: `{str(e)[:300]}`\n\n"
                f"Kiểm tra lại cấu hình WordPress và thử lại.",
                parse_mode="Markdown"
            )


# ============================================================
# MAIN
# ============================================================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("model",  cmd_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info(f"🤖 Bot đang chạy với Groq AI model: {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
