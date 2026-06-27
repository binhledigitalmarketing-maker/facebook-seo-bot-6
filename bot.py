"""
NQH English Bot v7.0
- 1 ảnh đơn hoặc nhiều ảnh album đều hoạt động
- Không dùng job-queue (tránh lỗi Railway)
- Dùng asyncio.sleep để gom album ảnh
- SEO 2026 đầy đủ + dạy bot quy tắc riêng
"""

import os, re, json, logging, requests, base64, io, asyncio
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
ALLOWED_USER_IDS = [int(x) for x in os.environ.get("ALLOWED_USER_IDS","").split(",") if x.strip()]
GROQ_MODEL       = os.environ.get("GROQ_MODEL","llama-3.3-70b-versatile")

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# FILE LƯU TRỮ
# ============================================================
PENDING_FILE     = "/tmp/pending_posts.json"
RULES_FILE       = "/tmp/bot_rules.json"
MG_FILE          = "/tmp/media_groups.json"   # gom album ảnh

# Dict in-memory để track album đang gom (key = media_group_id)
_mg_tasks: dict = {}   # media_group_id → asyncio.Task

# ============================================================
# SEO 2026
# ============================================================
SEO_2026_BASE = """
CHUẨN SEO 2026 BẮT BUỘC:

1. E-E-A-T: thể hiện trải nghiệm thực tế, chuyên môn, độ tin cậy; đưa ví dụ cụ thể
2. Search Intent: đáp ứng đúng ý định người đọc, không lan man
3. Information Gain: đưa góc nhìn mới, không viết lại bài cũ
4. AI Search (Google AI Overview, ChatGPT): định nghĩa cô đọng đầu bài, H2/H3 logic rõ ràng
5. On-page: H1 55-65 ký tự; meta 150-160 ký tự; đoạn 3-4 dòng; bullet points khi liệt kê
6. UX: hook câu đầu tiên, viết cho người đọc trước, không nhồi từ khóa
7. HTML chuẩn: <h2><h3><p><ul><ol><li><strong>, alt text ảnh tự nhiên
"""

# ============================================================
# RULES - bộ nhớ học tập
# ============================================================
def rules_load() -> list:
    try:
        with open(RULES_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return []

def rules_save(r):
    try:
        with open(RULES_FILE,"w",encoding="utf-8") as f: json.dump(r,f,ensure_ascii=False,indent=2)
    except Exception as e: logger.error(f"rules_save: {e}")

def rules_add(rule:str)->int:
    r=rules_load(); nid=max([x["id"] for x in r],default=0)+1
    r.append({"id":nid,"rule":rule}); rules_save(r); return nid

def rules_delete(rid:int)->bool:
    r=rules_load(); nr=[x for x in r if x["id"]!=rid]
    if len(nr)==len(r): return False
    rules_save(nr); return True

def rules_prompt()->str:
    r=rules_load()
    if not r: return ""
    return "\nQUY TẮC RIÊNG NQH ENGLISH:\n"+"\n".join([f"- [{x['id']}] {x['rule']}" for x in r])+"\n"

# ============================================================
# MEDIA GROUP - gom album ảnh (lưu file JSON, bền vững)
# ============================================================
def mg_load()->dict:
    try:
        with open(MG_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}

def mg_save(d):
    try:
        with open(MG_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False)
    except: pass

def mg_add(gid:str, fid:str, caption:str=""):
    d=mg_load()
    if gid not in d: d[gid]={"photos":[],"caption":""}
    if caption and not d[gid]["caption"]: d[gid]["caption"]=caption
    if fid not in d[gid]["photos"]: d[gid]["photos"].append(fid)
    mg_save(d)

def mg_get(gid:str)->dict|None: return mg_load().get(gid)
def mg_del(gid:str):
    d=mg_load(); d.pop(gid,None); mg_save(d)

# ============================================================
# PENDING POSTS
# ============================================================
def _load_p()->dict:
    try:
        with open(PENDING_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}

def _save_p(d):
    try:
        with open(PENDING_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    except Exception as e: logger.error(f"_save_p: {e}")

def pending_set(uid:int, val:dict):
    d=_load_p(); e={**val}
    if e.get("images_bytes"):
        e["images_bytes"]=[base64.b64encode(b).decode() if isinstance(b,(bytes,bytearray)) else b for b in e["images_bytes"]]
    d[str(uid)]=e; _save_p(d)

def pending_get(uid:int)->dict|None:
    d=_load_p(); e=d.get(str(uid))
    if not e: return None
    if e.get("images_bytes"):
        e["images_bytes"]=[base64.b64decode(b) if isinstance(b,str) else b for b in e["images_bytes"]]
    return e

def pending_upd(uid:int, seo:dict):
    d=_load_p()
    if str(uid) in d: d[str(uid)]["seo_data"]=seo; _save_p(d)

def pending_del(uid:int):
    d=_load_p(); d.pop(str(uid),None); _save_p(d)

def pending_has(uid:int)->bool: return str(uid) in _load_p()

# ============================================================
# HELPER
# ============================================================
def is_ok(uid:int)->bool:
    return True if not ALLOWED_USER_IDS else uid in ALLOWED_USER_IDS

def wp_auth()->dict:
    return {"Authorization":"Basic "+base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()}

def keyboard(uid:int)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Đăng WordPress",callback_data=f"publish_{uid}"),
         InlineKeyboardButton("❌ Huỷ",callback_data=f"cancel_{uid}")],
        [InlineKeyboardButton("🔄 Viết lại",callback_data=f"rewrite_{uid}")],
    ])

