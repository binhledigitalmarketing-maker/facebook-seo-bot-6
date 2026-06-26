"""
Telegram Bot: Facebook → Groq AI SEO → WordPress
Phiên bản: 4.0
- Gửi ảnh + caption  → viết bài + thumbnail
- Gửi link Facebook  → scrape + viết bài
- Gửi text thuần    → viết bài từ nội dung
- Nhắn yêu cầu chỉnh sau preview → bot viết lại theo yêu cầu
- Dữ liệu bền vững qua restart (lưu file JSON)
"""

import os
import re
import json
import logging
import requests
import base64
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from bs4 import BeautifulSoup
from groq import Groq

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# BIẾN MÔI TRƯỜNG
# ============================================================
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
WP_URL           = os.environ["WP_URL"].rstrip("/")
WP_USERNAME      = os.environ["WP_USERNAME"]
WP_APP_PASSWORD  = os.environ["WP_APP_PASSWORD"]
ALLOWED_USER_IDS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]
GROQ_MODEL       = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# LƯU TRỮ BỀN VỮNG (JSON file, tồn tại qua restart)
# ============================================================
PENDING_FILE = "/tmp/pending_posts.json"

def _load_pending() -> dict:
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_pending(data: dict):
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi lưu pending: {e}")

def pending_set(user_id: int, value: dict):
    data = _load_pending()
    entry = {**value}
    if entry.get("image_bytes") and isinstance(entry["image_bytes"], (bytes, bytearray)):
        entry["image_bytes"] = base64.b64encode(entry["image_bytes"]).decode()
    data[str(user_id)] = entry
    _save_pending(data)

def pending_get(user_id: int) -> dict | None:
    data = _load_pending()
    entry = data.get(str(user_id))
    if entry and entry.get("image_bytes") and isinstance(entry["image_bytes"], str):
        try:
            entry["image_bytes"] = base64.b64decode(entry["image_bytes"])
        except Exception:
            entry["image_bytes"] = None
    return entry

def pending_update_seo(user_id: int, seo_data: dict):
    data = _load_pending()
    if str(user_id) in data:
        data[str(user_id)]["seo_data"] = seo_data
        _save_pending(data)

def pending_delete(user_id: int):
    data = _load_pending()
    data.pop(str(user_id), None)
    _save_pending(data)

def pending_exists(user_id: int) -> bool:
    data = _load_pending()
    return str(user_id) in data

# ============================================================
# HELPER
# ============================================================
def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

def get_wp_auth_header() -> dict:
    auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}

def build_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Đăng lên WordPress", callback_data=f"publish_{user_id}"),
            InlineKeyboardButton("❌ Huỷ bỏ", callback_data=f"cancel_{user_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Viết lại ngẫu nhiên", callback_data=f"rewrite_{user_id}"),
        ],
    ])

def build_preview_text(seo_data: dict, has_image: bool = False) -> str:
    from html.parser import HTMLParser
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
        def handle_data(self, data):
            self.text.append(data)

    extractor = TextExtractor()
    extractor.feed(seo_data.get("content_html", ""))
    plain_text = " ".join(extractor.text)[:600]

    return (
        f"📝 *PREVIEW BÀI VIẾT SEO*\n"
        f"{'━' * 30}\n\n"
        f"🏷️ *Tiêu đề SEO:*\n{seo_data['seo_title']}\n\n"
        f"📊 *Từ khóa chính:* `{seo_data.get('focus_keyword', 'N/A')}`\n\n"
        f"📋 *Meta Description:*\n_{seo_data['meta_description']}_\n\n"
        f"🏷️ *Tags:* {', '.join(seo_data.get('tags', []))}\n\n"
        f"📂 *Danh mục:* {seo_data.get('category_suggestion', 'N/A')}\n\n"
        f"🖼️ *Ảnh đại diện:* {'✅ Có' if has_image else '❌ Không có'}\n\n"
        f"{'━' * 30}\n"
        f"📖 *Nội dung (rút gọn):*\n{plain_text}...\n\n"
        f"{'━' * 30}\n"
        f"💬 *Muốn chỉnh sửa?* Nhắn yêu cầu trực tiếp, ví dụ:\n"
        f"_\"viết ngắn hơn\"_, _\"thêm emoji\"_, _\"tone vui hơn\"_...\n\n"
        f"👇 Hoặc bấm nút bên dưới:"
    )

