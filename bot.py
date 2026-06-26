"""
Telegram Bot: Facebook → Groq AI SEO → WordPress
Phiên bản: 5.0
- Gửi ảnh + caption  → viết bài + thumbnail + ảnh giữa bài
- Gửi link Facebook  → scrape + viết bài chuẩn SEO 2026
- Gửi text thuần    → viết bài từ nội dung
- Nhắn yêu cầu chỉnh sau preview → bot viết lại theo yêu cầu
- Dữ liệu bền vững qua restart (lưu file JSON)
- Hệ thống dạy AI: /teach, /rules, /delrule
- Ảnh được chèn tự động giữa các đoạn trong bài
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
# FILE PATHS
# ============================================================
PENDING_FILE = "/tmp/pending_posts.json"
RULES_FILE   = "/tmp/ai_rules.json"

# ============================================================
# LƯU TRỮ BỀN VỮNG - PENDING POSTS
# ============================================================
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
    # Encode danh sách ảnh bytes (nếu có)
    if entry.get("image_bytes") and isinstance(entry["image_bytes"], (bytes, bytearray)):
        entry["image_bytes"] = base64.b64encode(entry["image_bytes"]).decode()
    if entry.get("extra_images_bytes"):
        encoded = []
        for img in entry["extra_images_bytes"]:
            if isinstance(img, (bytes, bytearray)):
                encoded.append(base64.b64encode(img).decode())
            else:
                encoded.append(img)
        entry["extra_images_bytes"] = encoded
    data[str(user_id)] = entry
    _save_pending(data)

def pending_get(user_id: int) -> dict | None:
    data = _load_pending()
    entry = data.get(str(user_id))
    if not entry:
        return None
    # Decode ảnh chính
    if entry.get("image_bytes") and isinstance(entry["image_bytes"], str):
        try:
            entry["image_bytes"] = base64.b64decode(entry["image_bytes"])
        except Exception:
            entry["image_bytes"] = None
    # Decode ảnh phụ
    if entry.get("extra_images_bytes"):
        decoded = []
        for img in entry["extra_images_bytes"]:
            if isinstance(img, str):
                try:
                    decoded.append(base64.b64decode(img))
                except Exception:
                    decoded.append(None)
            else:
                decoded.append(img)
        entry["extra_images_bytes"] = decoded
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
# LƯU TRỮ BỀN VỮNG - QUY TẮC AI (RULES)
# ============================================================
def rules_load() -> list:
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def rules_save(rules: list):
    try:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi lưu rules: {e}")

def rules_add(rule: str):
    rules = rules_load()
    rules.append(rule.strip())
    rules_save(rules)

def rules_delete(index: int) -> bool:
    rules = rules_load()
    if 0 <= index < len(rules):
        rules.pop(index)
        rules_save(rules)
        return True
    return False

def rules_get_as_text() -> str:
    rules = rules_load()
    if not rules:
        return ""
    lines = "\n".join(f"- {r}" for r in rules)
    return f"\nQUY TẮC BỔ SUNG DO NGƯỜI DÙNG DẠY:\n{lines}"

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

def build_preview_text(seo_data: dict, has_image: bool = False, extra_images: int = 0) -> str:
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

    img_info = ""
    if has_image and extra_images > 0:
        img_info = f"✅ Thumbnail + {extra_images} ảnh trong bài"
    elif has_image:
        img_info = "✅ Có thumbnail"
    else:
        img_info = "❌ Không có ảnh"

    word_count = len(" ".join(extractor.text).split())

    return (
        f"📝 *PREVIEW BÀI VIẾT SEO 2026*\n"
        f"{'━' * 30}\n\n"
        f"🏷️ *Tiêu đề SEO:*\n{seo_data['seo_title']}\n\n"
        f"📊 *Từ khóa chính:* `{seo_data.get('focus_keyword', 'N/A')}`\n\n"
        f"📋 *Meta Description:*\n_{seo_data['meta_description']}_\n\n"
        f"🏷️ *Tags:* {', '.join(seo_data.get('tags', []))}\n\n"
        f"📂 *Danh mục:* {seo_data.get('category_suggestion', 'N/A')}\n\n"
        f"🖼️ *Ảnh:* {img_info}\n\n"
        f"📏 *Độ dài:* ~{word_count} từ\n\n"
        f"{'━' * 30}\n"
        f"📖 *Nội dung (rút gọn):*\n{plain_text}...\n\n"
        f"{'━' * 30}\n"
        f"💬 *Muốn chỉnh sửa?* Nhắn yêu cầu trực tiếp:\n"
        f"_\"viết dài hơn\"_, _\"thêm emoji\"_, _\"tone vui hơn\"_...\n\n"
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
# GROQ AI: Viết bài chuẩn SEO 2026
# ============================================================
def rewrite_with_groq(raw_content: str) -> dict:
    custom_rules = rules_get_as_text()

    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam hàng đầu, "
        "chuyên viết bài cho trung tâm tiếng Anh thiếu nhi NQH English. "
        "Phong cách: thân thiện, vui tươi, gần gũi với phụ huynh và học sinh. "
        "Bạn nắm vững chuẩn SEO 2026: E-E-A-T, AI Search Optimization, Search Intent. "
        "Chỉ trả về JSON thuần túy, không markdown, không backtick, không text thêm."
    )

    user_prompt = f"""Viết lại thành bài blog chuẩn SEO 2026 cho website NQH English.