def preview_text(seo:dict, n_img:int=0)->str:
    from html.parser import HTMLParser
    class TX(HTMLParser):
        def __init__(self): super().__init__(); self.t=[]
        def handle_data(self,d): self.t.append(d)
    tx=TX(); tx.feed(seo.get("content_html","")); plain=" ".join(tx.t)[:500]
    img_note=(f"✅ {n_img} ảnh (thumbnail + {n_img-1} trong bài)" if n_img>1
              else ("✅ 1 ảnh thumbnail" if n_img==1 else "❌ Không có"))
    r=rules_load()
    return (
        f"📝 *PREVIEW BÀI VIẾT*\n{'━'*28}\n\n"
        f"🏷️ *Tiêu đề:* {seo['seo_title']}\n\n"
        f"📊 *Từ khóa:* `{seo.get('focus_keyword','N/A')}`\n\n"
        f"📋 *Meta:* _{seo['meta_description']}_\n\n"
        f"🏷️ *Tags:* {', '.join(seo.get('tags',[]))}\n\n"
        f"🖼️ *Ảnh:* {img_note}\n"
        f"📚 *{len(r)} quy tắc riêng* + SEO 2026\n\n"
        f"{'━'*28}\n📖 *Nội dung:*\n{plain}...\n\n"
        f"{'━'*28}\n"
        f"💬 Nhắn yêu cầu chỉnh trực tiếp:\n"
        f"_\"viết ngắn hơn\"_, _\"thêm emoji\"_, _\"tone vui hơn\"_\n\n"
        f"👇 Hoặc bấm nút:"
    )

