"""
Telegram Bot: Facebook → Groq AI SEO → WordPress
Phiên bản: 6.0
- Upload nhiều ảnh: ảnh 1 = thumbnail, ảnh 2+ chèn vào trong bài
- Bố cục bài: H2 xen kẽ ảnh minh họa
- SEO 2026 đầy đủ: Technical SEO, E-E-A-T, Search Intent, On-page
- Dạy bot quy tắc riêng, nhớ vĩnh viễn
- Dữ liệu bền vững qua restart
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

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
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
# FILE LƯU TRỮ
# ============================================================
PENDING_FILE      = "/tmp/pending_posts.json"
RULES_FILE        = "/tmp/bot_rules.json"
MEDIA_GROUP_FILE  = "/tmp/media_groups.json"  # gom nhóm ảnh album

# ============================================================
# CHUẨN SEO 2026 - TÍCH HỢP SẴN
# ============================================================
SEO_2026_BASE = """
CHUẨN SEO 2026 BẮT BUỘC ÁP DỤNG:

1. E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness):
   - Thể hiện trải nghiệm thực tế, đưa ví dụ và số liệu cụ thể
   - Nội dung đáng tin cậy, không phóng đại, thể hiện chuyên môn
   - Viết như một chuyên gia thực sự trong lĩnh vực

2. Search Intent - Đáp ứng đúng ý định tìm kiếm:
   - Xác định rõ người đọc muốn gì: thông tin, hướng dẫn, hay so sánh
   - Giải quyết đúng và đủ vấn đề người đọc quan tâm
   - Không viết lan man, mỗi đoạn phải có giá trị rõ ràng

3. Information Gain - Giá trị thực sự:
   - Không viết lại những gì bài khác đã có
   - Đưa góc nhìn mới, ví dụ thực tế, case study
   - Câu trả lời trực tiếp, cô đọng trước - giải thích sau

4. Tối ưu cho AI Search (Google AI Overview, ChatGPT, Perplexity):
   - Định nghĩa ngắn gọn ở phần mở đầu để AI dễ trích dẫn
   - Cấu trúc H2/H3 logic, mỗi heading là 1 câu hỏi hoặc chủ đề rõ ràng
   - Dùng danh sách (bullet/số) cho thông tin liệt kê

5. Kỹ thuật On-page:
   - H1 (tiêu đề): 55-65 ký tự, chứa từ khóa chính, hấp dẫn người click
   - Meta description: 150-160 ký tự, chứa từ khóa, thôi thúc click
   - Đoạn văn ngắn 3-4 dòng, dễ scan trên mobile
   - Internal link: gợi ý các chủ đề liên quan trong bài

6. Trải nghiệm người dùng (UX):
   - Viết cho con người đọc trước, máy tính sau
   - Nội dung tự nhiên, không nhồi từ khóa (Keyword Stuffing)
   - Tránh Thin Content - bài phải giải quyết được vấn đề người đọc
   - Đoạn mở đầu phải hook ngay từ câu đầu tiên

7. Technical SEO (áp dụng khi viết HTML):
   - Dùng thẻ HTML chuẩn: <h2>, <h3>, <p>, <ul>, <ol>, <li>, <strong>
   - Mỗi ảnh phải có alt text mô tả nội dung tự nhiên
   - Cấu trúc logic: Mở bài → Nội dung chính → Kết luận