NỘI DUNG GỐC:
{raw_content}

TIÊU CHUẨN SEO 2026 BẮT BUỘC:
1. TIÊU ĐỀ (60-70 ký tự): Chứa từ khóa chính, hấp dẫn, kích thích click
2. META DESCRIPTION (150-160 ký tự): Tóm tắt giá trị bài viết, có từ khóa
3. CẤU TRÚC BÀI VIẾT:
   - Mở bài: 2-3 câu hook mạnh, đặt vấn đề, có từ khóa chính tự nhiên
   - Thân bài: 3-4 mục H2, mỗi mục có 2-3 đoạn <p>, dùng <ul>/<li> khi liệt kê
   - Kết bài: Tóm lại giá trị, khuyến khích tương tác
4. ĐỘ DÀI: Tối thiểu 700 từ, tối đa 1200 từ
5. E-E-A-T: Thể hiện kinh nghiệm thực tế, trích dẫn lợi ích cụ thể
6. TỪ KHÓA: Xuất hiện tự nhiên ở H2, đoạn đầu, đoạn cuối — KHÔNG nhồi nhét
7. ĐẶT [IMAGE_PLACEHOLDER] giữa bài: Sau mỗi 2 mục H2 hãy chèn dòng [IMAGE_PLACEHOLDER] — đây là vị trí để chèn ảnh vào giữa bài
8. ĐỊNH DẠNG: Câu ngắn, đoạn 3-4 câu, dễ đọc trên mobile
9. Tone thân thiện, vui tươi, truyền cảm hứng học tiếng Anh
10. Tự nhiên nhắc "NQH English" 1-2 lần trong bài{custom_rules}

ĐỊNH DẠNG HTML (chỉ dùng các thẻ này):
<h2>, <h3>, <p>, <ul>, <li>, <strong>, <em>
Và chuỗi [IMAGE_PLACEHOLDER] ở giữa bài (2-3 lần tùy độ dài)

Trả về JSON (không thêm gì khác):
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "category_suggestion": "..."
}}"""

    return _call_groq(system_prompt, user_prompt)

# ============================================================
# GROQ AI: Chỉnh sửa bài theo yêu cầu người dùng
# ============================================================
def refine_with_groq(current_html: str, user_request: str, current_seo: dict) -> dict:
    custom_rules = rules_get_as_text()

    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam cho NQH English. "
        "Người dùng đang yêu cầu chỉnh sửa bài viết đã có. "
        "Hãy thực hiện ĐÚNG yêu cầu chỉnh sửa, giữ nguyên thông tin cốt lõi. "
        "Giữ lại các [IMAGE_PLACEHOLDER] ở vị trí hợp lý trong bài. "
        "Chỉ trả về JSON thuần túy, không markdown, không backtick."
    )

    user_prompt = f"""Đây là bài viết hiện tại:

TIÊU ĐỀ HIỆN TẠI: {current_seo.get('seo_title', '')}
NỘI DUNG HTML HIỆN TẠI:
{current_html}

YÊU CẦU CHỈNH SỬA CỦA NGƯỜI DÙNG:
"{user_request}"