# ============================================================
# SCRAPE FACEBOOK
# ============================================================
def scrape_fb(url:str)->dict:
    h={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0","Accept-Language":"vi-VN,vi;q=0.9"}
    try:
        r=requests.get(url,headers=h,timeout=15)
        s=BeautifulSoup(r.text,"html.parser")
        def og(p): t=s.find("meta",property=p); return t.get("content","") if t else ""
        title=og("og:title"); desc=og("og:description"); img=og("og:image")
        body=""
        for t in s.find_all(["p","div","span"]):
            tx=t.get_text(" ",strip=True)
            if len(tx)>100: body+=tx+"\n\n"
            if len(body)>2000: break
        return {"title":title or "Bài từ Facebook","content":desc or body[:2000] or "Không có nội dung","image_url":img,"source":url}
    except Exception as e:
        logger.error(f"scrape_fb: {e}")
        return {"title":"Bài từ Facebook","content":"","image_url":"","source":url}

# ============================================================
# GROQ AI
# ============================================================
def _groq(sys_p:str, usr_p:str)->dict:
    try:
        r=groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"system","content":sys_p},{"role":"user","content":usr_p}],
            temperature=0.7, max_tokens=3000
        )
        t=r.choices[0].message.content.strip()
        t=re.sub(r"```json\s*","",t); t=re.sub(r"```\s*","",t)
        return json.loads(t)
    except json.JSONDecodeError as e:
        logger.error(f"JSON err: {e}")
        return {"seo_title":"NQH English","meta_description":"NQH English.","focus_keyword":"",
                "content_html":"<p>Lỗi tạo nội dung.</p>","tags":[],"category_suggestion":"Tin tức"}
    except Exception as e:
        logger.error(f"Groq err: {e}"); raise

def write_post(raw:str, n_inline:int=0)->dict:
    img_ins=""
    if n_inline>0:
        img_ins=(f"\nCHÈN ẢNH: Đặt {{{{IMAGE_1}}}}, {{{{IMAGE_2}}}}... (tối đa {n_inline} placeholder)"
                 f" sau mỗi đoạn H2 trong content_html.\n"
                 f"Ví dụ: <h2>Tiêu đề</h2><p>Nội dung...</p>{{{{IMAGE_1}}}}<h2>Tiêu đề 2</h2>...\n")
    return _groq(
        "Bạn là SEO Content Writer chuyên nghiệp cho NQH English - trung tâm tiếng Anh thiếu nhi. "
        "Tone thân thiện, vui tươi. Chỉ trả về JSON thuần túy.",
        f"Viết bài blog chuẩn SEO cho NQH English:\n\nNỘI DUNG GỐC:\n{raw}\n\n"
        f"{SEO_2026_BASE}{rules_prompt()}{img_ins}\n"
        f"YÊU CẦU: tối thiểu 600 từ, hook câu đầu, đề cập NQH English 1-2 lần, KHÔNG copy nguyên văn.\n\n"
        f"JSON:\n{{\n  \"seo_title\": \"...\",\n  \"meta_description\": \"...\",\n"
        f"  \"focus_keyword\": \"...\",\n  \"content_html\": \"...\",\n"
        f"  \"tags\": [\"tag1\",\"tag2\",\"tag3\"],\n  \"category_suggestion\": \"...\"\n}}"
    )

def refine_post(html:str, req:str, seo:dict)->dict:
    return _groq(
        "Bạn là SEO Content Writer cho NQH English. Thực hiện đúng yêu cầu chỉnh sửa, giữ placeholder {{IMAGE_N}}. JSON thuần túy.",
        f"BÀI HIỆN TẠI:\nTiêu đề: {seo.get('seo_title','')}\nHTML: {html}\n\n"
        f"YÊU CẦU CHỈNH: \"{req}\"\n\n{SEO_2026_BASE}{rules_prompt()}\n"
        f"Giữ {{{{IMAGE_N}}}} nếu có. JSON:\n{{\n  \"seo_title\": \"...\",\n  \"meta_description\": \"...\",\n"
        f"  \"focus_keyword\": \"...\",\n  \"content_html\": \"...\",\n"
        f"  \"tags\": [\"tag1\",\"tag2\"],\n  \"category_suggestion\": \"...\"\n}}"
    )

# ============================================================
# WORDPRESS
# ============================================================
def wp_upload(img_bytes:bytes, fname:str="image.jpg", ct:str="image/jpeg")->tuple:
    try:
        h={**wp_auth(),"Content-Disposition":f'attachment; filename="{fname}"',"Content-Type":ct}
        r=requests.post(f"{WP_URL}/wp-json/wp/v2/media",headers=h,data=img_bytes,timeout=30)
        if r.status_code in (200,201):
            d=r.json(); return d.get("id"), d.get("source_url","")
        logger.error(f"wp_upload {r.status_code}")
    except Exception as e: logger.error(f"wp_upload: {e}")
    return None, None

