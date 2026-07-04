"""
NQH English Bot v9.0
1. Scrape tất cả ảnh từ link bài đăng (tối đa 10 ảnh)
2. BotCommand menu / gợi ý lệnh đẹp
3. /ask [câu hỏi] - hỏi đáp AI trực tiếp trong chat
4. /token - kiểm tra token Groq còn lại
5. Prompt Yoast SEO 90+ điểm: outbound links, internal links,
   keyphrase distribution, alt text, meta length, sentence length,
   subheading distribution, paragraph length, transition words
"""

import os, re, json, logging, requests, base64, io, asyncio
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
# FILES
# ============================================================
PENDING_FILE = "/tmp/pending_posts.json"
RULES_FILE   = "/tmp/bot_rules.json"
MG_FILE      = "/tmp/media_groups.json"
STATS_FILE   = "/tmp/post_stats.json"
TOKEN_FILE   = "/tmp/token_usage.json"

_mg_tasks: dict = {}

# ============================================================
# YOAST SEO 90+ PROMPT - đầy đủ tất cả tiêu chí
# ============================================================
YOAST_SEO_PROMPT = """
TIÊU CHUẨN YOAST SEO 90+ ĐIỂM - BẮT BUỘC ĐÁP ỨNG ĐẦY ĐỦ:

=== SEO ANALYSIS (phần xanh tất cả) ===

1. KEYPHRASE (Từ khóa chính):
   - Đặt focus keyphrase trong: tiêu đề H1, meta description, URL slug, đoạn mở đầu (100 từ đầu)
   - Keyphrase xuất hiện đều trong toàn bài (keyphrase distribution), mật độ 0.5-3%
   - Keyphrase trong alt text ít nhất 1 ảnh
   - Dùng keyphrase và các biến thể tự nhiên (LSI keywords)

2. OUTBOUND LINKS (liên kết ngoài):
   - BẮT BUỘC có ít nhất 1-2 external link đến nguồn uy tín
   - Ví dụ: link đến British Council, Cambridge, Bộ GD&ĐT, báo uy tín
   - Dùng thẻ: <a href="URL" target="_blank" rel="noopener">anchor text</a>

3. INTERNAL LINKS (liên kết nội bộ):
   - BẮT BUỘC có ít nhất 1-2 internal link đến bài viết liên quan
   - Dùng placeholder: <a href="/bai-viet-lien-quan">anchor text phù hợp</a>
   - Anchor text phải tự nhiên, chứa từ khóa liên quan

4. META DESCRIPTION:
   - Độ dài: 120-156 ký tự (không được ngắn hơn 120)
   - Chứa focus keyphrase
   - Hấp dẫn, thôi thúc người dùng click

5. SEO TITLE: 55-65 ký tự, chứa keyphrase ở đầu nếu có thể

=== READABILITY ANALYSIS (phần xanh tất cả) ===

6. SENTENCE LENGTH (câu ngắn):
   - QUAN TRỌNG: tối đa 25% câu được phép dài hơn 20 từ
   - Viết câu ngắn, rõ ràng, trung bình 15-18 từ/câu
   - Tách câu dài thành 2 câu ngắn

7. PARAGRAPH LENGTH (đoạn ngắn):
   - Mỗi đoạn <p> tối đa 150 từ (~6-8 dòng)
   - Tách đoạn thường xuyên để dễ đọc

8. SUBHEADING DISTRIBUTION (phân bổ H2/H3):
   - Cứ 300 từ phải có ít nhất 1 thẻ H2 hoặc H3
   - Không để đoạn text quá dài không có heading

9. TRANSITION WORDS (từ chuyển tiếp):
   - Ít nhất 30% câu phải dùng transition words
   - Tiếng Việt: "đầu tiên", "tiếp theo", "bên cạnh đó", "ngoài ra", "quan trọng hơn",
     "vì vậy", "do đó", "kết quả là", "tóm lại", "cuối cùng", "đặc biệt", "thật ra",
     "hơn nữa", "tuy nhiên", "mặc dù vậy", "ví dụ như", "cụ thể là"

10. PASSIVE VOICE: Hạn chế câu bị động, tối đa 10% câu dùng bị động

11. TEXT LENGTH: Tối thiểu 600 từ (lý tưởng 800-1200 từ)

12. IMAGE ALT TEXT:
    - Mỗi ảnh PHẢI có alt text
    - Alt text chứa keyphrase hoặc mô tả tự nhiên

=== CẤU TRÚC HTML CHUẨN ===
- Dùng: <h2>, <h3>, <p>, <ul>, <ol>, <li>, <strong>, <em>
- Mỗi <p> là 1 đoạn riêng biệt, không nhét quá nhiều vào 1 thẻ <p>
- <strong> cho từ/cụm từ quan trọng (không lạm dụng)

=== E-E-A-T & 2026 ===
- Experience: ví dụ thực tế, trải nghiệm
- Expertise: thông tin chuyên môn, số liệu
- Authoritativeness: trích dẫn nguồn
- Trustworthiness: thông tin chính xác, không phóng đại
"""