Hãy chỉnh sửa bài viết theo đúng yêu cầu trên. Giữ lại thông tin cốt lõi.
Đảm bảo có 2-3 [IMAGE_PLACEHOLDER] ở giữa bài để chèn ảnh.{custom_rules}

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
            max_tokens=4000,
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
            media = resp.json()
            logger.info(f"Upload ảnh OK, ID: {media.get('id')}, URL: {media.get('source_url')}")
            return media.get("id"), media.get("source_url", "")
        logger.error(f"Upload ảnh thất bại: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Lỗi upload ảnh: {e}")
    return None, None

def upload_image_url_to_wordpress(image_url: str) -> tuple:
    if not image_url:
        return None, None
    try:
        img_resp = requests.get(image_url, timeout=15)
        if img_resp.status_code != 200:
            return None, None
        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        ext = "jpg" if "jpeg" in content_type else content_type.split("/")[-1]
        return upload_image_bytes_to_wordpress(img_resp.content, f"fb-image.{ext}", content_type)
    except Exception as e:
        logger.error(f"Lỗi upload ảnh URL: {e}")
    return None, None

# ============================================================
# WORDPRESS: Chèn ảnh vào giữa bài (thay [IMAGE_PLACEHOLDER])
# ============================================================
def inject_images_into_content(content_html: str, image_urls: list) -> str:
    """Thay [IMAGE_PLACEHOLDER] bằng thẻ <img> thực tế."""
    if not image_urls:
        # Xóa placeholder nếu không có ảnh
        content_html = content_html.replace("[IMAGE_PLACEHOLDER]", "")
        return content_html

    idx = 0
    for img_url in image_urls:
        if "[IMAGE_PLACEHOLDER]" not in content_html:
            break
        img_tag = (
            f'<figure class="wp-block-image size-large" style="text-align:center;margin:30px 0;">'
            f'<img src="{img_url}" alt="NQH English" style="max-width:100%;border-radius:8px;" />'
            f'</figure>'
        )
        content_html = content_html.replace("[IMAGE_PLACEHOLDER]", img_tag, 1)
        idx += 1

    # Xóa placeholder còn thừa
    content_html = content_html.replace("[IMAGE_PLACEHOLDER]", "")
    return content_html