def wp_upload_url(url:str)->tuple:
    if not url: return None,None
    try:
        r=requests.get(url,timeout=15)
        if r.status_code!=200: return None,None
        ct=r.headers.get("Content-Type","image/jpeg")
        ext="jpg" if "jpeg" in ct else ct.split("/")[-1]
        return wp_upload(r.content,f"fb-img.{ext}",ct)
    except Exception as e: logger.error(f"wp_upload_url: {e}"); return None,None

def wp_post(seo:dict, thumb_id:int|None, inline_urls:list)->dict:
    h={**wp_auth(),"Content-Type":"application/json"}
    html=seo["content_html"]
    for i,url in enumerate(inline_urls,1):
        if url:
            tag=(f'<figure class="wp-block-image size-large">'
                 f'<img src="{url}" alt="{seo["seo_title"]} {i}" loading="lazy"/></figure>')
            html=html.replace(f"{{{{IMAGE_{i}}}}}",tag)
    html=re.sub(r"\{\{IMAGE_\d+\}\}","",html)  # xoá placeholder thừa

    payload={"title":seo["seo_title"],"content":html,"status":"publish","excerpt":seo["meta_description"]}
    if thumb_id: payload["featured_media"]=thumb_id

    if seo.get("tags"):
        ids=[]
        for name in seo["tags"]:
            tr=requests.post(f"{WP_URL}/wp-json/wp/v2/tags",headers=h,json={"name":name},timeout=10)
            if tr.status_code in (200,201): ids.append(tr.json()["id"])
        if ids: payload["tags"]=ids

    r=requests.post(f"{WP_URL}/wp-json/wp/v2/posts",headers=h,json=payload,timeout=30)
    r.raise_for_status(); return r.json()

# ============================================================
# XỬ LÝ ẢNH - hàm chung xử lý sau khi đã gom đủ ảnh
# ============================================================
async def process_photos(bot, chat_id:int, user_id:int, file_ids:list, caption:str):
    """Tải ảnh, viết bài AI, hiện preview."""
    msg = await bot.send_message(chat_id, f"📸 Nhận *{len(file_ids)} ảnh*. Đang tải...", parse_mode="Markdown")
    try:
        images_bytes=[]
        for i,fid in enumerate(file_ids):
            pf=await bot.get_file(fid)
            buf=io.BytesIO()
            await pf.download_to_memory(buf)
            images_bytes.append(buf.getvalue())

        raw_text = caption.strip() if len(caption.strip())>=10 else "Hoạt động mới tại NQH English"
        n_inline = len(images_bytes)-1  # ảnh 1 = thumbnail, còn lại chèn trong bài
        r        = rules_load()

        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"⚡ Groq AI viết bài ({len(images_bytes)} ảnh)...\n📚 {len(r)} quy tắc + SEO 2026"
        )

        seo = write_post(raw_text, n_inline_images=n_inline)

        pending_set(user_id,{
            "seo_data":     seo,
            "images_bytes": images_bytes,
            "image_url":    "",
            "source":       f"{len(images_bytes)} ảnh Telegram",
        })

        pv = preview_text(seo, n_img=len(images_bytes))
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=pv, parse_mode="Markdown", reply_markup=keyboard(user_id)
        )

    except Exception as e:
        logger.error(f"process_photos: {e}", exc_info=True)
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"❌ Lỗi:\n`{str(e)[:300]}`", parse_mode="Markdown"
        )