# ============================================================
# SCRAPE FACEBOOK
# ============================================================
def scrape_facebook_post(url: str) -> dict:
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

        og_title = og_desc = og_image = ""
        og_t = soup.find("meta", property="og:title")
        og_d = soup.find("meta", property="og:description")
        og_i = soup.find("meta", property="og:image")
        if og_t: og_title = og_t.get("content", "")
        if og_d: og_desc  = og_d.get("content", "")
        if og_i: og_image = og_i.get("content", "")

        body_text = ""
        for tag in soup.find_all(["p", "div", "span"]):
            text = tag.get_text(separator=" ", strip=True)
            if len(text) > 100:
                body_text += text + "\n\n"
                if len(body_text) > 2000:
                    break

        return {
            "title":     og_title or "Bài viết từ Facebook",
            "content":   og_desc or body_text[:2000] or "Không lấy được nội dung.",
            "image_url": og_image,
            "source":    url,
        }
    except Exception as e:
        logger.error(f"Lỗi scrape: {e}")
        return {"title": "Bài viết từ Facebook", "content": "", "image_url": "", "source": url}

# ============================================================
# GROQ AI: Viết bài mới từ nội dung thô
# ============================================================
def rewrite_with_groq(raw_content: str) -> dict:
    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam, "
        "chuyên viết bài cho trung tâm tiếng Anh thiếu nhi NQH English. "
        "Phong cách: thân thiện, vui tươi, gần gũi với phụ huynh và học sinh. "
        "Chỉ trả về JSON thuần túy, không markdown, không backtick, không text thêm."
    )

    user_prompt = f"""Viết lại thành bài blog chuẩn SEO cho website NQH English.

NỘI DUNG GỐC:
{raw_content}

YÊU CẦU:
- Tiêu đề SEO hấp dẫn (60-70 ký tự), chứa từ khóa chính
- Meta description (150-160 ký tự)
- Viết hoàn toàn bằng tiếng Việt, KHÔNG copy y chang nội dung gốc
- Cấu trúc: Mở bài → 2-3 đoạn H2 → Kết bài
- Tối thiểu 500 từ
- Tone thân thiện, vui tươi, truyền cảm hứng học tiếng Anh
- Có thể dùng emoji phù hợp, không quá nhiều
- Tự nhiên nhắc "NQH English" 1-2 lần

Trả về JSON (không thêm gì khác):
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1", "tag2", "tag3"],
  "category_suggestion": "..."
}}

content_html dùng thẻ: <h2>, <h3>, <p>, <ul>, <li>, <strong>"""

    return _call_groq(system_prompt, user_prompt)

# ============================================================
# GROQ AI: Chỉnh sửa bài theo yêu cầu người dùng
# ============================================================
def refine_with_groq(current_html: str, user_request: str, current_seo: dict) -> dict:
    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam cho NQH English. "
        "Người dùng đang yêu cầu chỉnh sửa bài viết đã có. "
        "Hãy thực hiện ĐÚNG yêu cầu chỉnh sửa, giữ nguyên thông tin cốt lõi. "
        "Chỉ trả về JSON thuần túy, không markdown, không backtick."
    )

    user_prompt = f"""Đây là bài viết hiện tại:

TIÊU ĐỀ HIỆN TẠI: {current_seo.get('seo_title', '')}
NỘI DUNG HTML HIỆN TẠI:
{current_html}

YÊU CẦU CHỈNH SỬA CỦA NGƯỜI DÙNG:
"{user_request}"

Hãy chỉnh sửa bài viết theo đúng yêu cầu trên. Giữ lại thông tin cốt lõi, chỉ thay đổi những gì được yêu cầu.

Trả về JSON (không thêm gì khác):
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1", "tag2", "tag3"],
  "category_suggestion": "..."
}}"""

    return _call_groq(system_prompt, user_prompt)