# ============================================================
# WORDPRESS: Đăng bài
# ============================================================
def post_to_wordpress(seo_data: dict, featured_media_id: int | None = None, extra_image_urls: list = None) -> dict:
    headers = {**get_wp_auth_header(), "Content-Type": "application/json"}

    # Chèn ảnh vào giữa bài nếu có
    content_html = seo_data["content_html"]
    if extra_image_urls:
        content_html = inject_images_into_content(content_html, extra_image_urls)
    else:
        content_html = content_html.replace("[IMAGE_PLACEHOLDER]", "")

    payload = {
        "title":   seo_data["seo_title"],
        "content": content_html,
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
# TELEGRAM HANDLERS - COMMANDS
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Bạn không có quyền dùng bot này.")
        return
    rules = rules_load()
    rules_count = f"\n🧠 *AI đã được dạy {len(rules)} quy tắc tùy chỉnh*" if rules else ""
    await update.message.reply_text(
        "👋 *Chào mừng đến với NQH English Bot v5.0!*\n\n"
        "📌 *Cách tạo bài:*\n"
        "1️⃣ Gửi *ảnh + caption* → thumbnail + ảnh chèn giữa bài\n"
        "2️⃣ Gửi *link Facebook* → scrape + viết chuẩn SEO 2026\n"
        "3️⃣ Gửi *text* trực tiếp → viết bài từ nội dung\n\n"
        "✏️ *Chỉnh sửa sau preview:*\n"
        "Nhắn thẳng yêu cầu, ví dụ: _\"viết ngắn hơn\"_, _\"thêm emoji\"_\n\n"
        "🧠 *Dạy AI thông minh hơn:*\n"
        "/teach — dạy AI quy tắc mới\n"
        "/rules — xem quy tắc đã dạy\n"
        "/delrule — xoá quy tắc\n\n"
        f"⚡ Powered by Groq AI ({GROQ_MODEL}){rules_count}\n"
        "📎 /help để xem chi tiết",
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
        "🧠 *Dạy AI thông minh hơn:*\n"
        "/teach [quy tắc] — Dạy AI một quy tắc mới\n"
        "Ví dụ: `/teach Luôn đề cập lớp học thứ 7, CN`\n"
        "Ví dụ: `/teach Không dùng từ 'tuyệt vời', thay bằng từ khác`\n"
        "Ví dụ: `/teach Bài viết luôn hướng đến phụ huynh có con 5-12 tuổi`\n\n"
        "/rules — Xem tất cả quy tắc đã dạy\n"
        "/delrule [số] — Xoá quy tắc theo số thứ tự\n\n"
        "💡 *Chỉnh sửa bài sau preview:*\n"
        "Nhắn trực tiếp:\n"
        "• _\"viết dài hơn 800 từ\"_\n"
        "• _\"thêm nhiều emoji hơn\"_\n"
        "• _\"tone chuyên nghiệp hơn\"_\n"
        "• _\"tập trung vào trẻ 6-10 tuổi\"_\n"
        "• _\"đổi tiêu đề hấp dẫn hơn\"_\n\n"
        "🖼️ *Ảnh trong bài:*\n"
        "Gửi nhiều ảnh cùng lúc → bot tự chèn vào giữa bài",
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
        rules = rules_load()
        if resp.status_code == 200:
            await update.message.reply_text(
                f"✅ *Kết nối WordPress thành công!*\n"
                f"🌐 URL: `{WP_URL}`\n"
                f"🤖 Model: `{GROQ_MODEL}`\n"
                f"🧠 AI đã được dạy: `{len(rules)} quy tắc`",
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
# COMMANDS - DẠY AI
# ============================================================
async def cmd_teach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rule = " ".join(context.args).strip() if context.args else ""
    if not rule:
        await update.message.reply_text(
            "📝 *Cách dùng:* `/teach [quy tắc]`\n\n"
            "Ví dụ:\n"
            "`/teach Luôn đề cập đến lớp học thứ 7 và Chủ nhật`\n"
            "`/teach Bài viết hướng đến phụ huynh có con 5-12 tuổi`\n"
            "`/teach Không dùng từ 'tuyệt vời', dùng từ khác thay thế`\n"
            "`/teach Mỗi bài phải có ít nhất 1 câu hỏi tương tác cuối bài`",
            parse_mode="Markdown"
        )
        return
    rules_add(rule)
    rules = rules_load()
    await update.message.reply_text(
        f"✅ *Đã dạy AI quy tắc mới!*\n\n"
        f"📌 Quy tắc #{len(rules)}: _{rule}_\n\n"
        f"🧠 Tổng cộng: *{len(rules)} quy tắc* đã được lưu.\n"
        f"AI sẽ áp dụng tất cả quy tắc này vào mọi bài viết từ bây giờ.",
        parse_mode="Markdown"
    )

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    rules = rules_load()
    if not rules:
        await update.message.reply_text(
            "🧠 *Chưa có quy tắc nào được dạy.*\n\n"
            "Dùng `/teach [quy tắc]` để dạy AI viết hay hơn!",
            parse_mode="Markdown"
        )
        return
    lines = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))
    await update.message.reply_text(
        f"🧠 *Các quy tắc AI đã được dạy ({len(rules)} quy tắc):*\n\n"
        f"{lines}\n\n"
        f"Dùng `/delrule [số]` để xoá quy tắc.\n"
        f"Dùng `/teach [quy tắc]` để thêm quy tắc mới.",
        parse_mode="Markdown"
    )

async def cmd_delrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Dùng: `/delrule [số thứ tự]`\nXem danh sách: /rules", parse_mode="Markdown")
        return
    try:
        idx = int(context.args[0]) - 1
        rules = rules_load()
        if 0 <= idx < len(rules):
            deleted = rules[idx]
            rules_delete(idx)
            await update.message.reply_text(
                f"🗑️ Đã xoá quy tắc #{idx+1}:\n_{deleted}_\n\n"
                f"Còn lại {len(rules)-1} quy tắc.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ Không tìm thấy quy tắc #{idx+1}. Dùng /rules để xem danh sách.")
    except ValueError:
        await update.message.reply_text("❌ Vui lòng nhập số thứ tự. Ví dụ: `/delrule 2`", parse_mode="Markdown")

# ============================================================
# JOB: XỬ LÝ ALBUM SAU KHI ĐÃ GOM ĐỦ ẢNH
# ============================================================
async def process_album_job(context):
    """Chạy sau 3 giây kể từ ảnh cuối cùng — đảm bảo đã gom đủ toàn bộ album."""
    job      = context.job
    data     = job.data
    album_key = data["album_key"]
    bot_data  = data["bot_data"]
    user_id   = job.user_id
    chat_id   = job.chat_id

    buf = bot_data.get("album_buffer", {})
    if album_key not in buf:
        return

    album     = buf.pop(album_key)
    all_images   = album["images"]
    caption_text = album.get("caption", "")

    if not all_images:
        return

    main_image   = all_images[0]
    extra_images = all_images[1:]

    logger.info(f"Album {album_key}: {len(all_images)} ảnh, {len(extra_images)} ảnh phụ")

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"🖼️ Đã nhận {len(all_images)} ảnh — Groq AI đang viết bài..."
    )

    try:
        raw_text = caption_text.strip() if len(caption_text.strip()) >= 20 else "Hoạt động mới tại NQH English"
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":           seo_data,
            "image_bytes":        main_image,
            "extra_images_bytes": extra_images,
            "image_url":          "",
            "source":             "Ảnh Telegram",
        })

        preview = build_preview_text(seo_data, has_image=True, extra_images=len(extra_images))
        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))

    except Exception as e:
        logger.error(f"process_album_job error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Lỗi xử lý album: `{str(e)[:200]}`", parse_mode="Markdown")