# ============================================================
# HANDLER: ẢNH (đơn lẻ hoặc album)
# ============================================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_ok(update.effective_user.id): return
    user_id   = update.effective_user.id
    msg       = update.message
    gid       = msg.media_group_id   # None = ảnh đơn
    caption   = msg.caption or ""
    fid       = msg.photo[-1].file_id
    chat_id   = msg.chat_id

    if gid:
        # ── ALBUM: gom ảnh vào file, đợi 2.5s rồi xử lý ──
        mg_add(str(gid), fid, caption)

        # Huỷ task cũ nếu có
        old_task = _mg_tasks.get(gid)
        if old_task and not old_task.done():
            old_task.cancel()

        async def _delayed():
            await asyncio.sleep(2.5)
            info = mg_get(str(gid))
            mg_del(str(gid))
            _mg_tasks.pop(gid, None)
            if info:
                await process_photos(context.bot, chat_id, user_id, info["photos"], info.get("caption",""))

        _mg_tasks[gid] = asyncio.create_task(_delayed())

    else:
        # ── ẢNH ĐƠN ──
        await process_photos(context.bot, chat_id, user_id, [fid], caption)

# ============================================================
# HANDLER: TEXT
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_ok(update.effective_user.id): return
    txt = update.message.text.strip()
    uid = update.effective_user.id

    # Đang có pending → chỉnh sửa
    if pending_has(uid):
        pi         = pending_get(uid)
        is_fb      = bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/",txt))
        is_new     = is_fb or len(txt)>200

        if not is_new:
            m=await update.message.reply_text(f"✏️ Đang chỉnh: _\"{txt[:80]}\"_...",parse_mode="Markdown")
            try:
                cur=pi["seo_data"]
                new=refine_post(cur.get("content_html",""),txt,cur)
                pending_upd(uid,new)
                ni=len(pi.get("images_bytes") or [])
                await m.edit_text(preview_text(new,n_img=ni),parse_mode="Markdown",reply_markup=keyboard(uid))
            except Exception as e:
                await m.edit_text(f"❌ Lỗi chỉnh: `{str(e)[:200]}`",parse_mode="Markdown")
            return
        pending_del(uid)

    # Tạo bài mới
    is_fb=bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/",txt))
    m=await update.message.reply_text("🔍 Đang scrape Facebook..." if is_fb else "⏳ Đang xử lý...")
    try:
        if is_fb:
            sc=scrape_fb(txt); raw=f"{sc['title']}\n\n{sc['content']}"; img_url=sc.get("image_url","")
        else:
            raw=txt; img_url=""

        if len(raw.strip())<50:
            await m.edit_text("⚠️ Nội dung quá ngắn.\n• Paste text trực tiếp\n• Hoặc gửi ảnh kèm caption")
            return

        r=rules_load()
        await m.edit_text(f"⚡ Groq AI viết bài...\n📚 {len(r)} quy tắc + SEO 2026")
        seo=write_post(raw)

        pending_set(uid,{"seo_data":seo,"images_bytes":[],"image_url":img_url,"source":txt if is_fb else "Text"})
        ni=1 if img_url else 0
        await m.edit_text(preview_text(seo,n_img=ni),parse_mode="Markdown",reply_markup=keyboard(uid))

    except Exception as e:
        logger.error(f"handle_message: {e}",exc_info=True)
        await m.edit_text(f"❌ Lỗi: `{str(e)[:200]}`\nVui lòng thử lại.",parse_mode="Markdown")