# ============================================================
# GROQ: Gọi API chung
# ============================================================
def _call_groq(system_prompt: str, user_prompt: str) -> dict:
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=3000,
        )
        text = response.choices[0].message.content.strip()
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON lỗi: {e}")
        return {
            "seo_title": "Bài viết NQH English",
            "meta_description": "Nội dung từ NQH English.",
            "focus_keyword": "",
            "content_html": f"<p>{user_prompt[:200]}</p>",
            "tags": [],
            "category_suggestion": "Tin tức",
        }
    except Exception as e:
        logger.error(f"Lỗi Groq AI: {e}")
        raise

# ============================================================
# WORDPRESS: Upload ảnh bytes
# ============================================================
def upload_image_bytes_to_wordpress(image_bytes: bytes, filename: str = "image.jpg", content_type: str = "image/jpeg") -> int | None:
    try:
        headers = {
            **get_wp_auth_header(),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        }
        resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/media",
            headers=headers,
            data=image_bytes,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            media_id = resp.json().get("id")
            logger.info(f"Upload ảnh OK, ID: {media_id}")
            return media_id
        logger.error(f"Upload ảnh thất bại: {resp.status_code}")
    except Exception as e:
        logger.error(f"Lỗi upload ảnh: {e}")
    return None

def upload_image_url_to_wordpress(image_url: str) -> int | None:
    if not image_url:
        return None
    try:
        img_resp = requests.get(image_url, timeout=15)
        if img_resp.status_code != 200:
            return None
        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        ext = "jpg" if "jpeg" in content_type else content_type.split("/")[-1]
        return upload_image_bytes_to_wordpress(img_resp.content, f"fb-image.{ext}", content_type)
    except Exception as e:
        logger.error(f"Lỗi upload ảnh URL: {e}")
    return None

# ============================================================
# WORDPRESS: Đăng bài
# ============================================================
def post_to_wordpress(seo_data: dict, featured_media_id: int | None = None) -> dict:
    headers = {**get_wp_auth_header(), "Content-Type": "application/json"}
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
    resp = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return
    await update.message.reply_text(
        "👋 *Chào mừng đến với NQH English Bot!*\n\n"
        "📌 *Cách dùng:*\n"
        "1️⃣ Gửi *ảnh + caption* → viết bài + đặt ảnh làm thumbnail\n"
        "2️⃣ Gửi *link Facebook* → tự scrape + viết bài\n"
        "3️⃣ Gửi *text* trực tiếp → viết bài từ nội dung\n\n"
        "✏️ *Sau khi xem preview:*\n"
        "• Nhắn yêu cầu chỉnh để bot sửa lại\n"
        "• Ví dụ: _\"viết ngắn hơn\"_, _\"thêm emoji\"_...\n\n"
        "⚡ Powered by Groq AI\n"
        "📎 /help để xem thêm",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *Danh sách lệnh:*\n\n"
        "/start — Khởi động bot\n"
        "/help — Hướng dẫn\n"
        "/status — Kiểm tra kết nối WordPress\n"
        "/model — Xem model Groq đang dùng\n"
        "/cancel — Huỷ bài đang soạn\n\n"
        "💡 *Chỉnh sửa bài sau preview:*\n"
        "Chỉ cần nhắn yêu cầu bình thường:\n"
        "• _\"viết ngắn hơn 300 từ\"_\n"
        "• _\"thêm nhiều emoji hơn\"_\n"
        "• _\"tone chuyên nghiệp hơn\"_\n"
        "• _\"tập trung vào trẻ 6-10 tuổi\"_\n"
        "• _\"thêm đoạn về lợi ích học tiếng Anh sớm\"_\n"
        "• _\"đổi tiêu đề hấp dẫn hơn\"_",
        parse_mode="Markdown"
    )