# ============================================================
# RULES
# ============================================================
def rules_load()->list:
    try:
        with open(RULES_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return []

def rules_save(r):
    try:
        with open(RULES_FILE,"w",encoding="utf-8") as f: json.dump(r,f,ensure_ascii=False,indent=2)
    except Exception as e: logger.error(f"rules_save:{e}")

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
# TOKEN TRACKING
# ============================================================
def token_load()->dict:
    try:
        with open(TOKEN_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {"total_input":0,"total_output":0,"calls":0,"history":[]}

def token_save(d):
    try:
        with open(TOKEN_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    except: pass

def token_add(inp:int, out:int):
    d=token_load()
    d["total_input"]+=inp; d["total_output"]+=out; d["calls"]+=1
    d["history"].append({"time":datetime.now().strftime("%H:%M %d/%m"),"in":inp,"out":out})
    if len(d["history"])>50: d["history"]=d["history"][-50:]
    token_save(d)

def token_text()->str:
    d=token_load()
    ti=d.get("total_input",0); to=d.get("total_output",0)
    calls=d.get("calls",0)
    total=ti+to
    # Groq free tier: khoảng 14,400 req/day, không giới hạn token rõ ràng
    # Hiển thị thống kê sử dụng
    recent=d.get("history",[])[-5:][::-1]
    recent_lines="\n".join([f"  [{r['time']}] in:{r['in']:,} out:{r['out']:,}" for r in recent]) if recent else "  (chưa có)"
    return (
        f"🔢 *THỐNG KÊ TOKEN GROQ*\n{'━'*28}\n\n"
        f"📥 *Input tokens:* {ti:,}\n"
        f"📤 *Output tokens:* {to:,}\n"
        f"📊 *Tổng tokens:* {total:,}\n"
        f"🔄 *Số lần gọi API:* {calls}\n\n"
        f"{'━'*28}\n"
        f"🕐 *5 lần gần nhất:*\n{recent_lines}\n\n"
        f"💡 Groq free tier: 14,400 req/ngày\n"
        f"Model: `{GROQ_MODEL}`"
    )

# ============================================================
# STATS
# ============================================================
def stats_load()->dict:
    try:
        with open(STATS_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {"posts":[]}

def stats_save(d):
    try:
        with open(STATS_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    except: pass

def stats_add(title:str, url:str, wp_id:int, n_images:int):
    d=stats_load()
    d["posts"].append({"id":wp_id,"title":title,"url":url,"images":n_images,
        "date":date.today().isoformat(),"time":datetime.now().strftime("%H:%M"),
        "datetime":datetime.now().isoformat()})
    stats_save(d)

def stats_summary()->dict:
    d=stats_load(); posts=d.get("posts",[])
    today=date.today().isoformat()
    w7=(date.today()-timedelta(days=7)).isoformat()
    m30=(date.today()-timedelta(days=30)).isoformat()
    return {"total":len(posts),
            "today":[p for p in posts if p.get("date","")==today],
            "week":[p for p in posts if p.get("date","")>=w7],
            "month":[p for p in posts if p.get("date","")>=m30],
            "all":posts}

def stats_text()->str:
    s=stats_summary()
    tl=s["today"]
    today_lines="\n".join([f"  {i+1}. [{p['time']}] {p['title'][:45]}{'...' if len(p['title'])>45 else ''}" for i,p in enumerate(tl)]) if tl else "  (chưa có bài hôm nay)"
    recent=s["all"][-5:][::-1]
    recent_lines="\n".join([f"  • [{p['date']} {p['time']}] {p['title'][:40]}..." for p in recent]) if recent else "  (chưa có)"
    return (f"📊 *THỐNG KÊ BÀI ĐĂNG*\n{'━'*28}\n\n"
            f"📅 *Hôm nay ({date.today().strftime('%d/%m/%Y')}):* {len(tl)} bài\n{today_lines}\n\n"
            f"📆 *7 ngày:* {len(s['week'])} bài\n"
            f"🗓️ *30 ngày:* {len(s['month'])} bài\n"
            f"📈 *Tổng cộng:* {s['total']} bài\n\n"
            f"{'━'*28}\n🕐 *5 bài gần nhất:*\n{recent_lines}")

STATS_KW=["thống kê","tổng kết","bao nhiêu bài","mấy bài","đăng mấy","đăng bao nhiêu",
          "hôm nay đăng","tuần này","tháng này","bài đã đăng","lịch sử","báo cáo"]

def is_stats_q(txt:str)->bool: return any(k in txt.lower() for k in STATS_KW)

# ============================================================
# MEDIA GROUP
# ============================================================
def mg_load()->dict:
    try:
        with open(MG_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}
def mg_save(d):
    try:
        with open(MG_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False)
    except: pass
def mg_add(gid:str,fid:str,caption:str=""):
    d=mg_load()
    if gid not in d: d[gid]={"photos":[],"caption":""}
    if caption and not d[gid]["caption"]: d[gid]["caption"]=caption
    if fid not in d[gid]["photos"]: d[gid]["photos"].append(fid)
    mg_save(d)
def mg_get(gid:str)->dict|None: return mg_load().get(gid)
def mg_del(gid:str):
    d=mg_load(); d.pop(gid,None); mg_save(d)

# ============================================================
# PENDING
# ============================================================
def _load_p()->dict:
    try:
        with open(PENDING_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}
def _save_p(d):
    try:
        with open(PENDING_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    except Exception as e: logger.error(f"_save_p:{e}")

def pending_set(uid:int,val:dict):
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

def pending_upd(uid:int,seo:dict):
    d=_load_p()
    if str(uid) in d: d[str(uid)]["seo_data"]=seo; _save_p(d)
def pending_del(uid:int):
    d=_load_p(); d.pop(str(uid),None); _save_p(d)
def pending_has(uid:int)->bool: return str(uid) in _load_p()

# ============================================================
# HELPER
# ============================================================
def is_ok(uid:int)->bool: return True if not ALLOWED_USER_IDS else uid in ALLOWED_USER_IDS
def wp_auth()->dict:
    return {"Authorization":"Basic "+base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()}
def keyboard(uid:int)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Đăng WordPress",callback_data=f"publish_{uid}"),
         InlineKeyboardButton("❌ Huỷ",callback_data=f"cancel_{uid}")],
        [InlineKeyboardButton("🔄 Viết lại",callback_data=f"rewrite_{uid}")],
    ])

def preview_text(seo:dict,n_img:int=0)->str:
    from html.parser import HTMLParser
    class TX(HTMLParser):
        def __init__(self): super().__init__(); self.t=[]
        def handle_data(self,d): self.t.append(d)
    tx=TX(); tx.feed(seo.get("content_html","")); plain=" ".join(tx.t)[:500]
    img_note=(f"✅ {n_img} ảnh (thumbnail + {n_img-1} trong bài)" if n_img>1
              else ("✅ 1 ảnh thumbnail" if n_img==1 else "❌ Không có"))
    r=rules_load(); s=stats_summary()
    return (
        f"📝 *PREVIEW BÀI VIẾT*\n{'━'*28}\n\n"
        f"🏷️ *Tiêu đề:* {seo['seo_title']}\n\n"
        f"📊 *Từ khóa:* `{seo.get('focus_keyword','N/A')}`\n\n"
        f"📋 *Meta:* _{seo['meta_description']}_\n"
        f"📏 *Meta length:* {len(seo.get('meta_description',''))} ký tự\n\n"
        f"🏷️ *Tags:* {', '.join(seo.get('tags',[]))}\n\n"
        f"🖼️ *Ảnh:* {img_note}\n"
        f"📚 *{len(r)} quy tắc* + Yoast SEO 90+\n\n"
        f"{'━'*28}\n📖 *Nội dung:*\n{plain}...\n\n"
        f"{'━'*28}\n"
        f"💬 Nhắn yêu cầu chỉnh: _\"viết ngắn hơn\"_, _\"thêm emoji\"_\n\n"
        f"👇 Hoặc bấm nút:"
    )

# ============================================================
# SCRAPE URL - lấy nội dung + TẤT CẢ ẢNH (tối đa 10)
# ============================================================
def scrape_url(url:str)->dict:
    """Scrape nội dung và tất cả ảnh từ URL (Facebook, website bất kỳ)."""
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
             "Accept-Language":"vi-VN,vi;q=0.9,en;q=0.8"}
    try:
        r=requests.get(url,headers=headers,timeout=15)
        s=BeautifulSoup(r.text,"html.parser")

        # OG tags
        def og(p): t=s.find("meta",property=p); return t.get("content","") if t else ""
        title  = og("og:title")  or (s.find("h1") and s.find("h1").get_text(strip=True)) or ""
        desc   = og("og:description")
        og_img = og("og:image")

        # Lấy tất cả ảnh từ <img> tags trong bài (tối đa 10)
        all_images = []
        if og_img: all_images.append(og_img)

        for img_tag in s.find_all("img"):
            src=img_tag.get("src","") or img_tag.get("data-src","") or img_tag.get("data-lazy-src","")
            if not src: continue
            # Bỏ qua ảnh nhỏ (icon, avatar, tracking pixel)
            if any(x in src.lower() for x in ["icon","avatar","logo","pixel","tracking","1x1","emoji"]): continue
            # Chỉ lấy ảnh có đuôi hợp lệ hoặc không có đuôi (dynamic)
            if src.startswith("http") and src not in all_images:
                all_images.append(src)
            elif src.startswith("//"):
                full="https:"+src
                if full not in all_images: all_images.append(full)
            if len(all_images)>=10: break

        # Lấy text nội dung
        body=""
        # Thử lấy từ article/main trước
        main=s.find("article") or s.find("main") or s.find(class_=re.compile(r"content|post|entry|article"))
        if main:
            for t in main.find_all(["p","h2","h3"]):
                tx=t.get_text(" ",strip=True)
                if len(tx)>30: body+=tx+"\n\n"
                if len(body)>3000: break
        # Fallback
        if len(body)<100:
            for t in s.find_all(["p","div","span"]):
                tx=t.get_text(" ",strip=True)
                if len(tx)>100: body+=tx+"\n\n"
                if len(body)>3000: break

        return {
            "title":   title or "Bài viết",
            "content": desc or body[:3000] or "Không lấy được nội dung.",
            "images":  all_images[:10],  # tối đa 10 ảnh
            "source":  url,
        }
    except Exception as e:
        logger.error(f"scrape_url:{e}")
        return {"title":"Bài viết","content":"","images":[],"source":url}

def make_seo_filename(keyphrase:str, index:int, ext:str="jpg")->str:
    """
    Tạo tên file ảnh chuẩn SEO từ keyphrase.
    Ví dụ: keyphrase='học tiếng Anh trẻ em', index=1
    → 'hoc-tieng-anh-tre-em-nqh-english-1.jpg'
    """
    import unicodedata
    # Chuẩn hóa tiếng Việt → ASCII
    slug = keyphrase.lower().strip()
    # Bảng thay thế ký tự tiếng Việt
    vi_map = {
        'à':'a','á':'a','ả':'a','ã':'a','ạ':'a',
        'ă':'a','ắ':'a','ặ':'a','ằ':'a','ẳ':'a','ẵ':'a',
        'â':'a','ấ':'a','ậ':'a','ầ':'a','ẩ':'a','ẫ':'a',
        'è':'e','é':'e','ẻ':'e','ẽ':'e','ẹ':'e',
        'ê':'e','ế':'e','ệ':'e','ề':'e','ể':'e','ễ':'e',
        'ì':'i','í':'i','ỉ':'i','ĩ':'i','ị':'i',
        'ò':'o','ó':'o','ỏ':'o','õ':'o','ọ':'o',
        'ô':'o','ố':'o','ộ':'o','ồ':'o','ổ':'o','ỗ':'o',
        'ơ':'o','ớ':'o','ợ':'o','ờ':'o','ở':'o','ỡ':'o',
        'ù':'u','ú':'u','ủ':'u','ũ':'u','ụ':'u',
        'ư':'u','ứ':'u','ự':'u','ừ':'u','ử':'u','ữ':'u',
        'ỳ':'y','ý':'y','ỷ':'y','ỹ':'y','ỵ':'y',
        'đ':'d',
    }
    for vi, la in vi_map.items():
        slug = slug.replace(vi, la)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug).strip('-')
    slug = re.sub(r'-+', '-', slug)
    if len(slug) > 40: slug = slug[:40].rstrip('-')
    if not slug: slug = "nqh-english"
    return f"{slug}-nqh-english-{index}.{ext}"

def download_image(url:str)->tuple:
    """Tải ảnh từ URL, trả về (bytes, content_type, ext)."""
    try:
        r=requests.get(url,timeout=15,headers={"User-Agent":"Mozilla/5.0 Chrome/120"})
        if r.status_code!=200: return None,None,None
        ct=r.headers.get("Content-Type","image/jpeg").split(";")[0].strip()
        ext="jpg" if "jpeg" in ct else ("png" if "png" in ct else ("webp" if "webp" in ct else "jpg"))
        return r.content, ct, ext
    except: return None,None,None

def build_image_block(urls:list, keyphrase:str, start_index:int=1)->str:
    """
    Tạo HTML block ảnh với layout đẹp:
    - 1 ảnh  → full width figure
    - 2 ảnh  → 2 cột ngang (Style 2)
    - 3 ảnh  → 1 ảnh full + 2 cột
    - 4 ảnh  → 2×2 grid (Style 8)
    - 5-9    → gallery 3 cột
    """
    valid = [u for u in urls if u]
    if not valid: return ""
    n = len(valid)
    kw = keyphrase or "NQH English"

    def img_tag(url, idx, caption=""):
        alt = f"{kw} - NQH English {idx}"
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        return f'<img src="{url}" alt="{alt}" loading="lazy"/>{cap}'

    if n == 1:
        return (f'<figure class="wp-block-image size-large aligncenter">'
                f'{img_tag(valid[0], start_index)}</figure>')

    elif n == 2:
        # Style 2: 2 cột ngang, tỷ lệ 1:1
        cols = "".join([
            f'<figure class="wp-block-image">{img_tag(valid[i], start_index+i)}</figure>'
            for i in range(2)
        ])
        return (f'<div class="wp-block-columns is-layout-flex" '
                f'style="display:flex;gap:12px;margin:24px 0;">'
                f'<div class="wp-block-column" style="flex:1;">{cols.split("</figure>")[0]}</figure></div>'
                f'<div class="wp-block-column" style="flex:1;">{cols.split("</figure>")[1]}</figure></div>'
                f'</div>')

    elif n == 3:
        # 1 ảnh full + 2 cột
        full = (f'<figure class="wp-block-image size-large aligncenter">'
                f'{img_tag(valid[0], start_index)}</figure>')
        pair = build_image_block(valid[1:3], keyphrase, start_index+1)
        return full + pair

    elif n == 4:
        # Style 8: 2×2 grid
        rows = []
        for row in range(2):
            r_imgs = "".join([
                f'<div class="wp-block-column" style="flex:1;">'
                f'<figure class="wp-block-image">'
                f'{img_tag(valid[row*2+col], start_index+row*2+col)}'
                f'</figure></div>'
                for col in range(2)
            ])
            rows.append(
                f'<div class="wp-block-columns is-layout-flex" '
                f'style="display:flex;gap:12px;margin:8px 0;">{r_imgs}</div>'
            )
        return f'<div style="margin:24px 0;">{"".join(rows)}</div>'

    else:
        # 5+ ảnh: gallery 3 cột
        items = "".join([
            f'<li class="blocks-gallery-item">'
            f'<figure>{img_tag(valid[i], start_index+i)}</figure></li>'
            for i in range(n)
        ])
        return (f'<figure class="wp-block-gallery columns-3 is-cropped" style="margin:24px 0;">'
                f'<ul class="blocks-gallery-grid">{items}</ul></figure>')

# ============================================================
# GROQ AI
# ============================================================
def _clean_groq_json(t: str) -> str:
    """Làm sạch JSON từ Groq: xoá code block, tìm JSON object, fix control chars."""
    # 1. Xoá markdown code block
    t = re.sub(r"```json\s*", "", t)
    t = re.sub(r"```\s*", "", t)
    t = t.strip()

    # 2. Tìm JSON object đầu tiên (bỏ text thừa trước/sau)
    m = re.search(r'\{.*\}', t, re.DOTALL)
    if m:
        t = m.group(0)

    # 3. Fix control characters không hợp lệ trong JSON
    # Thay literal newline/tab/carriage return bên trong string JSON thành escaped version
    # Chỉ xử lý các char trong range 0x00-0x1f (trừ 0x20 trở lên là ok)
    def fix_ctrl(s: str) -> str:
        result = []
        in_string = False
        i = 0
        while i < len(s):
            c = s[i]
            if c == '\\' and in_string:
                # escape sequence — giữ nguyên 2 ký tự
                result.append(c)
                if i + 1 < len(s):
                    result.append(s[i+1])
                    i += 2
                else:
                    i += 1
                continue
            if c == '"':
                in_string = not in_string
                result.append(c)
            elif in_string and ord(c) < 0x20:
                # Control character trong string → escape nó
                esc = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}.get(c, f'\\u{ord(c):04x}')
                result.append(esc)
            else:
                result.append(c)
            i += 1
        return ''.join(result)

    return fix_ctrl(t)