# ============================================================
# HANDLER: NÚT BẤM
# ============================================================
async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=update.effective_user.id; await q.answer()
    d=q.data

    if d.startswith("cancel_"):
        pending_del(uid); await q.edit_message_text("❌ Đã huỷ. Gửi nội dung mới để bắt đầu.")

    elif d.startswith("rewrite_"):
        pi=pending_get(uid)
        if not pi: await q.edit_message_text("⚠️ Không tìm thấy. Gửi lại."); return
        await q.edit_message_text("🔄 Đang viết lại...")
        try:
            ni=max(0,len(pi.get("images_bytes") or [])-1)
            raw=f"[Viết lại phong cách khác, sáng tạo hơn]\n{pi['seo_data']['content_html']}"
            seo=write_post(raw,n_inline_images=ni)
            pending_upd(uid,seo)
            nim=len(pi.get("images_bytes") or [])
            await q.edit_message_text(preview_text(seo,n_img=nim),parse_mode="Markdown",reply_markup=keyboard(uid))
        except Exception as e:
            await q.edit_message_text(f"❌ Lỗi: {e}")

    elif d.startswith("publish_"):
        pi=pending_get(uid)
        if not pi: await q.edit_message_text("⚠️ Không tìm thấy. Gửi lại."); return
        await q.edit_message_text("📤 Đang đăng bài...")
        try:
            imgs=pi.get("images_bytes") or []
            thumb_id=None; inline_urls=[]

            if imgs:
                total=len(imgs)
                for i,ib in enumerate(imgs):
                    if not ib: continue
                    await q.edit_message_text(f"🖼️ Upload ảnh {i+1}/{total}...")
                    mid,murl=wp_upload(ib,f"nqh-{i+1}.jpg","image/jpeg")
                    if i==0: thumb_id=mid
                    else: inline_urls.append(murl)
            elif pi.get("image_url"):
                await q.edit_message_text("🖼️ Upload ảnh Facebook...")
                thumb_id,_=wp_upload_url(pi["image_url"])

            await q.edit_message_text("📝 Đang đăng bài WordPress...")
            res=wp_post(pi["seo_data"],thumb_id,inline_urls)
            url=res.get("link",""); pid=res.get("id","")
            edit=f"{WP_URL}/wp-admin/post.php?post={pid}&action=edit"
            pending_del(uid)

            total_img=len(imgs) if imgs else (1 if pi.get("image_url") else 0)
            await q.edit_message_text(
                f"🎉 *Đăng thành công!*\n\n"
                f"📌 {pi['seo_data']['seo_title']}\n"
                f"🖼️ {'✅ '+str(total_img)+' ảnh' if total_img else '❌ Không có ảnh'}\n"
                f"🔗 [Xem bài]({url})\n✏️ [WP Admin]({edit})\n\n"
                f"⚡ Groq `{GROQ_MODEL}`",
                parse_mode="Markdown",disable_web_page_preview=False
            )
        except Exception as e:
            logger.error(f"publish: {e}",exc_info=True)
            await q.edit_message_text(f"❌ Lỗi đăng: `{str(e)[:300]}`",parse_mode="Markdown")