"""

# ============================================================
# QUẢN LÝ RULES - Bộ nhớ học tập
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

def rules_add(rule: str) -> int:
    rules  = rules_load()
    new_id = max([r["id"] for r in rules], default=0) + 1
    rules.append({"id": new_id, "rule": rule})
    rules_save(rules)
    return new_id

def rules_delete(rule_id: int) -> bool:
    rules     = rules_load()
    new_rules = [r for r in rules if r["id"] != rule_id]
    if len(new_rules) == len(rules):
        return False
    rules_save(new_rules)
    return True

def rules_to_prompt() -> str:
    rules = rules_load()
    if not rules:
        return ""
    lines = "\n".join([f"- [{r['id']}] {r['rule']}" for r in rules])
    return f"\nQUY TẮC RIÊNG CỦA NQH ENGLISH (bắt buộc tuân theo):\n{lines}\n"

# ============================================================
# QUẢN LÝ MEDIA GROUPS (gom nhóm ảnh album Telegram)
# ============================================================
def mg_load() -> dict:
    try:
        with open(MEDIA_GROUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def mg_save(data: dict):
    try:
        with open(MEDIA_GROUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Lỗi lưu media_group: {e}")

def mg_add_photo(group_id: str, file_id: str, caption: str = ""):
    data = mg_load()
    if group_id not in data:
        data[group_id] = {"photos": [], "caption": caption}
    if caption and not data[group_id]["caption"]:
        data[group_id]["caption"] = caption
    if file_id not in data[group_id]["photos"]:
        data[group_id]["photos"].append(file_id)
    mg_save(data)

def mg_get(group_id: str) -> dict | None:
    return mg_load().get(group_id)

def mg_delete(group_id: str):
    data = mg_load()
    data.pop(group_id, None)
    mg_save(data)

# ============================================================
# PENDING POSTS - Bền vững qua restart
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
    data  = _load_pending()
    entry = {**value}
    # Encode danh sách ảnh bytes → base64
    if entry.get("images_bytes"):
        entry["images_bytes"] = [
            base64.b64encode(b).decode() if isinstance(b, (bytes, bytearray)) else b
            for b in entry["images_bytes"]
        ]
    data[str(user_id)] = entry
    _save_pending(data)

def pending_get(user_id: int) -> dict | None:
    data  = _load_pending()
    entry = data.get(str(user_id))
    if not entry:
        return None
    # Decode base64 → bytes
    if entry.get("images_bytes"):
        decoded = []
        for b in entry["images_bytes"]:
            try:
                decoded.append(base64.b64decode(b) if isinstance(b, str) else b)
            except Exception:
                decoded.append(None)
        entry["images_bytes"] = decoded
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
    return str(user_id) in _load_pending()

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
        [InlineKeyboardButton("🔄 Viết lại ngẫu nhiên", callback_data=f"rewrite_{user_id}")],
    ])

def build_preview_text(seo_data: dict, num_images: int = 0) -> str:
    from html.parser import HTMLParser
    class TX(HTMLParser):
        def __init__(self): super().__init__(); self.t = []
        def handle_data(self, d): self.t.append(d)
    tx = TX()
    tx.feed(seo_data.get("content_html", ""))
    plain = " ".join(tx.t)[:500]

    rules     = rules_load()
    img_note  = f"✅ {num_images} ảnh (1 thumbnail + {num_images-1} ảnh trong bài)" if num_images > 1 else ("✅ 1 ảnh thumbnail" if num_images == 1 else "❌ Không có ảnh")

    return (
        f"📝 *PREVIEW BÀI VIẾT SEO*\n"
        f"{'━'*30}\n\n"
        f"🏷️ *Tiêu đề:*\n{seo_data['seo_title']}\n\n"
        f"📊 *Từ khóa:* `{seo_data.get('focus_keyword','N/A')}`\n\n"
        f"📋 *Meta:* _{seo_data['meta_description']}_\n\n"
        f"🏷️ *Tags:* {', '.join(seo_data.get('tags',[]))}\n\n"
        f"📂 *Danh mục:* {seo_data.get('category_suggestion','N/A')}\n\n"
        f"🖼️ *Ảnh:* {img_note}\n\n"
        f"📚 *Áp dụng:* {len(rules)} quy tắc riêng + SEO 2026\n\n"
        f"{'━'*30}\n"
        f"📖 *Nội dung (rút gọn):*\n{plain}...\n\n"
        f"{'━'*30}\n"
        f"💬 *Muốn chỉnh?* Nhắn yêu cầu trực tiếp:\n"
        f"_\"viết ngắn hơn\"_, _\"thêm emoji\"_, _\"tone vui hơn\"_...\n\n"
        f"👇 Hoặc bấm nút:"
    )

# ============================================================
# SCRAPE FACEBOOK
# ============================================================
def scrape_facebook_post(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        og_title = og_desc = og_image = ""
        for prop, var in [("og:title", "og_title"), ("og:description", "og_desc"), ("og:image", "og_image")]:
            tag = soup.find("meta", property=prop)
            if tag: locals()[var]; exec(f'{var} = tag.get("content", "")')
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
                if len(body_text) > 2000: break
        return {
            "title":     og_title or "Bài viết từ Facebook",
            "content":   og_desc or body_text[:2000] or "Không lấy được nội dung.",
            "image_url": og_image, "source": url,
        }
    except Exception as e:
        logger.error(f"Lỗi scrape: {e}")
        return {"title": "Bài viết từ Facebook", "content": "", "image_url": "", "source": url}

# ============================================================
# WORDPRESS: Upload ảnh
# ============================================================
def upload_image_bytes_to_wp(image_bytes: bytes, filename: str = "image.jpg", content_type: str = "image/jpeg") -> int | None:
    try:
        headers = {
            **get_wp_auth_header(),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        }
        resp = requests.post(f"{WP_URL}/wp-json/wp/v2/media", headers=headers, data=image_bytes, timeout=30)
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info(f"Upload ảnh OK: {data.get('id')} - {data.get('source_url','')}")
            return data.get("id"), data.get("source_url", "")
        logger.error(f"Upload ảnh lỗi: {resp.status_code}")
    except Exception as e:
        logger.error(f"Lỗi upload: {e}")
    return None, None

def upload_image_url_to_wp(image_url: str) -> tuple:
    if not image_url:
        return None, None
    try:
        r = requests.get(image_url, timeout=15)
        if r.status_code != 200: return None, None
        ct  = r.headers.get("Content-Type", "image/jpeg")
        ext = "jpg" if "jpeg" in ct else ct.split("/")[-1]
        return upload_image_bytes_to_wp(r.content, f"fb-image.{ext}", ct)
    except Exception as e:
        logger.error(f"Lỗi upload URL: {e}")
    return None, None

# ============================================================
# GROQ AI: Viết bài với placeholder ảnh
# ============================================================
def rewrite_with_groq(raw_content: str, num_inline_images: int = 0) -> dict:
    custom_rules   = rules_to_prompt()
    image_instruct = ""
    if num_inline_images > 0:
        image_instruct = f"""