async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"🤖 *Model Groq hiện tại:* `{GROQ_MODEL}`\n\n"
        f"Các model khả dụng:\n"
        f"• `llama-3.1-8b-instant` — Siêu nhanh\n"
        f"• `llama-3.3-70b-versatile` — Chất lượng cao ⭐\n"
        f"• `gemma2-9b-it` — Google Gemma 2\n"
        f"• `mixtral-8x7b-32768` — Context dài\n\n"
        f"Đổi qua biến môi trường `GROQ_MODEL` trên Railway.",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("🔍 Đang kiểm tra kết nối WordPress...")
    try:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/posts?per_page=1",
            headers=get_wp_auth_header(),
            timeout=10,
        )
        if resp.status_code == 200:
            await update.message.reply_text(
                f"✅ *Kết nối WordPress thành công!*\n"
                f"🌐 URL: `{WP_URL}`\n"
                f"🤖 Model: `{GROQ_MODEL}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ Lỗi kết nối: HTTP {resp.status_code}\nKiểm tra lại WP_URL và APP_PASSWORD")
    except Exception as e:
        await update.message.reply_text(f"❌ Không kết nối được: {e}")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if pending_exists(user_id):
        pending_delete(user_id)
        await update.message.reply_text("❌ Đã huỷ bài đang soạn. Gửi nội dung mới để bắt đầu lại.")
    else:
        await update.message.reply_text("ℹ️ Không có bài nào đang soạn.")

# ============================================================
# XỬ LÝ ẢNH
# ============================================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Không có quyền truy cập.")
        return

    user_id = update.effective_user.id
    caption = update.message.caption or ""
    msg = await update.message.reply_text("🖼️ Đang tải ảnh...")

    try:
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(photo_bytes_io)
        image_bytes = photo_bytes_io.getvalue()

        raw_text = caption.strip() if len(caption.strip()) >= 20 else "Hoạt động mới tại NQH English"

        await msg.edit_text(f"⚡ Groq AI đang viết bài từ ảnh...")
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":    seo_data,
            "image_bytes": image_bytes,
            "image_url":   "",
            "source":      "Ảnh Telegram",
        })

        preview = build_preview_text(seo_data, has_image=True)
        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))

    except Exception as e:
        logger.error(f"handle_photo error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown")

# ============================================================
# XỬ LÝ TEXT (link Facebook, text thuần, hoặc yêu cầu chỉnh sửa)
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Không có quyền truy cập.")
        return

    user_input = update.message.text.strip()
    user_id    = update.effective_user.id

    # ── TRƯỜNG HỢP 1: Đang có bài pending → coi đây là yêu cầu chỉnh sửa ──
    if pending_exists(user_id):
        post_info = pending_get(user_id)

        # Phát hiện nếu user gửi link/text mới (muốn làm bài mới, không phải chỉnh)
        is_fb_link = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/", user_input))
        is_new_content = is_fb_link or len(user_input) > 200

        if not is_new_content:
            # → Chỉnh sửa bài hiện tại theo yêu cầu
            msg = await update.message.reply_text(
                f"✏️ Đang chỉnh sửa theo yêu cầu:\n_\"{user_input}\"_\n\nVui lòng chờ...",
                parse_mode="Markdown"
            )
            try:
                current_seo  = post_info["seo_data"]
                current_html = current_seo.get("content_html", "")
                new_seo_data = refine_with_groq(current_html, user_input, current_seo)
                pending_update_seo(user_id, new_seo_data)

                has_image = bool(post_info.get("image_bytes") or post_info.get("image_url"))
                preview   = build_preview_text(new_seo_data, has_image=has_image)
                await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
            except Exception as e:
                logger.error(f"refine error: {e}", exc_info=True)
                await msg.edit_text(f"❌ Lỗi chỉnh sửa: `{str(e)[:200]}`", parse_mode="Markdown")
            return

        # → Nội dung mới → xoá pending cũ, tiếp tục xử lý bình thường
        pending_delete(user_id)

    # ── TRƯỜNG HỢP 2: Không có pending → tạo bài mới ──
    is_fb_link = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/", user_input))
    msg = await update.message.reply_text(
        "🔍 Đang scrape bài Facebook..." if is_fb_link else "⏳ Đang xử lý nội dung..."
    )

    try:
        if is_fb_link:
            scraped   = scrape_facebook_post(user_input)
            raw_text  = f"{scraped['title']}\n\n{scraped['content']}"
            image_url = scraped.get("image_url", "")
        else:
            raw_text  = user_input
            image_url = ""

        if len(raw_text.strip()) < 50:
            await msg.edit_text(
                "⚠️ Không lấy được nội dung đủ dài.\n\n"
                "📌 *Gợi ý:* Facebook hạn chế scrape, hãy thử:\n"
                "• Copy text bài viết và paste trực tiếp\n"
                "• Hoặc gửi ảnh kèm caption",
                parse_mode="Markdown"
            )
            return

        await msg.edit_text(f"⚡ Groq AI ({GROQ_MODEL}) đang viết bài...")
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":    seo_data,
            "image_bytes": None,
            "image_url":   image_url,
            "source":      user_input if is_fb_link else "Text trực tiếp",
        })

        has_image = bool(image_url)
        preview   = build_preview_text(seo_data, has_image=has_image)
        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))

    except Exception as e:
        logger.error(f"handle_message error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`\n\nVui lòng thử lại.", parse_mode="Markdown")