def _groq(sys_p:str,usr_p:str)->dict:
    t = ""
    try:
        resp=groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"system","content":sys_p},{"role":"user","content":usr_p}],
            temperature=0.7,max_tokens=4000
        )
        # Track token usage
        usage=resp.usage
        if usage: token_add(usage.prompt_tokens, usage.completion_tokens)

        t=resp.choices[0].message.content.strip()
        t=_clean_groq_json(t)
        return json.loads(t)
    except json.JSONDecodeError as e:
        logger.error(f"JSON err:{e}\nRaw (200 chars): {t[:200]}")
        # Fallback: thử xoá toàn bộ control chars rồi parse lại
        try:
            t2 = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', t)
            m = re.search(r'\{.*\}', t2, re.DOTALL)
            if m:
                return json.loads(m.group(0))
        except Exception:
            pass
        return {"seo_title":"NQH English","meta_description":"Trung tâm tiếng Anh NQH English - nơi các bé học tiếng Anh hiệu quả và vui vẻ.","focus_keyword":"",
                "content_html":"<p>Lỗi tạo nội dung.</p>","tags":[],"category_suggestion":"Tin tức",
                "outbound_links":[],"internal_links":[]}
    except Exception as e:
        logger.error(f"Groq err:{e}"); raise

def _groq_chat(question:str)->str:
    """Hỏi đáp tự do, trả về text."""
    try:
        resp=groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role":"system","content":"Bạn là trợ lý thông minh của NQH English Bot. Trả lời bằng tiếng Việt, ngắn gọn, rõ ràng. Có thể trả lời mọi câu hỏi về SEO, WordPress, tiếng Anh, cách dùng bot, v.v."},
                {"role":"user","content":question}
            ],
            temperature=0.5,max_tokens=800
        )
        if resp.usage: token_add(resp.usage.prompt_tokens,resp.usage.completion_tokens)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"_groq_chat:{e}")
        return f"❌ Lỗi: {str(e)[:200]}"