QUAN TRỌNG - CHÈN ẢNH VÀO BÀI:
Bài viết có {num_inline_images} ảnh minh họa sẽ được chèn vào trong bài.
Hãy đặt placeholder {{{{IMAGE_1}}}}, {{{{IMAGE_2}}}}, ... (tối đa {num_inline_images} placeholder)
tại các vị trí phù hợp trong content_html, sau mỗi đoạn H2 hoặc đoạn nội dung quan trọng.
Ví dụ: <h2>Tiêu đề phần 1</h2><p>Nội dung...</p>{{{{IMAGE_1}}}}<h2>Tiêu đề phần 2</h2>...
"""

    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer người Việt Nam, "
        "chuyên viết bài cho trung tâm tiếng Anh thiếu nhi NQH English. "
        "Phong cách: thân thiện, vui tươi, gần gũi. "
        "Chỉ trả về JSON thuần túy, không markdown, không backtick."
    )

    user_prompt = f"""Viết bài blog chuẩn SEO cho NQH English từ nội dung sau:

NỘI DUNG GỐC:
{raw_content}

{SEO_2026_BASE}
{custom_rules}
{image_instruct}

YÊU CẦU THÊM:
- Tối thiểu 600 từ
- Tone thân thiện, vui tươi, truyền cảm hứng học tiếng Anh
- Emoji phù hợp, không quá nhiều
- Đề cập "NQH English" 1-2 lần tự nhiên
- KHÔNG copy y chang nội dung gốc
- Mở đầu phải hook ngay từ câu đầu

Trả về JSON:
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1","tag2","tag3"],
  "category_suggestion": "..."
}}"""

    return _call_groq(system_prompt, user_prompt)

def refine_with_groq(current_html: str, user_request: str, current_seo: dict) -> dict:
    custom_rules  = rules_to_prompt()
    system_prompt = (
        "Bạn là chuyên gia SEO Content Writer cho NQH English. "
        "Thực hiện ĐÚNG yêu cầu chỉnh sửa, giữ nguyên placeholder ảnh {{IMAGE_N}}. "
        "Chỉ trả về JSON thuần túy."
    )
    user_prompt = f"""Bài hiện tại:
TIÊU ĐỀ: {current_seo.get('seo_title','')}
NỘI DUNG: {current_html}

YÊU CẦU CHỈNH: "{user_request}"

{SEO_2026_BASE}
{custom_rules}

Giữ nguyên {{{{IMAGE_N}}}} placeholder nếu có. Chỉ thay đổi những gì được yêu cầu.