# ============================================================
# XỬ LÝ ẢNH (hỗ trợ nhiều ảnh trong album)
# ============================================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Không có quyền truy cập.")
        return

    user_id = update.effective_user.id
    caption = update.message.caption or ""
    media_group_id = update.message.media_group_id

    msg = await update.message.reply_text("🖼️ Đang tải ảnh...")

    try:
        # Tải ảnh hiện tại
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(photo_bytes_io)
        image_bytes = photo_bytes_io.getvalue()

        # Kiểm tra nếu đây là album (media group) — gom ảnh rồi xử lý 1 lần
        if media_group_id:
            album_key = f"album_{user_id}_{media_group_id}"
            if "album_buffer" not in context.bot_data:
                context.bot_data["album_buffer"] = {}
            buf = context.bot_data["album_buffer"]

            # Khởi tạo buffer cho album này
            if album_key not in buf:
                buf[album_key] = {
                    "images":   [],
                    "caption":  caption,
                    "user_id":  user_id,
                    "msg_id":   msg.message_id,
                }
                # Xoá tin nhắn "Đang tải ảnh..." ngay (sẽ gửi lại sau khi gom đủ)
                await msg.delete()
            else:
                # Ảnh tiếp theo trong album — xoá tin nhắn thừa
                try:
                    await msg.delete()
                except Exception:
                    pass

            # Thêm ảnh vào buffer
            buf[album_key]["images"].append(image_bytes)
            if caption:
                buf[album_key]["caption"] = caption

            # Huỷ job cũ nếu có, đặt job mới chờ 3 giây
            job_name = f"process_album_{album_key}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()

            context.job_queue.run_once(
                process_album_job,
                when=3,
                name=job_name,
                data={"album_key": album_key, "bot_data": context.bot_data},
                chat_id=update.effective_chat.id,
                user_id=user_id,
            )
            return

        # Ảnh đơn
        raw_text = caption.strip() if len(caption.strip()) >= 20 else "Hoạt động mới tại NQH English"
        await msg.edit_text(f"⚡ Groq AI đang viết bài từ ảnh...")
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":          seo_data,
            "image_bytes":       image_bytes,
            "extra_images_bytes": [],
            "image_url":         "",
            "source":            "Ảnh Telegram",
        })

        preview = build_preview_text(seo_data, has_image=True, extra_images=0)
        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))

    except Exception as e:
        logger.error(f"handle_photo error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown")