def write_post(raw:str,n_inline:int=0,keyphrase:str="")->dict:
    img_ins=""
    if n_inline>0:
        # Hướng dẫn nhóm ảnh thành gallery 2 hoặc 4
        if n_inline >= 4:
            img_ins=(f"\nCHÈN ẢNH (có {n_inline} ảnh): Nhóm ảnh thành gallery trong bài:"
                     f"\n- Sau H2 đầu tiên: đặt {{{{IMAGE_1}}}}{{{{IMAGE_2}}}} liền nhau (sẽ thành gallery 2 ảnh)"
                     f"\n- Sau H2 thứ hai: đặt {{{{IMAGE_3}}}}{{{{IMAGE_4}}}} liền nhau (sẽ thành gallery 2×2)"
                     f"\n- Ảnh còn lại đặt lẻ sau các đoạn phù hợp"
                     f"\nChỉ đặt placeholder, KHÔNG thêm thẻ img hay figure.\n")
        elif n_inline >= 2:
            img_ins=(f"\nCHÈN ẢNH (có {n_inline} ảnh): Đặt {{{{IMAGE_1}}}}{{{{IMAGE_2}}}} liền nhau"
                     f" sau đoạn H2 đầu tiên (sẽ tự động tạo gallery 2 cột)."
                     f"\nChỉ đặt placeholder, KHÔNG thêm thẻ img hay figure.\n")
        else:
            img_ins=(f"\nCHÈN ẢNH: Đặt {{{{IMAGE_1}}}} sau đoạn H2 phù hợp nhất."
                     f"\nChỉ đặt placeholder, KHÔNG thêm thẻ img hay figure.\n")
    kp_ins=f"\nFOCUS KEYPHRASE GỢI Ý: \"{keyphrase}\" - dùng keyphrase này xuyên suốt bài.\n" if keyphrase else ""

    return _groq(
        "Bạn là SEO Content Writer chuyên nghiệp cho NQH English. "
        "Viết bài đạt Yoast SEO 90+ điểm. Tone thân thiện, vui tươi. JSON thuần túy.",

        f"Viết bài blog chuẩn Yoast SEO 90+ cho NQH English:\n\nNỘI DUNG GỐC:\n{raw}\n\n"
        f"{YOAST_SEO_PROMPT}{rules_prompt()}{img_ins}{kp_ins}\n"
        f"BẮT BUỘC trong content_html:\n"
        f"- Câu ngắn (tối đa 25% câu > 20 từ)\n"
        f"- Dùng transition words (đầu tiên, tiếp theo, bên cạnh đó, do đó, tóm lại...)\n"
        f"- H2 mỗi 300 từ, đoạn <p> ngắn\n"
        f"- 1-2 external link thực tế đến nguồn uy tín (British Council, Cambridge, báo VN)\n"
        f"- 1-2 internal link dạng: <a href='/bai-viet-lien-quan'>anchor text</a>\n"
        f"- Tối thiểu 800 từ\n\n"
        f"JSON (không thêm gì khác):\n"
        f"{{\"seo_title\":\"...\",\"meta_description\":\"... (120-156 ký tự)\","
        f"\"focus_keyword\":\"...\",\"content_html\":\"...\","
        f"\"tags\":[\"tag1\",\"tag2\",\"tag3\"],\"category_suggestion\":\"...\"}}"
    )