Trả về JSON:
{{
  "seo_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content_html": "...",
  "tags": ["tag1","tag2","tag3"],
  "category_suggestion": "..."
}}"""
    return _call_groq(system_prompt, user_prompt)

def _call_groq(system_prompt: str, user_prompt: str) -> dict:
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.7, max_tokens=3000,
        )
        text = resp.choices[0].message.content.strip()
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*",     "", text)
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON lỗi: {e}")
        return {"seo_title": "Bài viết NQH English", "meta_description": "NQH English.", "focus_keyword": "",
                "content_html": "<p>Lỗi tạo nội dung.</p>", "tags": [], "category_suggestion": "Tin tức"}
    except Exception as e:
        logger.error(f"Groq lỗi: {e}"); raise

# ============================================================
# WORDPRESS: Đăng bài với ảnh chèn trong bài
# ============================================================
def post_to_wordpress(seo_data: dict, thumbnail_id: int | None, inline_image_urls: list) -> dict:
    """
    thumbnail_id: media ID ảnh đại diện
    inline_image_urls: list URL ảnh đã upload để thay thế {IMAGE_N}
    """
    headers = {**get_wp_auth_header(), "Content-Type": "application/json"}

    # Thay placeholder {IMAGE_N} bằng thẻ <img> thực
    content_html = seo_data["content_html"]
    for i, img_url in enumerate(inline_image_urls, start=1):
        if img_url:
            img_tag = (
                f'<figure class="wp-block-image size-large">'
                f'<img src="{img_url}" alt="{seo_data["seo_title"]} - hình {i}" '
                f'class="wp-image" loading="lazy"/>'
                f'</figure>'
            )
            content_html = content_html.replace(f"{{{{IMAGE_{i}}}}}", img_tag)
    # Xoá placeholder thừa chưa được thay
    content_html = re.sub(r"\{\{IMAGE_\d+\}\}", "", content_html)

    payload = {
        "title":   seo_data["seo_title"],
        "content": content_html,
        "status":  "publish",
        "excerpt": seo_data["meta_description"],
    }
    if thumbnail_id:
        payload["featured_media"] = thumbnail_id

    # Tags
    if seo_data.get("tags"):
        tag_ids = []
        for name in seo_data["tags"]:
            tr = requests.post(f"{WP_URL}/wp-json/wp/v2/tags", headers=headers, json={"name": name}, timeout=10)
            if tr.status_code in (200, 201):
                tag_ids.append(tr.json()["id"])
        if tag_ids:
            payload["tags"] = tag_ids

    resp = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

# ============================================================
# TELEGRAM HANDLERS - LỆNH
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    rules = rules_load()
    await update.message.reply_text(
        "👋 *NQH English Bot v6.0*\n\n"
        "📸 *Gửi ảnh:*\n"
        "• 1 ảnh + caption → thumbnail + viết bài\n"
        "• Nhiều ảnh (album) + caption → ảnh 1 làm thumbnail, ảnh còn lại chèn trong bài\n\n"
        "📝 *Gửi nội dung:*\n"
        "• Link Facebook → scrape + viết bài\n"
        "• Text → viết bài từ nội dung\n\n"
        "✏️ *Sau preview:* nhắn yêu cầu chỉnh trực tiếp\n\n"
        "🧠 *Dạy bot:*\n"
        "/teach [quy tắc] — Bot nhớ mãi\n"
        "/rules — Xem quy tắc\n"
        "/delrule [id] — Xoá quy tắc\n\n"
        f"📚 *{len(rules)} quy tắc riêng* + SEO 2026\n"
        "/help để xem đầy đủ",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *Lệnh đầy đủ:*\n\n"
        "*Bài viết:*\n"
        "/status — Kiểm tra WP\n"
        "/model — Model Groq\n"
        "/cancel — Huỷ bài đang soạn\n\n"
        "*Dạy bot:*\n"
        "/teach [nội dung] — Dạy quy tắc mới\n"
        "/rules — Xem tất cả quy tắc\n"
        "/delrule [id] — Xoá quy tắc\n\n"
        "*Ví dụ dạy bot:*\n"
        "`/teach mở đầu bằng câu hỏi kích thích tò mò`\n"
        "`/teach không dùng từ \"tuy nhiên\" hay \"bên cạnh đó\"`\n"
        "`/teach mỗi bài có ít nhất 1 ví dụ thực tế`\n"
        "`/teach câu ngắn, tối đa 20 từ mỗi câu`\n\n"
        "*Gửi nhiều ảnh:*\n"
        "Chọn nhiều ảnh → gửi dưới dạng album (Media Group)\n"
        "Viết caption cho ảnh đầu tiên\n"
        "Ảnh 1 → thumbnail, ảnh 2,3... → chèn trong bài",
        parse_mode="Markdown"
    )

async def cmd_teach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    rule_text = " ".join(context.args).strip()
    if not rule_text:
        await update.message.reply_text(
            "⚠️ Cú pháp: `/teach [quy tắc]`\n\nVí dụ:\n"
            "`/teach luôn mở đầu bằng câu hỏi kích thích tò mò`\n"
            "`/teach không dùng từ \"tuy nhiên\"`",
            parse_mode="Markdown"
        )
        return
    rule_id = rules_add(rule_text)
    rules   = rules_load()
    await update.message.reply_text(
        f"✅ *Đã dạy bot quy tắc mới!*\n\n"
        f"🆔 ID: `{rule_id}`\n"
        f"📝 _{rule_text}_\n\n"
        f"📚 Tổng: *{len(rules)} quy tắc riêng*\n"
        f"Bot áp dụng ngay từ bài tiếp theo.",
        parse_mode="Markdown"
    )

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    rules = rules_load()
    if not rules:
        await update.message.reply_text("📭 Chưa có quy tắc riêng.\nDùng `/teach [quy tắc]`.", parse_mode="Markdown")
        return
    lines = "\n".join([f"*[{r['id']}]* _{r['rule']}_" for r in rules])
    await update.message.reply_text(
        f"📚 *Quy tắc riêng ({len(rules)}):*\n\n{lines}\n\n"
        f"➕ `/teach [quy tắc]`\n🗑️ `/delrule [id]`\n\n"
        f"_Bot luôn áp dụng SEO 2026 mặc định._",
        parse_mode="Markdown"
    )

async def cmd_delrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("⚠️ `/delrule [id]` — Dùng /rules xem ID.", parse_mode="Markdown")
        return
    try:
        rule_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ ID phải là số.", parse_mode="Markdown")
        return
    if rules_delete(rule_id):
        await update.message.reply_text(f"🗑️ Đã xoá quy tắc `{rule_id}`. Còn *{len(rules_load())}* quy tắc.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Không tìm thấy ID `{rule_id}`.", parse_mode="Markdown")

async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        f"🤖 *Model:* `{GROQ_MODEL}`\n\nKhả dụng:\n"
        f"• `llama-3.1-8b-instant` — Siêu nhanh\n"
        f"• `llama-3.3-70b-versatile` — Chất lượng ⭐\n"
        f"• `gemma2-9b-it` — Gemma 2\n"
        f"• `mixtral-8x7b-32768` — Context dài",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("🔍 Kiểm tra WordPress...")
    try:
        resp = requests.get(f"{WP_URL}/wp-json/wp/v2/posts?per_page=1", headers=get_wp_auth_header(), timeout=10)
        if resp.status_code == 200:
            await update.message.reply_text(
                f"✅ *WordPress OK!*\n🌐 `{WP_URL}`\n🤖 `{GROQ_MODEL}`\n📚 *{len(rules_load())}* quy tắc riêng",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ HTTP {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if pending_exists(user_id):
        pending_delete(user_id)
        await update.message.reply_text("❌ Đã huỷ. Gửi nội dung mới để bắt đầu.")
    else:
        await update.message.reply_text("ℹ️ Không có bài nào đang soạn.")

# ============================================================
# XỬ LÝ ẢNH (đơn lẻ hoặc album)
# ============================================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    user_id    = update.effective_user.id
    message    = update.message
    media_gid  = message.media_group_id  # None nếu ảnh đơn lẻ
    caption    = message.caption or ""
    photo_fid  = message.photo[-1].file_id

    if media_gid:
        # ── Album nhiều ảnh ──
        mg_add_photo(str(media_gid), photo_fid, caption)

        # Dùng job queue để xử lý sau khi nhận đủ ảnh (delay 2s)
        job_name = f"mg_{media_gid}_{user_id}"
        # Xoá job cũ nếu có
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()

        context.job_queue.run_once(
            process_media_group,
            when=2.5,
            name=job_name,
            data={"media_group_id": str(media_gid), "user_id": user_id, "chat_id": message.chat_id},
        )
    else:
        # ── Ảnh đơn lẻ ──
        msg = await message.reply_text("🖼️ Đang tải ảnh...")
        try:
            photo_file     = await context.bot.get_file(photo_fid)
            photo_bytes_io = io.BytesIO()
            await photo_file.download_to_memory(photo_bytes_io)
            image_bytes = photo_bytes_io.getvalue()

            raw_text = caption.strip() if len(caption.strip()) >= 20 else "Hoạt động mới tại NQH English"
            rules    = rules_load()
            await msg.edit_text(f"⚡ Groq AI viết bài...\n📚 {len(rules)} quy tắc + SEO 2026")
            seo_data = rewrite_with_groq(raw_text, num_inline_images=0)

            pending_set(user_id, {
                "seo_data":    seo_data,
                "images_bytes": [image_bytes],
                "image_url":   "",
                "source":      "Ảnh đơn Telegram",
            })

            preview = build_preview_text(seo_data, num_images=1)
            await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
        except Exception as e:
            logger.error(f"handle_photo error: {e}", exc_info=True)
            await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown")


async def process_media_group(context):
    """Job xử lý album ảnh sau khi đã nhận đủ."""
    job_data   = context.job.data
    mg_id      = job_data["media_group_id"]
    user_id    = job_data["user_id"]
    chat_id    = job_data["chat_id"]

    group_info = mg_get(mg_id)
    if not group_info:
        return

    photo_ids = group_info["photos"]
    caption   = group_info["caption"] or "Hoạt động mới tại NQH English"
    mg_delete(mg_id)

    msg = await context.bot.send_message(
        chat_id,
        f"📸 Nhận được *{len(photo_ids)} ảnh*. Đang tải và xử lý...",
        parse_mode="Markdown"
    )

    try:
        images_bytes = []
        for fid in photo_ids:
            pf  = await context.bot.get_file(fid)
            buf = io.BytesIO()
            await pf.download_to_memory(buf)
            images_bytes.append(buf.getvalue())

        num_inline = len(images_bytes) - 1  # ảnh 1 = thumbnail, còn lại chèn trong bài
        rules      = rules_load()
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"⚡ Groq AI viết bài với {len(images_bytes)} ảnh...\n📚 {len(rules)} quy tắc + SEO 2026"
        )

        seo_data = rewrite_with_groq(caption, num_inline_images=num_inline)

        pending_set(user_id, {
            "seo_data":     seo_data,
            "images_bytes": images_bytes,
            "image_url":    "",
            "source":       f"Album {len(images_bytes)} ảnh Telegram",
        })

        preview = build_preview_text(seo_data, num_images=len(images_bytes))
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=preview, parse_mode="Markdown",
            reply_markup=build_keyboard(user_id)
        )

    except Exception as e:
        logger.error(f"process_media_group error: {e}", exc_info=True)
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown"
        )

# ============================================================
# XỬ LÝ TEXT
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    user_input = update.message.text.strip()
    user_id    = update.effective_user.id

    # Đang có pending → chỉnh sửa
    if pending_exists(user_id):
        post_info      = pending_get(user_id)
        is_fb_link     = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/", user_input))
        is_new_content = is_fb_link or len(user_input) > 200

        if not is_new_content:
            msg = await update.message.reply_text(
                f"✏️ Đang chỉnh: _\"{user_input}\"_...", parse_mode="Markdown"
            )
            try:
                cur_seo      = post_info["seo_data"]
                new_seo      = refine_with_groq(cur_seo.get("content_html",""), user_input, cur_seo)
                pending_update_seo(user_id, new_seo)
                num_img      = len(post_info.get("images_bytes") or [])
                preview      = build_preview_text(new_seo, num_images=num_img)
                await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
            except Exception as e:
                await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown")
            return
        pending_delete(user_id)

    # Tạo bài mới
    is_fb_link = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/", user_input))
    msg = await update.message.reply_text("🔍 Đang scrape..." if is_fb_link else "⏳ Đang xử lý...")

    try:
        if is_fb_link:
            scraped   = scrape_facebook_post(user_input)
            raw_text  = f"{scraped['title']}\n\n{scraped['content']}"
            image_url = scraped.get("image_url","")
        else:
            raw_text  = user_input
            image_url = ""

        if len(raw_text.strip()) < 50:
            await msg.edit_text("⚠️ Nội dung quá ngắn.\n• Paste text trực tiếp\n• Hoặc gửi ảnh kèm caption")
            return

        rules = rules_load()
        await msg.edit_text(f"⚡ Groq AI viết bài...\n📚 {len(rules)} quy tắc + SEO 2026")
        seo_data = rewrite_with_groq(raw_text)

        pending_set(user_id, {
            "seo_data":     seo_data,
            "images_bytes": [],
            "image_url":    image_url,
            "source":       user_input if is_fb_link else "Text",
        })

        num_img = 1 if image_url else 0
        preview = build_preview_text(seo_data, num_images=num_img)
        await msg.edit_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))

    except Exception as e:
        logger.error(f"handle_message error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Lỗi: `{str(e)[:200]}`", parse_mode="Markdown")

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
        await query.edit_message_text("❌ Đã huỷ.")

    elif data.startswith("rewrite_"):
        post_info = pending_get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Gửi lại.")
            return
        await query.edit_message_text("🔄 Viết lại với phong cách khác...")
        try:
            num_img  = len(post_info.get("images_bytes") or [])
            num_inl  = max(0, num_img - 1)
            raw      = f"[Viết lại phong cách khác, sáng tạo hơn]\n{post_info['seo_data']['content_html']}"
            seo_data = rewrite_with_groq(raw, num_inline_images=num_inl)
            pending_update_seo(user_id, seo_data)
            preview = build_preview_text(seo_data, num_images=num_img)
            await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=build_keyboard(user_id))
        except Exception as e:
            await query.edit_message_text(f"❌ Lỗi: {e}")

    elif data.startswith("publish_"):
        post_info = pending_get(user_id)
        if not post_info:
            await query.edit_message_text("⚠️ Không tìm thấy nội dung. Gửi lại.")
            return

        await query.edit_message_text("📤 Đang đăng bài...")
        try:
            images_bytes = post_info.get("images_bytes") or []
            thumbnail_id = None
            inline_urls  = []

            if images_bytes:
                total = len(images_bytes)
                for i, img_bytes in enumerate(images_bytes):
                    if img_bytes is None:
                        continue
                    await query.edit_message_text(f"🖼️ Đang upload ảnh {i+1}/{total}...")
                    mid, murl = upload_image_bytes_to_wp(img_bytes, f"nqh-image-{i+1}.jpg", "image/jpeg")
                    if i == 0:
                        thumbnail_id = mid  # Ảnh đầu → thumbnail
                    else:
                        inline_urls.append(murl)  # Ảnh còn lại → chèn trong bài

            elif post_info.get("image_url"):
                await query.edit_message_text("🖼️ Upload ảnh Facebook...")
                thumbnail_id, _ = upload_image_url_to_wp(post_info["image_url"])

            await query.edit_message_text("📝 Đang đăng bài lên WordPress...")
            result   = post_to_wordpress(post_info["seo_data"], thumbnail_id, inline_urls)
            post_url = result.get("link","")
            post_id  = result.get("id","")
            edit_url = f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"

            pending_delete(user_id)

            total_imgs = len(images_bytes) if images_bytes else (1 if post_info.get("image_url") else 0)
            await query.edit_message_text(
                f"🎉 *Đăng bài thành công!*\n\n"
                f"📌 *Tiêu đề:* {post_info['seo_data']['seo_title']}\n"
                f"🖼️ *Ảnh:* {'✅ ' + str(total_imgs) + ' ảnh đã upload' if total_imgs else '❌ Không có'}\n"
                f"🔗 *Xem bài:* [Nhấn vào đây]({post_url})\n"
                f"✏️ *Chỉnh sửa:* [WP Admin]({edit_url})\n\n"
                f"⚡ *Groq AI* `{GROQ_MODEL}`",
                parse_mode="Markdown", disable_web_page_preview=False,
            )
        except Exception as e:
            logger.error(f"Publish error: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Lỗi đăng bài: `{str(e)[:300]}`", parse_mode="Markdown")

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

    logger.info(f"🤖 NQH English Bot v6.0 | {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