# ============================================================
# LỆNH
# ============================================================
async def cmd_start(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    r=rules_load()
    await u.message.reply_text(
        "👋 *NQH English Bot v7.0*\n\n"
        "📸 *Gửi ảnh:*\n"
        "• 1 ảnh + caption → viết bài + thumbnail\n"
        "• Nhiều ảnh (album) + caption → ảnh 1 thumbnail, ảnh 2+ chèn trong bài\n\n"
        "📝 *Gửi nội dung:*\n"
        "• Link Facebook → scrape + viết bài\n"
        "• Text → viết bài từ nội dung\n\n"
        "✏️ *Sau preview:* nhắn yêu cầu chỉnh trực tiếp\n\n"
        "🧠 *Dạy bot:*\n"
        "/teach [quy tắc] — nhớ mãi\n"
        "/rules — xem quy tắc\n"
        "/delrule [id] — xoá\n\n"
        f"📚 *{len(r)} quy tắc riêng* + SEO 2026\n/help để xem đầy đủ",
        parse_mode="Markdown"
    )

async def cmd_help(u:Update,c:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📚 *Lệnh:*\n\n"
        "/status — Kiểm tra WP\n/model — Model Groq\n/cancel — Huỷ bài\n\n"
        "*Dạy bot:*\n"
        "/teach [nội dung]\n/rules\n/delrule [id]\n\n"
        "*Ví dụ /teach:*\n"
        "`/teach mở đầu bằng câu hỏi kích thích tò mò`\n"
        "`/teach không dùng từ \"tuy nhiên\"`\n"
        "`/teach câu ngắn, tối đa 20 từ`\n"
        "`/teach mỗi bài có ít nhất 1 ví dụ thực tế`\n\n"
        "*Gửi nhiều ảnh:*\n"
        "Chọn nhiều ảnh → Gửi dưới dạng album trong Telegram\n"
        "Viết caption → Bot tự xử lý",
        parse_mode="Markdown"
    )

async def cmd_teach(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    rule=" ".join(c.args).strip()
    if not rule:
        await u.message.reply_text("⚠️ `/teach [quy tắc]`\nVí dụ: `/teach mở đầu bằng câu hỏi`",parse_mode="Markdown")
        return
    rid=rules_add(rule); r=rules_load()
    await u.message.reply_text(
        f"✅ *Đã dạy bot!*\n🆔 `{rid}`\n📝 _{rule}_\n\n📚 Tổng *{len(r)} quy tắc*",
        parse_mode="Markdown"
    )

async def cmd_rules(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    r=rules_load()
    if not r:
        await u.message.reply_text("📭 Chưa có quy tắc.\n`/teach [quy tắc]`",parse_mode="Markdown"); return
    lines="\n".join([f"*[{x['id']}]* _{x['rule']}_" for x in r])
    await u.message.reply_text(f"📚 *{len(r)} quy tắc riêng:*\n\n{lines}\n\n➕ `/teach` 🗑️ `/delrule [id]`",parse_mode="Markdown")

async def cmd_delrule(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    if not c.args:
        await u.message.reply_text("⚠️ `/delrule [id]`",parse_mode="Markdown"); return
    try: rid=int(c.args[0])
    except: await u.message.reply_text("⚠️ ID phải là số.",parse_mode="Markdown"); return
    if rules_delete(rid):
        await u.message.reply_text(f"🗑️ Xoá quy tắc `{rid}`. Còn *{len(rules_load())}*.",parse_mode="Markdown")
    else:
        await u.message.reply_text(f"⚠️ Không tìm thấy ID `{rid}`.",parse_mode="Markdown")

async def cmd_model(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text(
        f"🤖 *Model:* `{GROQ_MODEL}`\n\n"
        "• `llama-3.1-8b-instant` — Siêu nhanh\n"
        "• `llama-3.3-70b-versatile` — Chất lượng ⭐\n"
        "• `gemma2-9b-it` — Gemma 2\n"
        "• `mixtral-8x7b-32768` — Context dài\n\n"
        "Đổi qua `GROQ_MODEL` trên Railway.",parse_mode="Markdown"
    )

async def cmd_status(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text("🔍 Kiểm tra WordPress...")
    try:
        r=requests.get(f"{WP_URL}/wp-json/wp/v2/posts?per_page=1",headers=wp_auth(),timeout=10)
        if r.status_code==200:
            await u.message.reply_text(
                f"✅ *WordPress OK!*\n🌐 `{WP_URL}`\n🤖 `{GROQ_MODEL}`\n📚 *{len(rules_load())}* quy tắc",
                parse_mode="Markdown"
            )
        else: await u.message.reply_text(f"❌ HTTP {r.status_code}")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_cancel(u:Update,c:ContextTypes.DEFAULT_TYPE):
    uid=u.effective_user.id
    if pending_has(uid):
        pending_del(uid); await u.message.reply_text("❌ Đã huỷ. Gửi nội dung mới.")
    else: await u.message.reply_text("ℹ️ Không có bài đang soạn.")

# ============================================================
# MAIN
# ============================================================
def main():
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("model",  cmd_model))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("teach",  cmd_teach))
    app.add_handler(CommandHandler("rules",  cmd_rules))
    app.add_handler(CommandHandler("delrule",cmd_delrule))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_cb))
    logger.info(f"🤖 NQH English Bot v7.0 | {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