def refine_post(html:str,req:str,seo:dict)->dict:
    return _groq(
        "Bạn là SEO Content Writer cho NQH English. Thực hiện đúng yêu cầu, giữ placeholder {{IMAGE_N}}, duy trì chuẩn Yoast SEO 90+. JSON thuần túy.",
        f"BÀI HIỆN TẠI:\nTiêu đề: {seo.get('seo_title','')}\nHTML: {html}\n\n"
        f"YÊU CẦU: \"{req}\"\n\n{YOAST_SEO_PROMPT}{rules_prompt()}\n"
        f"JSON:\n{{\"seo_title\":\"...\",\"meta_description\":\"...\","
        f"\"focus_keyword\":\"...\",\"content_html\":\"...\","
        f"\"tags\":[\"tag1\",\"tag2\"],\"category_suggestion\":\"...\"}}"
    )

# ============================================================
# WORDPRESS
# ============================================================
def wp_upload(img_bytes:bytes, fname:str="image.jpg", ct:str="image/jpeg")->tuple:
    """Upload ảnh lên WordPress với tên file SEO chuẩn."""
    try:
        h={**wp_auth(),"Content-Disposition":f'attachment; filename="{fname}"',"Content-Type":ct}
        r=requests.post(f"{WP_URL}/wp-json/wp/v2/media",headers=h,data=img_bytes,timeout=30)
        if r.status_code in (200,201):
            d=r.json()
            # Cập nhật alt text và title cho media
            media_id=d.get("id")
            return media_id, d.get("source_url","")
    except Exception as e: logger.error(f"wp_upload:{e}")
    return None,None