# ============================================================
# XỬ LÝ TEXT
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Không có quyền truy cập.")
        return

    user_input = update.message.text.strip()
    user_id    = update.effective_user.id

    # TRƯỜNG HỢP 1: Đang có bài pending → yêu cầu chỉnh sửa
    if pending_exists(user_id):
        post_info = pending_get(user_id)
        is_fb_link = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/", user_input))
        is_new_content = is_fb_link or len(user_input) > 200

        if not is_new_content:
            msg = await update.message.reply_text(
                f"✏️ Đang chỉnh sửa theo yêu cầu:\n_\"{user_input}\"_\n\nVui lòng chờ...",
                parse_mode="Markdown"
            )
            try:
                current_seo  = post_info["seo_data"]
                current_html = current_seo.get("content_html", "")
                new_seo_data = refine_with_groq(current_html, user_input, current_seo)
                pending_update_seo(user_id, new_seo_data)

                has_image    = bool(post_info.get("image_bytes") or post_info.get("image_url"))
                extra_count  = len(post_info.get("extra_images_bytes") or [])
                preview      = build_preview_text(new_seo_data, has_image=has_image, extra_images=extra_count)
                await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
            except Exception as e:
                logger.error(f"refine error: {e}", exc_info=True)
                await msg.edit_text(f"❌ Lỗi chỉnh sửa: `{str(e)[:200]}`", parse_mode="Markdown")
            return

        pending_delete(user_id)

    # TRƯỜNG HỢP 2: Tạo bài mới
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
                "📌 *Gợi ý:*\n"
                "• Copy text bài viết và paste trực tiếp\n"
                "• Hoặc gửi ảnh kèm caption",
                parse_mode="Markdown"
            )
            return

        await msg.edit_text(f"⚡ Groq AI ({GROQ_MODEL}) đang viết bài chuẩn SEO 2026...")
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":          seo_data,
            "image_bytes":       None,
            "extra_images_bytes": [],
            "image_url":         image_url,
            "source":            user_input if is_fb_link else "Text trực tiếp",
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
            has_image   = bool(post_info.get("image_bytes") or post_info.get("image_url"))
            extra_count = len(post_info.get("extra_images_bytes") or [])
            preview     = build_preview_text(seo_data, has_image=has_image, extra_images=extra_count)
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
            featured_media_id = None
            extra_image_urls  = []

            # Upload ảnh thumbnail chính
            if post_info.get("image_bytes"):
                await query.edit_message_text("🖼️ Đang upload thumbnail lên WordPress...")
                media_id, media_url = upload_image_bytes_to_wordpress(
                    post_info["image_bytes"], "telegram-image.jpg", "image/jpeg"
                )
                featured_media_id = media_id

                # Upload ảnh phụ (chèn giữa bài)
                extra_imgs = post_info.get("extra_images_bytes") or []
                if extra_imgs:
                    await query.edit_message_text(f"🖼️ Đang upload {len(extra_imgs)} ảnh cho bài...")
                    for i, img_bytes in enumerate(extra_imgs):
                        if img_bytes:
                            _, img_url = upload_image_bytes_to_wordpress(
                                img_bytes, f"inline-image-{i+1}.jpg", "image/jpeg"
                            )
                            if img_url:
                                extra_image_urls.append(img_url)

            elif post_info.get("image_url"):
                await query.edit_message_text("🖼️ Đang upload ảnh từ Facebook...")
                featured_media_id, _ = upload_image_url_to_wordpress(post_info["image_url"])

            await query.edit_message_text("📝 Đang đăng bài chuẩn SEO 2026...")
            result   = post_to_wordpress(post_info["seo_data"], featured_media_id, extra_image_urls)
            post_url = result.get("link", "")
            post_id  = result.get("id", "")
            edit_url = f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"

            pending_delete(user_id)

            img_info = "❌ Không có"
            if featured_media_id and extra_image_urls:
                img_info = f"✅ Thumbnail + {len(extra_image_urls)} ảnh trong bài"
            elif featured_media_id:
                img_info = "✅ Thumbnail"

            await query.edit_message_text(
                f"🎉 *Đăng bài thành công!*\n\n"
                f"📌 *Tiêu đề:* {post_info['seo_data']['seo_title']}\n"
                f"🖼️ *Ảnh:* {img_info}\n"
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

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("model",   cmd_model))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(CommandHandler("teach",   cmd_teach))
    app.add_handler(CommandHandler("rules",   cmd_rules))
    app.add_handler(CommandHandler("delrule", cmd_delrule))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info(f"🤖 NQH English Bot v5.0 | Groq: {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