# ============================================================
# XỬ LÝ NÚT BẤM
# ============================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    data = query.data

    if data.startswith("cancel_"):
        pending_delete(user_id)
        await query.edit_message_text("❌ Đã huỷ. Gửi nội dung mới để bắt đầu lại.")

    elif data.startswith("rewrite_"):
        post_info = pending_get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Vui lòng gửi lại.")
            return

        await query.edit_message_text("🔄 Groq AI đang viết lại với phong cách khác...")
        try:
            raw      = post_info["seo_data"]["content_html"]
            seo_data = rewrite_with_groq(f"[Viết lại theo phong cách khác, sáng tạo hơn]\n{raw}")
            pending_update_seo(user_id, seo_data)
            has_image = bool(post_info.get("image_bytes") or post_info.get("image_url"))
            preview   = build_preview_text(seo_data, has_image=has_image)
            await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
        except Exception as e:
            await query.edit_message_text(f"❌ Lỗi viết lại: {e}")

    elif data.startswith("publish_"):
        post_info = pending_get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Vui lòng gửi lại.")
            return

        await query.edit_message_text("📤 Đang đăng bài lên WordPress...")
        try:
            media_id = None
            if post_info.get("image_bytes"):
                await query.edit_message_text("🖼️ Đang upload ảnh lên WordPress...")
                media_id = upload_image_bytes_to_wordpress(
                    post_info["image_bytes"], "telegram-image.jpg", "image/jpeg"
                )
            elif post_info.get("image_url"):
                await query.edit_message_text("🖼️ Đang upload ảnh từ Facebook...")
                media_id = upload_image_url_to_wordpress(post_info["image_url"])

            await query.edit_message_text("📝 Đang đăng bài...")
            result   = post_to_wordpress(post_info["seo_data"], media_id)
            post_url = result.get("link", "")
            post_id  = result.get("id", "")
            edit_url = f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"

            pending_delete(user_id)

            await query.edit_message_text(
                f"🎉 *Đăng bài thành công!*\n\n"
                f"📌 *Tiêu đề:* {post_info['seo_data']['seo_title']}\n"
                f"🖼️ *Ảnh:* {'✅ Đã upload' if media_id else '❌ Không có'}\n"
                f"🔗 *Xem bài:* [Nhấn vào đây]({post_url})\n"
                f"✏️ *Chỉnh sửa:* [WP Admin]({edit_url})\n\n"
                f"⚡ *Powered by Groq AI* (`{GROQ_MODEL}`)",
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
        except Exception as e:
            logger.error(f"Publish error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Lỗi đăng bài: `{str(e)[:300]}`\n\nKiểm tra lại cấu hình WordPress.",
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
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info(f"🤖 NQH English Bot v4.0 | Groq: {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