def wp_post(seo:dict, thumb_id:int|None, inline_data:list)->dict:
    """
    inline_data: list of (url, group_info)
    group_info = {"urls": [...], "start_idx": N} để nhóm ảnh vào gallery
    """
    h={**wp_auth(),"Content-Type":"application/json"}
    html=seo["content_html"]
    kw=seo.get("focus_keyword","NQH English")

    # Xử lý từng nhóm ảnh theo placeholder
    # inline_data là list url đơn giản, ta nhóm liên tiếp để tạo gallery
    # Chiến lược: nhóm ảnh theo cặp placeholder liên tiếp
    if inline_data:
        # Tìm tất cả placeholder trong html
        placeholders = re.findall(r'\{\{IMAGE_(\d+)\}\}', html)

        # Nhóm các placeholder liên tiếp thành gallery
        groups = []
        current_group = []
        prev_idx = -1

        for ph_str in placeholders:
            idx = int(ph_str)
            if prev_idx == -1 or idx == prev_idx + 1:
                current_group.append(idx)
            else:
                if current_group: groups.append(current_group)
                current_group = [idx]
            prev_idx = idx
        if current_group: groups.append(current_group)

        # Thay từng nhóm bằng gallery block
        for group in groups:
            # Lấy URLs của nhóm này
            group_urls = []
            for idx in group:
                url_idx = idx - 1
                if 0 <= url_idx < len(inline_data):
                    group_urls.append(inline_data[url_idx])

            if not group_urls: continue

            gallery_html = build_image_block(group_urls, kw, start_index=group[0])

            # Tìm đoạn placeholder liên tiếp đầu tiên để thay
            if len(group) == 1:
                html = html.replace(f"{{{{IMAGE_{group[0]}}}}}", gallery_html)
            else:
                # Thay placeholder đầu nhóm bằng gallery, xoá các placeholder còn lại
                first_ph = f"{{{{IMAGE_{group[0]}}}}}"
                html = html.replace(first_ph, gallery_html)
                for idx in group[1:]:
                    html = html.replace(f"{{{{IMAGE_{idx}}}}}", "")

    # Xoá placeholder thừa
    html = re.sub(r"\{\{IMAGE_\d+\}\}", "", html)
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
# XỬ LÝ ẢNH
# ============================================================
async def process_photos(bot,chat_id:int,user_id:int,file_ids:list,caption:str):
    msg=await bot.send_message(chat_id,f"📸 *{len(file_ids)} ảnh*. Đang tải...",parse_mode="Markdown")
    try:
        images_bytes=[]
        for fid in file_ids:
            pf=await bot.get_file(fid); buf=io.BytesIO()
            await pf.download_to_memory(buf); images_bytes.append(buf.getvalue())
        raw_text=caption.strip() if len(caption.strip())>=10 else "Hoạt động mới tại NQH English"
        n_inline=len(images_bytes)-1; r=rules_load()
        await bot.edit_message_text(chat_id=chat_id,message_id=msg.message_id,
            text=f"⚡ Groq AI viết bài Yoast 90+ ({len(images_bytes)} ảnh)...\n📚 {len(r)} quy tắc")
        seo=write_post(raw_text,n_inline=n_inline)
        pending_set(user_id,{"seo_data":seo,"images_bytes":images_bytes,"image_url":"","source":f"{len(images_bytes)} ảnh"})
        await bot.edit_message_text(chat_id=chat_id,message_id=msg.message_id,
            text=preview_text(seo,n_img=len(images_bytes)),parse_mode="Markdown",reply_markup=keyboard(user_id))
    except Exception as e:
        logger.error(f"process_photos:{e}",exc_info=True)
        await bot.edit_message_text(chat_id=chat_id,message_id=msg.message_id,
            text=f"❌ Lỗi:\n`{str(e)[:300]}`",parse_mode="Markdown")

# ============================================================
# HANDLERS
# ============================================================
async def handle_photo(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not is_ok(update.effective_user.id): return
    uid=update.effective_user.id; msg=update.message
    gid=msg.media_group_id; caption=msg.caption or ""
    fid=msg.photo[-1].file_id; chat_id=msg.chat_id
    if gid:
        mg_add(str(gid),fid,caption)
        old=_mg_tasks.get(gid)
        if old and not old.done(): old.cancel()
        async def _d():
            await asyncio.sleep(2.5)
            info=mg_get(str(gid)); mg_del(str(gid)); _mg_tasks.pop(gid,None)
            if info: await process_photos(context.bot,chat_id,uid,info["photos"],info.get("caption",""))
        _mg_tasks[gid]=asyncio.create_task(_d())
    else:
        await process_photos(context.bot,chat_id,uid,[fid],caption)

async def handle_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not is_ok(update.effective_user.id): return
    txt=update.message.text.strip(); uid=update.effective_user.id

    # Thống kê?
    if is_stats_q(txt):
        await update.message.reply_text(stats_text(),parse_mode="Markdown"); return

    # Đang có pending → chỉnh sửa
    if pending_has(uid):
        pi=pending_get(uid)
        is_fb=bool(re.match(r"https?://(www\.)?(facebook\.com|fb\.com|fb\.watch)/",txt))
        is_url=bool(re.match(r"https?://",txt))
        is_new=is_fb or is_url or len(txt)>200
        if not is_new:
            m=await update.message.reply_text(f"✏️ Đang chỉnh: _\"{txt[:80]}\"_...",parse_mode="Markdown")
            try:
                cur=pi["seo_data"]
                new=refine_post(cur.get("content_html",""),txt,cur)
                pending_upd(uid,new); ni=len(pi.get("images_bytes") or [])
                await m.edit_text(preview_text(new,n_img=ni),parse_mode="Markdown",reply_markup=keyboard(uid))
            except Exception as e:
                await m.edit_text(f"❌ Lỗi:`{str(e)[:200]}`",parse_mode="Markdown")
            return
        pending_del(uid)

    # URL (Facebook hoặc bất kỳ)
    is_url=bool(re.match(r"https?://",txt))
    if is_url:
        m=await update.message.reply_text("🔍 Đang scrape nội dung và tất cả ảnh từ link...")
        try:
            sc=scrape_url(txt)
            raw=f"{sc['title']}\n\n{sc['content']}"
            scraped_imgs=sc.get("images",[])

            if len(raw.strip())<50:
                await m.edit_text("⚠️ Không lấy được nội dung.\n• Paste text trực tiếp\n• Hoặc gửi ảnh kèm caption")
                return

            img_count=len(scraped_imgs)
            r=rules_load()
            await m.edit_text(
                f"⚡ Groq AI viết bài Yoast 90+...\n"
                f"🖼️ Tìm thấy {img_count} ảnh từ link\n"
                f"📚 {len(r)} quy tắc"
            )

            n_inline=max(0,min(img_count,9)-1) if img_count>0 else 0
            seo=write_post(raw,n_inline=n_inline)

            pending_set(uid,{
                "seo_data":seo,
                "images_bytes":[],         # sẽ tải khi publish
                "scraped_image_urls":scraped_imgs,  # ảnh từ scrape
                "image_url":"",
                "source":txt,
            })

            ni=img_count
            await m.edit_text(preview_text(seo,n_img=ni),parse_mode="Markdown",reply_markup=keyboard(uid))
        except Exception as e:
            logger.error(f"url handler:{e}",exc_info=True)
            await m.edit_text(f"❌ Lỗi:`{str(e)[:200]}`",parse_mode="Markdown")
        return

    # Text thuần
    m=await update.message.reply_text("⏳ Đang xử lý...")
    try:
        if len(txt.strip())<50:
            await m.edit_text("⚠️ Nội dung quá ngắn.\n• Paste text trực tiếp\n• Gửi ảnh kèm caption"); return
        r=rules_load()
        await m.edit_text(f"⚡ Groq AI viết bài Yoast 90+...\n📚 {len(r)} quy tắc")
        seo=write_post(txt)
        pending_set(uid,{"seo_data":seo,"images_bytes":[],"scraped_image_urls":[],"image_url":"","source":"Text"})
        await m.edit_text(preview_text(seo,n_img=0),parse_mode="Markdown",reply_markup=keyboard(uid))
    except Exception as e:
        await m.edit_text(f"❌ Lỗi:`{str(e)[:200]}`",parse_mode="Markdown")

async def handle_cb(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=update.effective_user.id; await q.answer()
    d=q.data

    if d.startswith("cancel_"):
        pending_del(uid); await q.edit_message_text("❌ Đã huỷ.")

    elif d.startswith("rewrite_"):
        pi=pending_get(uid)
        if not pi: await q.edit_message_text("⚠️ Không tìm thấy. Gửi lại."); return
        await q.edit_message_text("🔄 Viết lại Yoast 90+...")
        try:
            ni=max(0,len(pi.get("images_bytes") or [])-1)
            sc_imgs=pi.get("scraped_image_urls",[])
            if sc_imgs: ni=max(0,min(len(sc_imgs),9)-1)
            raw=f"[Viết lại phong cách khác]\n{pi['seo_data']['content_html']}"
            seo=write_post(raw,n_inline=ni)
            pending_upd(uid,seo)
            nim=len(pi.get("images_bytes") or []) or len(sc_imgs)
            await q.edit_message_text(preview_text(seo,n_img=nim),parse_mode="Markdown",reply_markup=keyboard(uid))
        except Exception as e:
            await q.edit_message_text(f"❌ Lỗi:{e}")

    elif d.startswith("publish_"):
        pi=pending_get(uid)
        if not pi: await q.edit_message_text("⚠️ Không tìm thấy. Gửi lại."); return
        await q.edit_message_text("📤 Đang đăng bài...")
        try:
            imgs_bytes   = pi.get("images_bytes") or []
            sc_urls      = pi.get("scraped_image_urls") or []
            thumb_id     = None
            inline_urls  = []   # list URL cho ảnh trong bài (index 2+)
            total_img    = 0
            kw           = pi["seo_data"].get("focus_keyword","nqh-english")

            # ── Ảnh từ Telegram ──
            if imgs_bytes:
                total=len(imgs_bytes)
                for i,ib in enumerate(imgs_bytes):
                    if not ib: continue
                    await q.edit_message_text(f"🖼️ Upload ảnh Telegram {i+1}/{total}...")
                    fname=make_seo_filename(kw, i+1, "jpg")
                    mid,murl=wp_upload(ib, fname, "image/jpeg")
                    if i==0: thumb_id=mid
                    else:
                        if murl: inline_urls.append(murl)
                total_img=total

            # ── Ảnh từ scrape URL (tối đa 10) ──
            elif sc_urls:
                total=min(len(sc_urls),10)
                await q.edit_message_text(f"🖼️ Đang tải và upload {total} ảnh từ link...")
                for i,img_url in enumerate(sc_urls[:10]):
                    await q.edit_message_text(f"🖼️ Upload ảnh {i+1}/{total}...")
                    img_b,ct,ext=download_image(img_url)
                    if not img_b: continue
                    fname=make_seo_filename(kw, i+1, ext or "jpg")
                    mid,murl=wp_upload(img_b, fname, ct or "image/jpeg")
                    if i==0: thumb_id=mid
                    else:
                        if murl: inline_urls.append(murl)
                    total_img+=1

            await q.edit_message_text("📝 Đang đăng bài WordPress...")
            res=wp_post(pi["seo_data"], thumb_id, inline_urls)
            post_url=res.get("link",""); post_id=res.get("id",0)
            edit_url=f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"
            title=pi["seo_data"]["seo_title"]

            stats_add(title,post_url,post_id,total_img)
            s=stats_summary(); pending_del(uid)

            await q.edit_message_text(
                f"🎉 *ĐĂNG BÀI THÀNH CÔNG!*\n{'━'*28}\n\n"
                f"📌 *Tiêu đề:*\n{title}\n\n"
                f"🖼️ *Ảnh:* {'✅ '+str(total_img)+' ảnh' if total_img else '❌ Không có'}\n"
                f"🔗 [Xem bài]({post_url})\n✏️ [WP Admin]({edit_url})\n\n"
                f"{'━'*28}\n📊 *Hôm nay:* {len(s['today'])} bài | *Tổng:* {s['total']} bài\n\n"
                f"⚡ Groq `{GROQ_MODEL}` | Yoast SEO 90+",
                parse_mode="Markdown",disable_web_page_preview=False
            )
        except Exception as e:
            logger.error(f"publish:{e}",exc_info=True)
            await q.edit_message_text(f"❌ Lỗi:\n`{str(e)[:300]}`",parse_mode="Markdown")

# ============================================================
# LỆNH
# ============================================================
async def cmd_start(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    r=rules_load(); s=stats_summary()
    await u.message.reply_text(
        "👋 *NQH English Bot v9.0*\n\n"
        "📸 *Gửi ảnh:* 1 ảnh hoặc album nhiều ảnh\n"
        "🔗 *Gửi link:* Facebook/website → scrape nội dung + ảnh tự động\n"
        "📝 *Gửi text:* viết bài từ nội dung\n\n"
        "✏️ Sau preview: nhắn yêu cầu chỉnh trực tiếp\n\n"
        f"📊 Hôm nay: *{len(s['today'])} bài* | Tổng: *{s['total']} bài*\n\n"
        "⌨️ Gõ / để xem tất cả lệnh",
        parse_mode="Markdown"
    )

async def cmd_help(u:Update,c:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📚 *Tất cả lệnh:*\n\n"
        "*/start* — Trang chủ\n"
        "*/help* — Hướng dẫn này\n"
        "*/status* — Kiểm tra WP\n"
        "*/model* — Model Groq\n"
        "*/cancel* — Huỷ bài đang soạn\n\n"
        "*📊 Thống kê:*\n"
        "*/stats* — Thống kê bài đăng\n"
        "*/token* — Thống kê token Groq\n\n"
        "*🧠 Dạy bot:*\n"
        "*/teach* [quy tắc] — Dạy bot nhớ mãi\n"
        "*/rules* — Xem quy tắc đã dạy\n"
        "*/delrule* [id] — Xoá quy tắc\n\n"
        "*💬 Hỏi đáp:*\n"
        "*/ask* [câu hỏi] — Hỏi AI bất kỳ điều gì\n\n"
        "*Ví dụ /ask:*\n"
        "`/ask Yoast SEO 90 điểm cần làm gì?`\n"
        "`/ask Cách tạo internal link hiệu quả?`\n"
        "`/ask Bot này hoạt động như thế nào?`",
        parse_mode="Markdown"
    )

async def cmd_ask(u:Update,c:ContextTypes.DEFAULT_TYPE):
    """Hỏi đáp AI bất kỳ câu hỏi nào."""
    if not is_ok(u.effective_user.id): return
    question=" ".join(c.args).strip()
    if not question:
        await u.message.reply_text(
            "💬 *Cú pháp:* `/ask [câu hỏi]`\n\n"
            "*Ví dụ:*\n"
            "`/ask Yoast SEO 90 điểm cần làm gì?`\n"
            "`/ask Cách viết meta description chuẩn?`\n"
            "`/ask Keyphrase density là gì?`\n"
            "`/ask Bot này có thể làm gì?`\n"
            "`/ask Tại sao cần internal link?`",
            parse_mode="Markdown"
        )
        return
    m=await u.message.reply_text("🤔 Đang tìm câu trả lời...")
    ans=_groq_chat(question)
    await m.edit_text(f"💬 *Hỏi:* _{question}_\n\n{ans}",parse_mode="Markdown")

async def cmd_token(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text(token_text(),parse_mode="Markdown")

async def cmd_stats(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text(stats_text(),parse_mode="Markdown")

async def cmd_teach(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    rule=" ".join(c.args).strip()
    if not rule:
        await u.message.reply_text("⚠️ `/teach [quy tắc]`\nVí dụ: `/teach mở đầu bằng câu hỏi`",parse_mode="Markdown"); return
    rid=rules_add(rule); r=rules_load()
    await u.message.reply_text(f"✅ *Đã dạy bot!*\n🆔 `{rid}`\n📝 _{rule}_\n\n📚 Tổng *{len(r)} quy tắc*",parse_mode="Markdown")

async def cmd_rules(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    r=rules_load()
    if not r: await u.message.reply_text("📭 Chưa có quy tắc.\n`/teach [quy tắc]`",parse_mode="Markdown"); return
    lines="\n".join([f"*[{x['id']}]* _{x['rule']}_" for x in r])
    await u.message.reply_text(f"📚 *{len(r)} quy tắc:*\n\n{lines}\n\n➕ `/teach` 🗑️ `/delrule [id]`",parse_mode="Markdown")

async def cmd_delrule(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    if not c.args: await u.message.reply_text("⚠️ `/delrule [id]`",parse_mode="Markdown"); return
    try: rid=int(c.args[0])
    except: await u.message.reply_text("⚠️ ID phải là số.",parse_mode="Markdown"); return
    if rules_delete(rid): await u.message.reply_text(f"🗑️ Xoá `{rid}`. Còn *{len(rules_load())}* quy tắc.",parse_mode="Markdown")
    else: await u.message.reply_text(f"⚠️ Không tìm ID `{rid}`.",parse_mode="Markdown")

async def cmd_model(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text(
        f"🤖 *Model:* `{GROQ_MODEL}`\n\n• `llama-3.1-8b-instant` — Siêu nhanh\n"
        f"• `llama-3.3-70b-versatile` — Chất lượng ⭐\n• `gemma2-9b-it` — Gemma 2\n\n"
        f"Đổi qua `GROQ_MODEL` trên Railway.",parse_mode="Markdown")

async def cmd_status(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not is_ok(u.effective_user.id): return
    await u.message.reply_text("🔍 Kiểm tra...")
    try:
        r=requests.get(f"{WP_URL}/wp-json/wp/v2/posts?per_page=1",headers=wp_auth(),timeout=10)
        if r.status_code==200:
            s=stats_summary()
            await u.message.reply_text(
                f"✅ *WordPress OK!*\n🌐 `{WP_URL}`\n🤖 `{GROQ_MODEL}`\n"
                f"📚 *{len(rules_load())}* quy tắc\n📊 *{s['total']}* bài đã đăng",parse_mode="Markdown")
        else: await u.message.reply_text(f"❌ HTTP {r.status_code}")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_cancel(u:Update,c:ContextTypes.DEFAULT_TYPE):
    uid=u.effective_user.id
    if pending_has(uid): pending_del(uid); await u.message.reply_text("❌ Đã huỷ. Gửi nội dung mới.")
    else: await u.message.reply_text("ℹ️ Không có bài đang soạn.")

# ============================================================
# SETUP BOT COMMANDS MENU (hiện khi gõ /)
# ============================================================
async def post_init(app:Application):
    """Thiết lập menu lệnh hiển thị khi gõ / trong Telegram."""
    commands=[
        BotCommand("start",     "🏠 Trang chủ & hướng dẫn"),
        BotCommand("help",      "📚 Xem tất cả lệnh"),
        BotCommand("ask",       "💬 Hỏi AI bất kỳ câu hỏi"),
        BotCommand("stats",     "📊 Thống kê bài đã đăng"),
        BotCommand("token",     "🔢 Kiểm tra token Groq"),
        BotCommand("status",    "✅ Kiểm tra kết nối WordPress"),
        BotCommand("teach",     "🧠 Dạy bot quy tắc mới"),
        BotCommand("rules",     "📋 Xem quy tắc đã dạy"),
        BotCommand("delrule",   "🗑️ Xoá quy tắc"),
        BotCommand("model",     "🤖 Xem model Groq đang dùng"),
        BotCommand("cancel",    "❌ Huỷ bài đang soạn"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("✅ Đã thiết lập Bot Commands Menu")

# ============================================================
# MAIN
# ============================================================
def main():
    app=(Application.builder()
         .token(TELEGRAM_TOKEN)
         .post_init(post_init)
         .build())

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("ask",     cmd_ask))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("token",   cmd_token))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("model",   cmd_model))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(CommandHandler("teach",   cmd_teach))
    app.add_handler(CommandHandler("rules",   cmd_rules))
    app.add_handler(CommandHandler("delrule", cmd_delrule))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_cb))

    logger.info(f"🤖 NQH English Bot v9.0 | {GROQ_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
